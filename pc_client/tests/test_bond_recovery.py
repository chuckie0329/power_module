from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QMessageBox
from pc_client.app import Window


class BondTests(unittest.TestCase):
    def setUp(self):
        self.w = SimpleNamespace(
            kind="usb", info={"clear_ble_bonds": True, "device_id": "PM-123456789ABC"}, jobs=set(),
            pc_handoff=False, exit_requested=False, last_receipt=monotonic(),
            state={"measured": {"fresh": True}, "applied": {"enabled": False, "relay1": False, "relay2": False}},
            launch=Mock(), worker=SimpleNamespace(client=SimpleNamespace(call=Mock())),
        )
        self.w.bond_recovery_status = lambda: Window.bond_recovery_status(self.w)

    def test_disallow_ble_missing_capability_busy_and_unknown_or_live_output(self):
        self.assertEqual(self.w.bond_recovery_status(), "")
        for attr, value in [("kind", "ble"), ("info", {"device_id": "PM-123456789ABC"}),
                            ("jobs", {"固件升级"}), ("jobs", {"应用设定"}), ("last_receipt", 0),
                            ("state", {"measured": {"fresh": True}, "applied": {}})]:
            old = getattr(self.w, attr)
            setattr(self.w, attr, value)
            self.assertTrue(self.w.bond_recovery_status())
            setattr(self.w, attr, old)
        self.w.state["applied"]["relay2"] = True
        self.assertTrue(self.w.bond_recovery_status())

    def test_cancel_and_device_change_never_send_clear(self):
        with patch("pc_client.app.QMessageBox", Mock(Icon=QMessageBox.Icon, StandardButton=QMessageBox.StandardButton)) as msg:
            msg.return_value.exec.return_value = QMessageBox.StandardButton.No
            Window.clear_ble_bonds(self.w)
            self.w.worker.client.call.assert_not_called()
            def changed():
                self.w.info["device_id"] = "PM-other"
                return QMessageBox.StandardButton.Yes
            msg.return_value.exec.side_effect = changed
            Window.clear_ble_bonds(self.w)
            self.w.worker.client.call.assert_not_called()

    def test_confirmation_sends_only_usb_operation_to_current_device(self):
        with patch("pc_client.app.QMessageBox", Mock(Icon=QMessageBox.Icon, StandardButton=QMessageBox.StandardButton)) as msg:
            msg.return_value.exec.return_value = QMessageBox.StandardButton.Yes
            Window.clear_ble_bonds(self.w)
        self.w.worker.client.call.assert_called_once_with(
            "clear_ble_bonds", {"device_id": "PM-123456789ABC", "confirm_all": True})
        self.w.launch.assert_called_once()
