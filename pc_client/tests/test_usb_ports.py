import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from pc_client.app import Window
from pc_client.connection import usb_ports


def port(device, description, vid=None, pid=None):
    return SimpleNamespace(device=device, description=description, vid=vid, pid=pid)


class UsbPortTests(unittest.TestCase):
    def test_lists_only_ch340_including_missing_driver_metadata(self):
        ports = [
            port("COM1", "通信端口"),
            port("COM2", "通信端口"),
            port("COM3", "Intel AMT - SOL"),
            port("COM6", "USB-SERIAL CH341", 0x1A86, 0x5523),
            port("COM8", "USB-SERIAL CH340", 0x10C4, 0xEA60),
            port("COM31", "USB-SERIAL CH340 (COM31)", 0x1A86, 0x7523),
            port("COM35", "USB-SERIAL CH340E"),
            port("COM39", None, 0x1A86, 0x7523),
        ]
        with patch("pc_client.connection.list_ports.comports", return_value=ports):
            self.assertEqual(usb_ports(), [("COM31  ·  USB-SERIAL CH340", "COM31"),
                                           ("COM35  ·  USB-SERIAL CH340E", "COM35"),
                                           ("COM39  ·  CH340", "COM39")])

    def test_no_adapter_never_falls_back_to_other_ports(self):
        with patch("pc_client.connection.list_ports.comports", return_value=[port("COM1", "通信端口")]):
            self.assertEqual(usb_ports(), [])


class UsbPortWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_keeps_ch340_selection_and_disables_connect_for_empty_list(self):
        with patch("pc_client.connection.list_ports.comports") as enumerate_ports:
            enumerate_ports.return_value = [port("COM2", "通信端口")]
            window = Window()
            try:
                with patch.object(window, "launch") as launch:
                    self.assertEqual(window.target.count(), 0)
                    self.assertEqual(window.target.placeholderText(), "未找到 CH340 串口")
                    self.assertFalse(window.connect_button.isEnabled())

                    ports = [port("COM31", "USB-SERIAL CH340", 0x1A86, 0x7523),
                             port("COM35", "USB-SERIAL CH340E")]
                    enumerate_ports.return_value += ports
                    window.refresh_targets()
                    self.assertEqual(window.target.currentData(), "COM31")
                    self.assertTrue(window.connect_button.isEnabled())
                    window.target.setCurrentIndex(window.target.findData("COM35"))
                    window.refresh_targets()
                    self.assertEqual(window.target.currentData(), "COM35")
                    enumerate_ports.return_value = [port("COM2", "通信端口")]
                    window.refresh_targets()
                    self.assertFalse(window.connect_button.isEnabled())
                    launch.assert_not_called()
            finally:
                window.close()
                window.worker.thread.join(timeout=2)
