import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

from pc_client.app import Window
from pc_client.connection import Client, DeviceError
from pc_client.diagnostics import ACTION, InvalidDiagnosticReply, read_snapshot, serialize
from pc_client.protocol import decode, encode


def info(oldest=1, until=4, boot_id=2026):
    return {"data": {"schema": 1, "boot_id": boot_id, "oldest": oldest, "next": until, "capacity": 64}}


def page(next_cursor, events, lost=0):
    return {"data": {"events": events, "next": next_cursor, "lost": lost}}


class DiagnosticProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_snapshot_with_missing_rows_and_unknown_event(self):
        transport = AsyncMock(side_effect=[
            info(3, 7), page(5, [[4, 0xFFFFFFFF, 5, 32, 531]], 1),
            page(7, [[5, 1, 6, 6, -1], [6, 2, 55, -2147483648, 2147483647]]),
        ])
        log = await read_snapshot(transport)
        self.assertEqual(transport.await_args_list, [
            call("diag_info", {}), call("diag_read", {"boot_id": 2026, "from": 3, "until": 7}),
            call("diag_read", {"boot_id": 2026, "from": 5, "until": 7}),
        ])
        self.assertEqual(log["overwritten_before_read"], 2)
        self.assertEqual(log["overwritten_during_read"], 1)
        self.assertEqual([e["seq"] for e in log["events"]], [4, 5, 6])
        self.assertEqual(log["events"][0]["gap_reason"], 531)
        self.assertEqual(log["events"][1]["cause_name"], "indication_timeout")
        unknown = log["events"][2]
        self.assertEqual((unknown["event"], unknown["code"], unknown["a"]), ("unknown", 55, -2147483648))
        self.assertEqual(json.loads(serialize(log, "1.2.4", "1.0.6"))["client"], "1.0.6")

    async def test_empty_and_fully_overwritten_snapshots(self):
        worker = AsyncMock(return_value=info(1, 1))
        log = await read_snapshot(worker)
        self.assertEqual(log["events"], [])
        worker.assert_awaited_once_with("diag_info", {})
        log = await read_snapshot(AsyncMock(side_effect=[info(), page(4, [], 3)]))
        self.assertEqual(log["events"], [])
        self.assertEqual(log["overwritten_during_read"], 3)

    async def test_invalid_snapshot_arguments_and_nonmonotonic_or_malformed_pages(self):
        for bad_info in (info(1, 66), info(2, 1), info(1, 4, True), {}, {"data": []}):
            with self.subTest(bad_info=bad_info), self.assertRaises(InvalidDiagnosticReply):
                await read_snapshot(AsyncMock(return_value=bad_info))
        for bad_page in (
            page(1, []), page(5, []), page(2, []), page(2, [], True),
            page(3, [[1, 1, 1, 0, 0], [1, 2, 2, 1, 0]]),
            page(2, [[1, True, 1, 0, 0]]), page(2, [[1, 1, 0, 0, 0]]),
            page(2, [[1, 1, 1, 0, 1 << 31]]),
        ):
            with self.subTest(bad_page=bad_page), self.assertRaises(InvalidDiagnosticReply):
                await read_snapshot(AsyncMock(side_effect=[info(), bad_page]))
        with self.assertRaises(InvalidDiagnosticReply):
            await read_snapshot(AsyncMock(side_effect=[
                info(1, 11), page(11, [[seq, 0, 1, 0, 0] for seq in range(1, 11)])
            ]))


class DiagnosticWorkerTest(unittest.IsolatedAsyncioTestCase):
    def client(self):
        client = Client(Mock())
        client.link = SimpleNamespace(name="usb")
        client.ready = True
        client.info = {"diag_log": True, "firmware": "1.2.4"}
        client.call = AsyncMock(return_value=info(1, 1))
        return client

    async def test_blocks_ble_old_firmware_and_overlapping_upgrade(self):
        client = self.client()
        for attr, value, code in [
            ("link", SimpleNamespace(name="ble"), "DIAG_USB"),
            ("info", {"diag_log": 1}, "DIAG_UNSUPPORTED"), ("updating", True, "BUSY"),
        ]:
            with self.subTest(code=code), patch.object(client, attr, value):
                with self.assertRaisesRegex(DeviceError, code):
                    await client.read_diagnostics()
        client.call.assert_not_awaited()
        client.diagnosing = True
        with self.assertRaisesRegex(DeviceError, "BUSY"):
            await client.update_firmware(None, None)

    async def test_retries_transient_busy_and_releases_guard(self):
        client = self.client()
        client.call.side_effect = [DeviceError("DIAG_BUSY"), info(1, 2), page(2, [[1, 1, 1, 0, 0]])]
        with patch("pc_client.connection.asyncio.sleep", new_callable=AsyncMock) as sleep:
            log, firmware = await client.read_diagnostics()
        self.assertEqual(firmware, "1.2.4")
        self.assertEqual(log["events"][0]["event"], "boot")
        self.assertEqual(sleep.await_count, 3)
        self.assertFalse(client.diagnosing)

    async def test_existing_usb_request_dispatch_handles_telemetry_and_diag_frames(self):
        client = self.client()
        del client.call  # Exercise the production dispatch, not the mocked call.
        requests = []

        async def send(frame):
            request = decode(frame)
            requests.append(request["op"])
            payload = (
                info(1, 3)["data"] if request["op"] == "diag_info"
                else page(3, [[1, 1, 1, 0, 0], [2, 5, 10, 6, 99]])["data"]
            )
            client.receive(encode({"v": 1, "type": "state", "uptime_ms": 6,
                                   "applied": {"enabled": True}}))
            client.receive(encode({"v": 1, "type": "reply", "ok": True,
                                   "id": request["id"], "data": payload}))

        client.link.send = send
        client.link.timeout = 1
        snapshot, _ = await client.read_diagnostics()
        self.assertEqual(requests, ["diag_info", "diag_read"])
        self.assertTrue(client.latest["applied"]["enabled"])
        self.assertEqual(snapshot["events"][-1]["generation"], 99)
        self.assertEqual(snapshot["events"][-1]["cause_name"], "indication_timeout")

    async def test_boot_change_and_link_change_abort_without_partial_export(self):
        client = self.client()
        client.call.side_effect = [info(), DeviceError("DIAG_RESTARTED")]
        with patch("pc_client.connection.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaisesRegex(DeviceError, "DIAG_RESTARTED"):
                await client.read_diagnostics()
            self.assertFalse(client.diagnosing)

            async def replaced(*_):
                client.link = SimpleNamespace(name="usb")
                return info()

            client.call.side_effect = replaced
            with self.assertRaisesRegex(DeviceError, "LINK_ERROR"):
                await client.read_diagnostics()
            self.assertFalse(client.diagnosing)


class DiagnosticUITest(unittest.TestCase):
    def test_disabled_on_ble_ota_and_old_firmware_but_not_when_output_is_on(self):
        view = SimpleNamespace(kind="usb", info={"diag_log": True}, jobs=set(),
                               pc_handoff=False, exit_requested=False,
                               state={"applied": {"enabled": True}})
        self.assertEqual(Window.diagnostic_status(view), "")
        for attr, val in [("kind", "ble"), ("jobs", {"固件升级"}), ("info", {"firmware": "1.2.3"})]:
            with patch.object(view, attr, val):
                self.assertTrue(Window.diagnostic_status(view))

    def test_user_cancel_does_not_submit_and_success_writes_local_json(self):
        view = SimpleNamespace(kind="usb", info={"diag_log": True, "device_id": "PM-demo"},
                               jobs=set(), pc_handoff=False, exit_requested=False,
                               launch=Mock(), announce=Mock(),
                               worker=SimpleNamespace(client=SimpleNamespace(read_diagnostics=Mock())))
        view.diagnostic_status = lambda: Window.diagnostic_status(view)
        with patch("pc_client.app.QFileDialog.getSaveFileName", return_value=("", "")):
            Window.export_diagnostics(view)
        view.launch.assert_not_called()
        with TemporaryDirectory() as temp:
            path = str(Path(temp) / "test.json")
            with patch("pc_client.app.QFileDialog.getSaveFileName", return_value=(path, "")):
                Window.export_diagnostics(view)
            view.launch.assert_called_once_with(ACTION, view.worker.client.read_diagnostics.return_value)
            snapshot = {"events": [], "overwritten_before_read": 0, "overwritten_during_read": 0}
            Window.save_diagnostics(view, (snapshot, "1.2.4"))
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual((payload["firmware"], payload["client"]), ("1.2.4", "1.0.6"))
            self.assertNotIn("device_id", payload)
