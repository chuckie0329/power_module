import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QWheelEvent
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
        bounds = chart.plot_rect()
        voltage, current, power = (chart.series_rect(bounds, axis) for axis in range(3))
        self.assertLess(voltage.bottom(), current.top())
        self.assertLess(current.bottom(), power.top())
        frame = chart.grab().toImage()
        for lane, color, trace, wrong in ((current, "#8b4a9e", .275, .57), (power, "#097b70", .22, .35)):
            x = self.point(31).x()
            y = round(lane.bottom() - lane.height() * trace)
            nearby = (frame.pixelColor(x, y + offset) for offset in (-1, 0, 1))
            reference = QColor(color)
            self.assertTrue(any(max(abs(a - b) for a, b in zip(pixel.getRgb(), reference.getRgb())) < 90
                                for pixel in nearby))
            wrong_y = round(lane.bottom() - lane.height() * wrong)
            self.assertNotEqual(frame.pixelColor(x, wrong_y).name(), color)
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


    def wheel(self, delta, point):
        event = QWheelEvent(QPointF(point), QPointF(self.pane.chart.mapToGlobal(point)), QPoint(),
                            QPoint(0, delta), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(self.pane.chart, event)
        return event.isAccepted()

    def test_wheel_anchors_at_pointer_and_updates_window_stats(self):
        chart = self.pane.chart
        outside = QPoint(2, 2)
        self.assertFalse(self.wheel(120, outside))
        self.assertIsNone(self.pane.snapshot)
        at = self.point(15)
        anchor = chart.time_at(at.x())
        self.assertTrue(self.wheel(120, at))
        self.assertAlmostEqual(chart.end - chart.start, 48.0, places=5)
        self.assertAlmostEqual(chart.time_at(at.x()), anchor, places=5)
        self.assertEqual(self.pane.scope.text(), "缩放统计")
        self.assertEqual(self.pane.values["amps"].text(), "0.670 A")
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=at)
        self.assertIsNotNone(self.pane.zoom_window)
        self.assertTrue(self.wheel(-120, at))
        self.assertAlmostEqual(chart.end - chart.start, 60.0, places=5)
        self.pane.resume_button.click()
        self.assertIsNone(self.pane.zoom_window)
        self.assertIsNone(self.pane.snapshot)
        self.assertEqual(self.pane.scope.text(), "实时统计")

    def test_zoom_limit_fresh_points_and_selection_stay_in_time_range(self):
        pane = self.pane
        chart = pane.chart
        at = self.point(15)
        self.assertTrue(self.wheel(2400, at))
        self.assertAlmostEqual(chart.end - chart.start, 1.0, places=5)
        latest = Point(self.origin + 60, 10, 1, 5, 2)
        self.history.points.append(latest)
        pane.refresh()
        self.assertNotIn(latest, chart.points)
        bounds = chart.plot_rect()
        a = QPoint(round(bounds.center().x() - 40), round(bounds.center().y()))
        b = QPoint(round(bounds.center().x() + 40), round(bounds.center().y()))
        QTest.mousePress(chart, Qt.MouseButton.LeftButton, pos=b)
        QTest.mouseRelease(chart, Qt.MouseButton.LeftButton, pos=a)
        self.assertEqual(pane.scope.text(), "框选统计")
        self.assertTrue(chart.start <= pane.selection[0] < pane.selection[1] <= chart.end)
        self.assertTrue(self.wheel(-120, at))
        self.assertIsNone(pane.selection)
        self.assertEqual(pane.scope.text(), "缩放统计")


class RangeSummaryTests(unittest.TestCase):
    def test_clips_power_average_and_excludes_gaps_and_outside_peaks(self):
        points = [Point(0, 1000, 0, 100, 100), Point(2, 10, 0, 12, 0.5),
                  Point(3, 30, 0, None, 1.2), Point(7, 60, 1, 11, None),
                  Point(9, 1000, 1, 100, 100)]
        result = PowerHistory.summarize(points, 1, 8)
        self.assertEqual((result.volts, result.amps, result.peak_watts), (12, 1.2, 60))
        self.assertEqual((result.average.seconds, result.average.samples), (3, 3))
        self.assertAlmostEqual(result.average.watts, (257.5 + 20 + 295) / 3)
