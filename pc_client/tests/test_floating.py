"""Qt UI tests with the device and OS tray boundary simulated."""
import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from pc_client.app import Window
from pc_client.theme import STYLES, icon


class FloatingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(STYLES)
        cls.app.setWindowIcon(icon())

    def setUp(self):
        self.tray_available = patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True)
        self.tray_available.start()
        self.addCleanup(self.tray_available.stop)
        self.window = Window()
        self.window.show()
        self.addCleanup(self.finish_window)
        self.worker = self.window.worker
        self.sent = []
        self.window.change = lambda *args, **kwargs: self.sent.append((args, kwargs))

    def finish_window(self):
        self.window.restore_main()
        self.window.info = {}
        self.window.jobs.clear()
        self.window.close()
        self.worker.thread.join(timeout=2)

    def enter(self):
        self.window.float_button.click()
        self.app.processEvents()
        return self.window.floating

    def live(self, enabled=False, fresh=True, when=None):
        when = time.monotonic() if when is None else when
        self.window.info = dict(device_id="sim", firmware="1.2.1", direct_control=True,
                                sx_model_raw=4008, sx_version_raw=100)
        self.window.state_read({
            "applied": dict(enabled=enabled, relay1=False, relay2=True, group=0,
                            voltage_mV=12000, current_mA=1000),
            "measured": dict(fresh=fresh, voltage_mV=12010, current_mA=1730, power_mW=20800),
            "state_rev": 7,
        }, when, False)

    def test_telemetry_and_buttons_reuse_session(self):
        self.live()
        panel = self.enter()
        self.assertTrue(panel.isVisible())
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.tray.isVisible())
        for flag in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowStaysOnTopHint):
            self.assertTrue(panel.windowFlags() & flag)
        self.assertTrue(panel.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        # Offscreen's host window minimums vary; validate our requested content size.
        self.assertLess(panel.minimumWidth(), 260)
        self.assertLess(panel.minimumHeight(), 90)
        self.assertEqual(panel.maximumSize(), panel.minimumSize())
        frame = panel.grab().toImage()
        self.assertEqual(frame.pixelColor(0, 0).alpha(), 0)
        self.assertIn(frame.pixelColor(4, panel.height() // 2).alpha(), range(1, 255))
        self.assertEqual([meter.text() for meter, _ in panel.values.values()],
                         ["12.01 V", "0.173 A", "2.1 W"])
        self.assertEqual([panel.relays[i].text() for i in (1, 2)], ["K1 关", "K2 开"])
        self.assertEqual(self.sent, [])
        panel.output.click()
        panel.relays[1].click()
        self.assertEqual(self.sent, [
            (("开启输出", "set_output", {"voltage_mV": 12000, "current_mA": 1000, "enabled": True}),
             {"revision": True}),
            (("继电器 K1", "set_relay", {"channel": 1, "enabled": True}), {}),
        ])
        self.live(enabled=True)
        panel.output.click()
        self.assertEqual(self.sent[-1], (("关闭输出", "output_off", {}), {}))
        self.window.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(panel.isVisible())
        self.assertFalse(self.window.tray.isVisible())
        self.assertIs(self.window.worker, self.worker)

    def test_hidden_edits_and_stale_readings_never_turn_output_on(self):
        self.live()
        self.window.voltage.setValue(20)
        self.assertTrue(self.window.output_dirty)
        panel = self.enter()
        self.assertFalse(panel.output.isEnabled())
        panel.output.click()
        self.assertEqual(self.sent, [])
        self.live(enabled=True, when=time.monotonic()-7)
        self.assertEqual([meter.text() for meter, _ in panel.values.values()], ["— V", "— A", "— W"])
        self.assertEqual(panel.output.text(), "输出 —")
        self.assertFalse(panel.relays[1].isEnabled())
        panel.output.click()
        self.assertEqual(self.sent, [(("关闭输出", "output_off", {}), {})])
        self.window.jobs.add("固件升级")
        self.window.render()
        self.assertFalse(panel.output.isEnabled())
        self.assertFalse(panel.relays[1].isEnabled())
        panel.output.click()
        self.assertEqual(len(self.sent), 1)
        self.window.jobs.clear()
        self.window.disconnected("")
        self.assertFalse(panel.output.isEnabled())
        self.assertFalse(panel.relays[2].isEnabled())
        self.assertEqual(panel.relays[2].text(), "K2 —")
        panel.close()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.tray.isVisible())

    def test_detached_chart_leaves_taskbar_with_main(self):
        self.window.history_pane.open_window()
        dialog = self.window.history_pane.dialog
        self.assertTrue(dialog.isVisible())
        self.enter()
        self.assertFalse(dialog.isVisible())
        self.window.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        self.assertTrue(dialog.isVisible())
        dialog.close()

    def test_no_tray_does_not_hide_main(self):
        with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
            self.assertIsNone(self.enter())
        self.assertTrue(self.window.isVisible())


if __name__ == "__main__":
    unittest.main()
