import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import QSettings

from pc_client.app import RELAY_SEQUENCE, Window
from pc_client.connection import Client, DeviceError
from pc_client.relay_dialog import RelayNamesDialog, RelaySequenceDialog
from pc_client.relay_sequence import RelayNames, RelayStep, SequenceStore, load_sequence, resolve_steps, save_sequence, sequence_directory

STEPS = (RelayStep(0, True, False), RelayStep(0, None, True))
NAMED = (RelayStep(0, None, None, named=(("启动", True), ("RESET", False))),
         RelayStep(10, None, None, True))



class SequenceFiles(unittest.TestCase):
    def test_storage_is_beside_executable_and_created_only_on_save(self):
        with TemporaryDirectory() as temp:
            portable = Path(temp) / "portable"
            portable.mkdir()
            exe = portable / "PowerModule-PC.exe"
            with patch.object(sys, "executable", str(exe)), patch.object(sys, "frozen", True, create=True):
                store = SequenceStore(sequence_directory())
            folder = portable.resolve() / "sequences"
            self.assertEqual(store.directory, folder)
            self.assertEqual(store.names(), [])
            self.assertFalse(folder.exists())
            store.save("通电流程", STEPS)
            self.assertTrue((folder / "通电流程.json").is_file())
            self.assertEqual(store.load("通电流程"), STEPS)

    def test_round_trip_and_no_overwrite_when_invalid(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "K1 测试.json"
            expected = (RelayStep(200, False, None, False), RelayStep(5010, None, True, True))
            save_sequence(path, expected)
            self.assertEqual(load_sequence(path), expected)
            with self.assertRaises(ValueError):
                save_sequence(path, (RelayStep(500, None, None),))
            self.assertEqual(load_sequence(path), expected)

    def test_v1_keeps_output_unchanged_and_v2_rejects_numeric_output(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.json"
            data = {"format": "power-module-relay-sequence", "version": 1,
                    "steps": [{"wait_ms": 1000, "k1": True, "k2": None}]}
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(load_sequence(path), (RelayStep(1000, True, None),))
            data["version"] = 2
            data["steps"][0]["output"] = 1
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_sequence(path)

    def test_untrusted_data_is_bounded_and_strictly_typed(self):
        bad = ('[{"wait_ms": 0, "k1": "false", "k2": null}]',
               '[{"wait_ms": true, "k1": true, "k2": null}]',
               '[{"wait_ms": -1, "k1": true, "k2": null}]',
               '[{"wait_ms": 0, "k1": true}]',
               '[{"wait_ms": 0, "k1": true, "k2": null, "op": "set_output"}]')
        with TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            for rows in bad:
                path.write_text(json.dumps({"format": "power-module-relay-sequence", "version": 1,
                                            "steps": json.loads(rows)}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_sequence(path)
            path.write_bytes(b"x" * 65536)
            with self.assertRaises(ValueError):
                load_sequence(path)


    def test_named_round_trip_and_full_preflight(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.json"
            save_sequence(path, NAMED)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 3)
            self.assertEqual(load_sequence(path), NAMED)
            config = RelayNames(("启动", "RESET"), ("启动", None))
            with self.assertRaisesRegex(ValueError, "RESET"):
                resolve_steps(NAMED, config)
            self.assertEqual(resolve_steps(STEPS, config), STEPS)
            for bad in ([["启动", 1]], [["启动", True], ["启动", False]], [["", True]], [["启动"]]):
                data["steps"][0]["relays"] = bad
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_sequence(path)

    def test_mapping_persists_and_rejects_duplicate_bindings_or_names(self):
        config = RelayNames(("启动", "RESET"), ("RESET", "启动"))
        self.assertEqual(RelayNames.decode(config.encode()), config)
        for names, ports in [
            (("RESET", "ｒｅｓｅｔ"), (None, None)),
            (("RESET",), ("RESET", "RESET")),
            (("RESET",), ("启动", None)),
        ]:
            with self.assertRaises(ValueError):
                RelayNames(names, ports)


class SequenceWorker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        client = self.client = Client(SimpleNamespace(relay_progress=Mock()))
        client.link = object()
        client.ready = True
        client.info = {"device_id": "sim", "direct_control": True}
        client.state_received = time.monotonic()
        client.latest = {"applied": {"relay1": False, "relay2": False}, "measured": {"fresh": True}}
        self.cancel = Event()
        client.change = AsyncMock(side_effect=self.reply)

    @staticmethod
    async def reply(op, args, rev):
        return {"applied": {f"relay{args['channel']}": args["enabled"]}}

    async def run_steps(self, steps=STEPS, device="sim"):
        return await self.client.run_relay_sequence(device, steps, self.cancel.is_set)

    async def test_order_skips_hold_waits_and_only_runs_once(self):
        sent_at = []
        base = time.monotonic()

        async def timed(op, args, rev):
            sent_at.append(time.monotonic())
            return await self.reply(op, args, rev)

        self.client.change.side_effect = timed
        result = await self.run_steps((RelayStep(40, True, False), RelayStep(35, None, True)))
        self.assertEqual(result, {"done": 2, "total": 2, "pending": None, "error": ""})
        self.assertGreaterEqual(sent_at[0] - base, 0.039)
        self.assertGreaterEqual(sent_at[2] - sent_at[1], 0.034)
        self.assertEqual([call.args for call in self.client.change.await_args_list], [
            ("set_relay", {"channel": 1, "enabled": True}, None),
            ("set_relay", {"channel": 2, "enabled": False}, None),
            ("set_relay", {"channel": 2, "enabled": True}, None),
        ])

    async def test_rebinding_keeps_action_order_and_output_rules(self):
        self.live_output()
        for wiring, order in [(("启动", "RESET"), (1, 2)), (("RESET", "启动"), (2, 1))]:
            self.client.change.reset_mock()
            steps = resolve_steps((
                RelayStep(0, None, None, False, NAMED[0].named),
                NAMED[1],
            ), RelayNames(("启动", "RESET"), wiring))
            result = await self.client.run_relay_sequence(
                "sim", steps, self.cancel.is_set, (4, 5000, 1200), True,
            )
            calls = self.client.change.await_args_list
            self.assertEqual(result["error"], "")
            self.assertEqual([call.args[0] for call in calls],
                             ["output_off", "set_relay", "set_relay", "set_output"])
            self.assertEqual([call.args[1]["channel"] for call in calls[1:3]], list(order))
            self.assertEqual([call.args[1]["enabled"] for call in calls[1:3]], [True, False])

    async def test_unresolved_named_steps_never_reach_device(self):
        with self.assertRaises(ValueError):
            await self.run_steps(NAMED)
        self.client.change.assert_not_awaited()

    async def test_stop_during_long_wait(self):
        asyncio.get_running_loop().call_later(0.02, self.cancel.set)
        result = await asyncio.wait_for(self.run_steps((RelayStep(3600000, True, None),)), 1)
        self.assertEqual(result["error"], "RELAY_STOPPED")
        self.client.change.assert_not_awaited()

    async def test_stop_during_first_command_never_sends_second(self):
        async def stop(op, args, rev):
            self.cancel.set()
            return await self.reply(op, args, rev)
        self.client.change.side_effect = stop
        result = await self.run_steps()
        self.assertEqual((result["done"], result["pending"], result["error"]), (0, None, "RELAY_STOPPED"))
        self.assertEqual(self.client.change.await_count, 1)

    async def test_disconnect_during_wait_no_commands(self):
        asyncio.get_running_loop().call_later(0.02, setattr, self.client, "ready", False)
        result = await self.run_steps((RelayStep(200, True, None),))
        self.assertEqual(result["error"], "LINK_ERROR")
        self.client.change.assert_not_awaited()

    async def test_replaced_link_or_device_reboot_never_runs_next_operation(self):
        for attr, value in (("link", object()), ("relay_epoch", 1)):
            with self.subTest(attr=attr):
                original = getattr(self.client, attr)
                async def replace(op, args, rev):
                    setattr(self.client, attr, value)
                    return await self.reply(op, args, rev)
                self.client.change.reset_mock()
                self.client.change.side_effect = replace
                result = await self.run_steps()
                self.assertEqual(self.client.change.await_count, 1)
                self.assertEqual(result["pending"], 1)
                self.assertEqual(result["error"], "RELAY_READBACK")
                setattr(self.client, attr, original)

    async def test_mismatch_or_timeout_does_not_retry(self):
        for replies, error in (([{"applied": {"relay1": 1}}], "RELAY_READBACK"),
                               ([DeviceError("REPLY_TIMEOUT")], "REPLY_TIMEOUT")):
            self.client.change.reset_mock()
            self.client.change.side_effect = replies
            result = await self.run_steps()
            self.assertEqual((result["done"], result["pending"], result["error"]), (0, 1, error))
            self.assertEqual(self.client.change.await_count, 1)

    async def test_wrong_device_or_stale_state_never_sends_commands(self):
        self.assertEqual((await self.run_steps(device="other"))["error"], "LINK_ERROR")
        self.client.state_received -= 8
        self.assertEqual((await self.run_steps())["error"], "RELAY_NOT_READY")
        self.client.change.assert_not_awaited()


    def live_output(self):
        self.client.latest["applied"].update(enabled=True, group=4, voltage_mV=5000, current_mA=1200)
        self.client.latest["state_rev"] = 20

        async def apply(op, args, rev):
            if op in ("output_off", "set_output"):
                self.client.latest["applied"]["enabled"] = op == "set_output"
            else:
                self.client.latest["applied"][f"relay{args['channel']}"] = args["enabled"]
            self.client.latest["state_rev"] += 1
            return self.client.latest

        self.client.change.side_effect = apply

    async def test_outputs_and_relays_order_and_confirmed_settings(self):
        self.live_output()
        result = await self.client.run_relay_sequence("sim", (
            RelayStep(0, False, None, False), RelayStep(0, None, True, True),
        ), self.cancel.is_set, (4, 5000, 1200), True)
        self.assertEqual(result, {"done": 2, "total": 2, "pending": None, "error": ""})
        self.assertEqual([call.args for call in self.client.change.await_args_list], [
            ("output_off", {}, None),
            ("set_relay", {"channel": 1, "enabled": False}, None),
            ("set_relay", {"channel": 2, "enabled": True}, None),
            ("set_output", {"enabled": True, "voltage_mV": 5000, "current_mA": 1200}, 23),
        ])

    async def test_unconfirmed_shutdown_blocks_relay(self):
        self.live_output()
        self.client.change.side_effect = None
        self.client.change.return_value = self.client.latest
        result = await self.run_steps((RelayStep(0, False, True, False),))
        self.assertEqual((result["done"], result["pending"], result["error"]), (0, "output", "RELAY_OUTPUT_READBACK"))
        self.client.change.assert_awaited_once_with("output_off", {}, None)

    async def test_external_off_or_setpoint_change_cannot_auto_reenergize(self):
        for key, value in (("enabled", False), ("voltage_mV", 9000), ("group", 7)):
            self.live_output()
            asyncio.get_running_loop().call_later(0.015, self.client.latest["applied"].update, {key: value})
            result = await self.client.run_relay_sequence(
                "sim", (RelayStep(100, None, None, True),), self.cancel.is_set, (4, 5000, 1200), True,
            )
            self.assertEqual(result["error"], "RELAY_OUTPUT_CHANGED")
            self.client.change.assert_not_awaited()


class SequenceUi(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, self.temp.name)
        self.window = Window()
        self.window.sequence_store = SequenceStore(Path(self.temp.name) / "sequences")
        self.window.refresh_sequence_selector()
        self.window.show()
        self.addCleanup(self.close)
        self.app.processEvents()

    def close(self):
        self.window.info = {}
        self.window.jobs.clear()
        self.window.close()
        self.window.worker.thread.join(timeout=2)

    def connected(self, enabled=True):
        w = self.window
        w.info = {"device_id": "sim", "direct_control": True, "firmware": "1.2.6"}
        w.state_read({"applied": {"relay1": False, "relay2": False, "enabled": enabled,
                                  "group": 2, "voltage_mV": 5000, "current_mA": 1200},
                      "measured": {"fresh": True}}, time.monotonic(), True)

    def select(self, name):
        self.window.refresh_sequence_selector()
        self.window.sequence_selector.setCurrentIndex(self.window.sequence_selector.findData(name))

    def test_names_editor_saves_bindings_without_operating_device(self):
        w = self.window
        self.connected()

        def edit(dialog):
            for name in ("启动", "RESET"):
                dialog.new_name.setText(name)
                dialog.add_name()
            dialog.ports[0].setCurrentIndex(1)
            dialog.ports[1].setCurrentIndex(1)
            dialog.save()
            self.assertIn("不能绑定同一个", dialog.status.text())
            dialog.ports[1].setCurrentIndex(2)
            dialog.save()
            return dialog.result()

        with patch.object(RelayNamesDialog, "exec", new=edit), patch.object(w.worker, "submit") as submit:
            w.relay_name_config.click()
            submit.assert_not_called()
        self.assertEqual(w.relay_names.bindings, ("启动", "RESET"))
        self.assertIn("启动", w.relay[1].text())
        settings = QSettings(w.group_settings.fileName(), QSettings.Format.IniFormat)
        self.assertEqual(RelayNames.decode(settings.value("relays/names_and_bindings")), w.relay_names)

    def test_editor_preserves_step_table_saves_to_fixed_directory(self):
        w = self.window
        w.save_relay_names(RelayNames(("启动", "RESET"), ("启动", "RESET")))

        def edit(dialog):
            self.assertIs(dialog.store, w.sequence_store)
            self.assertEqual(dialog.table.columnCount(), 6)
            dialog.table.cellWidget(0, 1).setCurrentIndex(1)
            dialog.table.cellWidget(0, 2).setCurrentIndex(1)
            dialog.add_step()
            dialog.table.cellWidget(1, 0).setValue(1200)
            dialog.table.cellWidget(1, 3).setCurrentIndex(2)
            dialog.table.cellWidget(1, 4).setCurrentIndex(2)
            dialog.table.cellWidget(1, 5).setCurrentIndex(2)
            dialog.move_step(-1)
            dialog.name.setText("中文测试")
            dialog.save.click()
            return dialog.result()

        self.assertFalse(w.sequence_run.isEnabled())
        with patch.object(RelaySequenceDialog, "exec", new=edit):
            w.sequence_new.click()
        expected = (RelayStep(1200, None, None, False, (("RESET", False),)),
                    RelayStep(0, None, None, named=(("启动", True),)))
        self.assertEqual(w.sequence_store.names(), ["中文测试"])
        self.assertEqual(w.sequence_store.load("中文测试"), expected)
        self.assertEqual(w.loaded_sequence, ("中文测试", expected))
        self.assertFalse(w.sequence_run.isEnabled())
        self.assertEqual(w.jobs, set())

    def test_missing_name_blocks_entire_run_and_swap_updates_resolution(self):
        w = self.window
        self.connected()
        w.sequence_store.save("named", NAMED)
        self.select("named")
        self.assertFalse(w.sequence_run.isEnabled())
        self.assertIn("请先绑定", w.sequence_summary.text())
        snapshot = (w.sequence_store.directory / "named.json").read_bytes()
        with patch.object(w.worker, "submit", side_effect=lambda _name, coro: coro.close()) as submit:
            w.start_relay_sequence(NAMED)
            submit.assert_not_called()
            w.save_relay_names(RelayNames(("启动", "RESET"), ("RESET", "启动")))
            submit.assert_not_called()
            self.assertTrue(w.sequence_run.isEnabled())
            self.assertIn("K2：启动", w.sequence_summary.text())
            w.sequence_run.click()
            submit.assert_called_once()
            self.assertFalse(w.relay_name_config.isEnabled())
        self.assertEqual((w.sequence_store.directory / "named.json").read_bytes(), snapshot)

    def test_legacy_editor_does_not_silently_convert_channels(self):
        w = self.window
        w.save_relay_names(RelayNames(("启动", "RESET"), ("RESET", "启动")))
        w.sequence_store.save("legacy", STEPS)
        self.select("legacy")

        def edit(dialog):
            self.assertEqual(dialog.table.columnCount(), 4)
            dialog.save.click()
            return dialog.result()

        with patch.object(RelaySequenceDialog, "exec", new=edit):
            w.sequence_edit.click()
        self.assertEqual(w.sequence_store.load("legacy"), STEPS)

    def test_select_auto_loads_and_never_runs_or_leaves_stale_sequence(self):
        self.connected()
        w = self.window
        self.assertFalse(hasattr(w, "sequence_load"))
        w.sequence_store.save("one", STEPS)
        enabled = (RelayStep(0, None, None, True),)
        w.sequence_store.save("two", enabled)
        (w.sequence_store.directory / "broken.json").write_text("invalid")
        with patch.object(QDialog, "exec", side_effect=AssertionError("unexpected popup")), patch.object(
                w.worker, "submit", side_effect=AssertionError("selection must not send commands")) as submit:
            self.select("one")
            self.assertEqual(w.loaded_sequence, ("one", STEPS))
            self.assertTrue(w.sequence_run.isEnabled())
            self.select("two")
            self.assertEqual(w.loaded_sequence, ("two", enabled))
            self.assertIn("会开启主输出", w.sequence_summary.text())
            self.assertIn("5.00 V", w.sequence_summary.text())
            self.select("broken")
            self.assertIsNone(w.loaded_sequence)
            self.assertFalse(w.sequence_run.isEnabled())
            self.assertIn("未载入", w.sequence_status.text())
            self.assertEqual(w.sequence_summary.text(), "")
            self.assertFalse(w.jobs)
            submit.assert_not_called()

    def test_main_run_and_stop_use_single_button_and_no_popup(self):
        self.connected()
        w = self.window
        w.sequence_store.save("test", STEPS)
        self.select("test")
        sent = []

        def record(name, operation):
            args = operation.cr_frame.f_locals.copy()
            operation.close()
            sent.append((name, args.get("steps")))

        with patch.object(w.worker, "submit", side_effect=record), patch.object(
                QDialog, "exec", side_effect=AssertionError("unexpected popup")):
            w.sequence_run.click()
            self.assertEqual(sent, [(RELAY_SEQUENCE, STEPS)])
            self.assertEqual(w.sequence_run.text(), "停止")
            self.assertFalse(w.sequence_selector.isEnabled())
            self.assertFalse(w.relay[1].isEnabled())
            w.toggle_relay(2)
            self.assertEqual(len(sent), 1)
            w.sequence_run.click()
            self.assertTrue(w.relay_cancel.is_set())
            self.assertFalse(w.sequence_run.isEnabled())
            w.output_switch.click()
            self.assertEqual(sent[-1][0], "关闭输出")
            w.finished(RELAY_SEQUENCE, {"done": 0, "pending": None, "error": "RELAY_STOPPED"}, "")
            self.assertIn("已停止", w.sequence_status.text())
            self.assertEqual(w.sequence_run.text(), "运行")
            self.assertTrue(w.sequence_selector.isEnabled())

    def test_fixed_storage_does_not_overwrite_with_new_or_traverse(self):
        store = self.window.sequence_store
        store.save("自动测试", STEPS)
        for name in ("自动测试", "../外部", "CON.txt", "bad:stream"):
            with self.assertRaises(ValueError):
                store.save(name, (RelayStep(0, None, None, False),))
        self.assertEqual(store.load("自动测试"), STEPS)
        store.save("自动测试", (RelayStep(0, None, None, False),), original="自动测试")
        self.assertEqual(store.load("自动测试"), (RelayStep(0, None, None, False),))
