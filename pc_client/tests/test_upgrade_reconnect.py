import unittest
from unittest.mock import AsyncMock, Mock, patch

from pc_client.connection import Client, DeviceError


class UpgradeReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def connect_expected(self, mode, reported_id):
        worker = Mock()
        client = Client(worker)
        old_expected = "PM-0123456789AB"
        link = Mock()
        link.close = AsyncMock()
        target = "COM4" if mode == "usb" else "12:34:56:78:9A:BC"

        async def identify(candidate):
            client.link = candidate
            return dict(v=1, device_id=reported_id, firmware="1.2.2", direct_control=True)

        async def call(operation):
            if operation != "get_state":
                self.fail(f"unexpected command: {operation}")
            client.latest = dict(applied=dict(enabled=True, relay1=True, relay2=False))
            return client.latest

        client.identify = AsyncMock(side_effect=identify)
        client.call = AsyncMock(side_effect=call)
        with patch("pc_client.connection.SerialLink" if mode == "usb" else "pc_client.connection.BleLink",
                   return_value=link) as factory:
            if reported_id != old_expected:
                with self.assertRaisesRegex(DeviceError, "OTA_DEVICE"):
                    await client.connect(mode, target, expected_device=old_expected)
                client.call.assert_not_awaited()
                worker.connected.emit.assert_not_called()
                link.close.assert_awaited_once()
            else:
                await client.connect(mode, target, expected_device=old_expected)
                client.call.assert_awaited_once_with("get_state")
                worker.connected.emit.assert_called_once()
                self.assertTrue(client.latest["applied"]["enabled"])
                self.assertIsNone(client.heartbeat)
                await client.close(graceful=False)
            factory.assert_called_once_with(target)

    async def test_usb_and_ble_same_device_read_only(self):
        for mode in ("usb", "ble"):
            with self.subTest(mode=mode):
                await self.connect_expected(mode, "PM-0123456789AB")

    async def test_usb_and_ble_wrong_device_never_write_or_enable_controls(self):
        for mode in ("usb", "ble"):
            with self.subTest(mode=mode):
                await self.connect_expected(mode, "PM-other")


class UpgradeSafetyTests(unittest.TestCase):
    def test_legacy_disconnect_requires_fresh_disabled_outputs_and_relays(self):
        from time import monotonic
        from types import SimpleNamespace
        from pc_client.app import Window

        state = dict(measured=dict(fresh=True), applied=dict(enabled=False, relay1=False, relay2=False))
        w = SimpleNamespace(info=dict(firmware="1.2.0"), state=state, last_receipt=monotonic(), jobs=set(),
                            exit_requested=False, pc_handoff=False, connect_target="COM4")
        self.assertEqual(Window.pc_upgrade_blocker(w), "")
        w.state["applied"]["relay1"] = True
        self.assertIn("旧固件", Window.pc_upgrade_blocker(w))
        w.state["applied"]["relay1"] = False
        w.last_receipt -= 10
        self.assertIn("旧固件", Window.pc_upgrade_blocker(w))
        w.info["firmware"] = "1.2.2"
        self.assertEqual(Window.pc_upgrade_blocker(w), "")
        w.jobs = {"固件升级"}
        self.assertIn("当前设备操作", Window.pc_upgrade_blocker(w))
