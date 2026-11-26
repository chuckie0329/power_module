import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from pc_client.app import GROUP_SWITCH, Window
from pc_client.theme import STYLES


class MemoryGroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(STYLES)

    def setUp(self):
        self.window = Window()
        self.addCleanup(self.finish_window)
        self.window.resize(990, 690)
        self.window.show()
        self.commands = []
        mock_submit = patch.object(self.window.worker, "submit", side_effect=self.record)
        mock_submit.start()
        self.addCleanup(mock_submit.stop)
        self.window.info = dict(device_id="simulation", firmware="1.2.4", direct_control=True,
                                group_select=True, protection_write=True, opp_write=True)
        self.live()
        self.app.processEvents()

    def record(self, action, coroutine):
        code = coroutine.cr_frame.f_locals
        self.commands.append((action, code["operation"], code["args"], code["revision"]))
        coroutine.close()

    def live(self, group=0, enabled=True, fresh=True):
        self.window.state_read({
            "state_rev": 23,
            "applied": dict(group=group, enabled=enabled, relay1=False, relay2=False,
                            voltage_mV=4300 if group else 4000, current_mA=2000),
            "measured": dict(fresh=fresh, voltage_mV=4000, current_mA=201, power_mW=804),
            "protection": dict(ovp_mV=6500 if group else 5500, ocp_mA=4400, opp_mW=20000,
                               ocp_step_mA=1),
        }, time.monotonic(), True)

    def finish_window(self):
        self.window.info = {}
        self.window.jobs.clear()
        self.window.close()
        self.window.worker.thread.join(timeout=2)

    def choose(self, group):
        # activated is emitted by Qt only when the user commits a selection.
        combo = self.window.group_selector
        combo.setCurrentIndex(group)
        combo.activated.emit(group)

    def test_shared_card_and_compact_controls(self):
        w = self.window
        card = w.voltage.parentWidget()
        self.assertIs(w.ovp.parentWidget(), card)
        self.assertIs(w.group_selector.parentWidget(), card)
        self.assertEqual([w.group_selector.itemData(i) for i in range(w.group_selector.count())], list(range(11)))
        self.assertEqual(card.width(), 224)
        plot_width = w.history_pane.chart.plot_rect().width()
        self.assertGreater(plot_width, 385)
        for field in (w.voltage, w.limit, w.ovp, w.ocp, w.opp):
            field.setValue(field.maximum())
            self.assertLess(field.fontMetrics().horizontalAdvance(field.text()), field.lineEdit().width())
        self.assertLess(w.group_selector.y(), w.voltage.y())
        self.assertFalse(any(x.text() == GROUP_SWITCH for x in card.findChildren(QPushButton)))

    def test_committed_selection_calls_original_revision_checked_switch_only(self):
        w = self.window
        self.choose(3)
        self.assertEqual(self.commands, [(GROUP_SWITCH, "select_group", {"group": 3}, 23)])
        self.assertIn(GROUP_SWITCH, w.jobs)
        self.assertFalse(w.group_selector.isEnabled())
        self.assertTrue(w.output_requests_off)
        self.assertEqual(w.group_selector.currentData(), 0)
        self.choose(4)
        self.assertEqual(len(self.commands), 1)
        self.live(group=3, enabled=False)
        w.finished(GROUP_SWITCH, {"applied": {"group": 3, "enabled": False}}, "")
        self.assertEqual(w.group_selector.currentData(), 3)
        self.assertEqual(w.voltage.value(), 4.3)
        self.assertEqual(w.ovp.value(), 6.5)
        self.assertEqual(w.output_switch.text(), "开启输出")
        self.assertIn("主输出已关闭", w.message.text())
        self.assertEqual(len(self.commands), 1)

    def test_rejected_switch_keeps_previous_group_and_output_state(self):
        w = self.window
        self.choose(1)
        w.finished(GROUP_SWITCH, None, "GROUP_RECALL_ON")
        self.assertEqual(w.group_selector.currentData(), 0)
        self.assertTrue(w.group_selector.isEnabled())
        self.assertEqual(w.output_switch.text(), "关闭输出")
        self.assertIn("M-OUT", w.message.text())

    def test_no_switch_on_readback_scroll_same_group_or_stale_state(self):
        combo = self.window.group_selector
        self.live(group=1)
        self.assertEqual(combo.currentData(), 1)
        self.choose(1)
        event = QWheelEvent(QPointF(6, 6), QPointF(6, 6), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(combo, event)
        self.assertEqual(combo.currentData(), 1)
        self.live(group=1, fresh=False)
        self.choose(5)
        self.assertEqual(self.commands, [])

    def test_keyboard_popup_commits_once(self):
        combo = self.window.group_selector
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key.Key_Down)
        self.assertEqual(self.commands, [])
        view = combo.view()
        self.assertTrue(view.isVisible())
        QTest.keyClick(view, Qt.Key.Key_Down)
        self.assertEqual(self.commands, [])
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertEqual(self.commands, [(GROUP_SWITCH, "select_group", {"group": 1}, 23)])
