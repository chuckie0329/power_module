"""The first catalog paint must fit both wrapped update descriptions."""
import os
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from pc_client import update_dialog
from pc_client.theme import STYLES


class Parent(QWidget):
    def firmware_upgrade_allowed(self):
        return True


class FirstNotesPaint(QObject):
    def __init__(self):
        super().__init__()
        self.boxes = {}

    def eventFilter(self, widget, event):
        if event.type() == QEvent.Type.Paint and "发布说明" in widget.text():
            self.boxes.setdefault(widget, (widget.geometry(), widget.heightForWidth(widget.width())))
        return False


class UpdateDialogLayoutTests(unittest.TestCase):
    def test_async_catalog_fits_when_first_opened(self):
        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(STYLES)
        parent = Parent()
        self.addCleanup(parent.close)
        dialog = update_dialog.UpdateDialog(parent, "1.2.2")
        self.addCleanup(dialog.close)
        dialog.directory = Path(".")
        observer = FirstNotesPaint()
        for label in dialog.rows.values():
            label.installEventFilter(observer)
        released = Event()
        self.addCleanup(released.set)
        releases = {
            "pc": SimpleNamespace(version="9.0.0", notes="客户端发布说明，修复在线更新并改善连接反馈。" * 8),
            "firmware": SimpleNamespace(version="1.2.3", notes="固件发布说明，下载后请关闭输出与两路继电器。" * 8),
        }

        def current_catalog():
            if not released.wait(5):
                raise TimeoutError
            return "simulated-revision"

        with (patch.object(update_dialog, "catalog_revision", side_effect=current_catalog),
              patch.object(update_dialog, "check_catalog", side_effect=lambda kind, **_: releases[kind]),
              patch.object(update_dialog, "cache_firmware", return_value=Path("PowerModule-ESP32.bin"))):
            dialog.show()
            dialog.check_all()
            QTest.qWait(20)
            initial_height = dialog.height()
            self.assertIn("正在检查", dialog.status.text())
            self.assertFalse(observer.boxes)
            released.set()
            deadline = monotonic() + 4
            while monotonic() < deadline and (dialog.operation or len(observer.boxes) != 2):
                QTest.qWait(15)

        self.assertTrue(dialog.isVisible())
        self.assertGreater(dialog.height(), initial_height)
        self.assertIn("固件已校验", dialog.status.text())
        self.assertEqual(len(observer.boxes), 2)
        for kind in ("pc", "firmware"):
            label = dialog.rows[kind]
            self.assertIn(releases[kind].notes, label.text())
            box, wanted_height = observer.boxes[label]
            self.assertGreater(wanted_height, label.fontMetrics().height())
            self.assertGreaterEqual(box.height(), wanted_height, kind)
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()), kind)
        self.assertLess(observer.boxes[dialog.rows["pc"]][0].bottom(),
                        observer.boxes[dialog.rows["firmware"]][0].top())
        self.assertLess(dialog.rows["firmware"].geometry().bottom(), dialog.status.geometry().top())
