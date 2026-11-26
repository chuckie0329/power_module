import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pc_client.chart import HistoryPane
from pc_client.history import Point, PowerHistory


class ChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.history = PowerHistory()
        self.origin = time.monotonic() - 60
        self.history.started_at = self.origin
        self.history.active = True
        self.history.points.extend(
            Point(self.origin + i / 20, 0.88 if i < 860 else 2.68, 0, 4, 0.22 if i < 860 else 0.67)
            for i in range(1, 1200)
        )
        self.history.points.append(Point(self.origin + 59.99, 3.2, 0, 4, 0.8))
        self.pane = HistoryPane(self.history)
        self.pane.minutes.setValue(1)
        self.pane.resize(725, 440)
        self.pane.show()
        self.app.processEvents()
        self.addCleanup(self.pane.close)
        self.addCleanup(self.pane.refresh_timer.stop)

    def point(self, second, height=0.5):
        bounds = self.pane.chart.plot_rect()
        return QPoint(round(bounds.left() + second / 60 * bounds.width()),
                      round(bounds.bottom() - height * bounds.height()))

    def test_load_step_does_not_fill_between_curve_and_chord(self):
        chart = self.pane.chart
        self.assertEqual(len(chart.prepare_plot(chart.plot_rect().width())), 3)
        frame = chart.grab().toImage()
        colors = set()
        for second, height in ((31, 0.57), (31, 0.35)):
            colors.add(frame.pixelColor(self.point(second, height)).name())
        self.assertTrue(colors.isdisjoint({"#8b4a9e", "#097b70"}))
        self.assertEqual(self.pane.values["amps"].text(), "0.800 A")
        self.assertEqual(self.pane.values["peak_watts"].text(), "3.20 W")

    def test_reverse_drag_freezes_and_resume_reads_new_points(self):
        pane, chart = self.pane, self.pane.chart
        before = chart.points
        QTest.mousePress(chart, Qt.MouseButton.LeftButton, pos=self.point(35))
        self.history.started_at -= 8
        self.history.points.append(Point(self.origin + 60, 4, 1, 5, 1))
        pane.refresh()
        self.assertIs(chart.points, before)
        QTest.mouseMove(chart, self.point(10))
        QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, pos=self.point(10))
        self.assertEqual(pane.scope.text(), "框选统计")
        self.assertEqual(pane.values["amps"].text(), "0.220 A")
        self.assertEqual(pane.values["average_watts"].text(), "0.88 W")
        pane.resume_button.click()
        self.assertIsNone(chart.selection)
        self.assertEqual(pane.values["amps"].text(), "1.000 A")

    def test_click_cancels_without_leaving_view_frozen(self):
        QTest.mouseClick(self.pane.chart, Qt.MouseButton.LeftButton, pos=self.point(20))
        self.assertIsNone(self.pane.snapshot)
        self.assertTrue(self.pane.minutes.isEnabled())


class RangeSummaryTests(unittest.TestCase):
    def test_clips_power_average_and_excludes_gaps_and_outside_peaks(self):
        points = [Point(0, 1000, 0, 100, 100), Point(2, 10, 0, 12, 0.5),
                  Point(3, 30, 0, None, 1.2), Point(7, 60, 1, 11, None),
                  Point(9, 1000, 1, 100, 100)]
        result = PowerHistory.summarize(points, 1, 8)
        self.assertEqual((result.volts, result.amps, result.peak_watts), (12, 1.2, 60))
        self.assertEqual((result.average.seconds, result.average.samples), (3, 3))
        self.assertAlmostEqual(result.average.watts, (257.5 + 20 + 295) / 3)
