import math
import time
from bisect import bisect_left
from datetime import datetime
from itertools import groupby
from operator import attrgetter

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from pc_client.history import PowerHistory, save_csv


SERIES = (
    ("volts", "电压", "V", "#2364b3", 2, 0.5),
    ("amps", "电流", "A", "#8b4a9e", 3, 0.05),
    ("watts", "功率", "W", "#097b70", 2, 0.5),
)
CENTER = Qt.AlignmentFlag.AlignCenter
MUTED = QColor("#586d80")


class OutputChart(QWidget):
    selection_started = Signal()
    selection_changed = Signal(float, float)
    selection_cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(365, 205)
        self.setMouseTracking(True)
        self.setAccessibleName("输出电压、电流和功率曲线")
        self.setToolTip("按住鼠标左键横向拖动，选取统计时间段。")
        self.points = []
        self.start = self.end = 0.0
        self.wall_offset = 0.0
        self.field = None
        self.selection = self.anchor = self.cursor = self.hover = None
        self._cached_plot = None

    def set_data(self, points, start, end, field, wall_offset):
        self.points, self.start, self.end = points, start, end
        self.field, self.wall_offset = field, wall_offset
        self._cached_plot = None
        self.update()

    def set_selection(self, selection):
        self.selection = selection
        self.update()

    def plot_rect(self):
        return QRectF(self.rect()).adjusted(47, 30, -91, -29)

    def time_at(self, x):
        bounds = self.plot_rect()
        fraction = min(1.0, max(0.0, (x - bounds.left()) / bounds.width()))
        return self.start + fraction * (self.end - self.start)

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton and self.points
                and self.start < self.end and self.plot_rect().contains(event.position())):
            self.hover = None
            self.anchor = self.cursor = event.position().x()
            self.selection_started.emit()
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.anchor is not None:
            self.cursor = event.position().x()
        else:
            self.hover = event.position()
        self.update()

    def leaveEvent(self, _event):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.anchor is None:
            super().mouseReleaseEvent(event)
            return
        anchor, x = self.anchor, event.position().x()
        self.anchor = self.cursor = None
        start, end = sorted((self.time_at(anchor), self.time_at(x)))
        if abs(x - anchor) >= 4 and start < end:
            self.selection_changed.emit(start, end)
        else:
            self.selection_cancelled.emit()
        self.update()
        event.accept()

    def abandon_drag(self):
        self.anchor = self.cursor = None
        self.update()

    def paintEvent(self, _event):
        bounds = self.plot_rect()
        if bounds.width() < 50 or self.start >= self.end:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self.prepare_plot(bounds.width())
        span = self.end - self.start

        def x_at(t):
            return bounds.left() + (t - self.start) / span * bounds.width()

        painter.setFont(QFont("Microsoft YaHei UI", 8))
        grid = QPen(QColor("#deebf2"), 1)
        for index in range(5):
            y = bounds.bottom() - bounds.height() * index / 4
            x = bounds.left() + index * bounds.width() / 4
            painter.setPen(grid)
            painter.drawLine(QPointF(bounds.left(), y), QPointF(bounds.right(), y))
            painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.bottom()))
            painter.setPen(MUTED)
            label = time.strftime("%H:%M:%S", time.localtime(self.wall_offset + self.start + index * span / 4))
            painter.drawText(QRectF(x - 32, bounds.bottom() + 4, 64, 19), CENTER, label)

        for axis, (spec, tick, segments) in plot.items():
            field, _, unit, color, _, _ = spec
            color = QColor(color)
            left = 0 if axis == 0 else bounds.right() + 3 + 43 * (axis - 1)
            labels = QRectF(left, 7, 43, 18)
            painter.setPen(color)
            painter.drawText(labels, CENTER, unit)
            for index in range(5):
                y = bounds.bottom() - bounds.height() * index / 4
                labels.moveTop(y - 9)
                painter.drawText(labels, CENTER, f"{tick * index:g}")
            painter.save()
            painter.setClipRect(bounds.adjusted(-1, -1, 1, 1))
            stroke = QPen(color, 1.8)
            for points in segments:
                coords = (QPointF(x_at(p.time), bounds.bottom() - getattr(p, field) / (4 * tick)
                                  * bounds.height()) for p in points)
                first = next(coords)
                path = QPainterPath(first)
                last = first
                for last in coords:
                    path.lineTo(last)
                # strokePath never fills the polygon between the first and last samples.
                # drawPath would implicitly close and fill an open path when a brush is active.
                painter.strokePath(path, stroke)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(last, 2, 2)
            painter.restore()

        if not any(segment[-1].time >= self.start for _, _, segments in plot.values() for segment in segments):
            painter.setPen(MUTED)
            painter.drawText(bounds, CENTER, "暂无有效读数" if self.points else "开启输出后开始记录")
        region = self.selection
        if self.anchor is not None:
            region = sorted((self.time_at(self.anchor), self.time_at(self.cursor)))
        if region is not None:
            a, b = map(x_at, region)
            painter.fillRect(QRectF(a, bounds.top(), b - a, bounds.height()), QColor(35, 100, 179, 44))
            painter.setPen(QPen(QColor("#2364b3"), 1, Qt.PenStyle.DashLine))
            for x in (a, b):
                painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.bottom()))
        if self.anchor is None and self.hover is not None and bounds.contains(self.hover):
            self.draw_hover(painter, bounds, x_at)

    def draw_hover(self, painter, bounds, x_at):
        at = self.time_at(self.hover.x())
        index = bisect_left(self.points, at, key=attrgetter("time"))
        candidates = (p for p in self.points[max(0, index - 1):index + 1] if self.start <= p.time <= self.end)
        point = min(candidates, key=lambda p: abs(p.time - at), default=None)
        if point is None or abs(x_at(point.time) - self.hover.x()) > 24:
            return
        x = x_at(point.time)
        painter.setPen(QPen(QColor("#9badbd"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.bottom()))
        text = "   ".join(f"{getattr(point, field):.{digits}f} {unit}"
                          for field, _, unit, _, digits, _ in SERIES
                          if self.field in (None, field) and getattr(point, field) is not None)
        if not text:
            return
        width = painter.fontMetrics().horizontalAdvance(text) + 16
        left = max(bounds.left() + 2, min(x + 8, bounds.right() - width - 2))
        box = QRectF(left, bounds.top() + 5, width, 21)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#143b51"))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(box, CENTER, text)

    def prepare_plot(self, width):
        if self._cached_plot is not None and self._cached_plot[0] == width:
            return self._cached_plot[1]
        plot = {}
        for axis, spec in enumerate(SERIES):
            field, _, _, _, _, minimum_tick = spec
            if self.field is not None and self.field != field:
                continue
            value = attrgetter(field)
            segments = []
            for (_, missing), group in groupby(self.points, key=lambda p: (p.segment, value(p) is None)):
                if not missing:
                    points = list(group)
                    if len(points) > 2 * width:
                        points = self.points_for_pixels(points, width, value)
                    segments.append(points)
            peak = max((value(point) for segment in segments for point in segment), default=0)
            tick = next((step for step in (0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 25, 50)
                         if step >= minimum_tick and 4 * step >= peak), math.ceil(peak / 4))
            plot[axis] = spec, tick, segments
        self._cached_plot = width, plot
        return plot

    def points_for_pixels(self, points, width, value):
        scale = width / (self.end - self.start)
        plotted = []
        for _, bucket in groupby(points, key=lambda p: math.floor((p.time - self.start) * scale)):
            column = list(bucket)
            if len(column) > 4:
                lowest, highest = min(column, key=value), max(column, key=value)
                column = sorted({column[0], lowest, highest, column[-1]}, key=attrgetter("time"))
            plotted.extend(column)
        return plotted


class HistoryPane(QWidget):
    def __init__(self, history, standalone=False, parent=None, sampling=None):
        super().__init__(parent)
        self.history = history
        self.dialog = None
        self._displayed_view = None
        self.snapshot = self.live_frame = self.selection = None
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        controls = QHBoxLayout()
        title = QLabel("输出曲线")
        title.setProperty("role", "section")
        controls.addWidget(title)
        controls.addStretch()
        if sampling is not None:
            caption = QLabel("采样频率")
            caption.setBuddy(sampling)
            controls.addWidget(caption)
            controls.addWidget(sampling)
        self.save_button = QPushButton("保存数据")
        self.save_button.clicked.connect(self.save_data)
        controls.addWidget(self.save_button)
        if not standalone:
            expand = QPushButton("独立窗口")
            expand.clicked.connect(self.open_window)
            controls.addWidget(expand)
        layout.addLayout(controls)

        self.view = QComboBox()
        self.view.setAccessibleName("曲线视图")
        self.view.addItem("全部曲线", None)
        for field, name, *_ in SERIES:
            self.view.addItem(f"{name}曲线", field)
        filters = QHBoxLayout()
        filters.addWidget(self.view)
        filters.addStretch()
        filters.addWidget(QLabel("最近"))
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 120)
        self.minutes.setValue(5)
        self.minutes.setSuffix(" 分钟")
        self.minutes.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.minutes.setKeyboardTracking(False)
        self.minutes.setToolTip("设定实时曲线显示的时间范围")
        self.minutes.valueChanged.connect(self.refresh)
        filters.addWidget(self.minutes)
        layout.addLayout(filters)
        body = QHBoxLayout()
        self.chart = OutputChart()
        body.addWidget(self.chart, 1)
        self.chart.selection_started.connect(self.freeze)
        self.chart.selection_changed.connect(self.select_range)
        self.chart.selection_cancelled.connect(self.cancel_drag)
        stats = QVBoxLayout()
        stats.setSpacing(6)
        self.scope = QLabel("实时统计")
        self.scope.setProperty("role", "section")
        stats.addWidget(self.scope)
        self.range_text = QLabel()
        self.range_text.setProperty("role", "muted")
        self.range_text.setWordWrap(True)
        stats.addWidget(self.range_text)
        self.values = {}
        for key, name, color in (
            ("volts", "最大电压", SERIES[0][3]), ("amps", "最大电流", SERIES[1][3]),
            ("peak_watts", "最大功率", SERIES[2][3]), ("average_watts", "平均功率", SERIES[2][3]),
        ):
            label = QLabel(name)
            label.setStyleSheet(f"color: {color};")
            stats.addWidget(label)
            self.values[key] = QLabel("—")
            self.values[key].setProperty("role", "average")
            self.values[key].setAccessibleName(name)
            stats.addWidget(self.values[key])
        self.coverage = QLabel()
        self.coverage.setProperty("role", "muted")
        self.coverage.setWordWrap(True)
        self.coverage.setToolTip("有效时长不含断线或缺测；实际采样频率按最近 5 秒计算。")
        stats.addWidget(self.coverage)
        stats.addStretch()
        self.resume_button = QPushButton("恢复实时")
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self.resume)
        stats.addWidget(self.resume_button)
        side = QWidget()
        side.setFixedWidth(142)
        side.setLayout(stats)
        body.addWidget(side)
        layout.addLayout(body, 1)
        self.view.currentIndexChanged.connect(self.refresh)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        self.refresh()

    def freeze(self):
        if self.snapshot is None:
            self.snapshot = self.live_frame
            self.minutes.setEnabled(False)
            self.resume_button.setEnabled(True)

    def select_range(self, start, end):
        self.selection = (start, end)
        self.chart.set_selection(self.selection)
        self.refresh()

    def cancel_drag(self):
        if self.selection is None:
            self.resume()

    def resume(self):
        self.snapshot = self.selection = None
        self.chart.abandon_drag()
        self.chart.set_selection(None)
        self.minutes.setEnabled(True)
        self.resume_button.setEnabled(False)
        self.refresh()

    def refresh(self):
        latest = self.history.points[-1] if self.history.points else None
        self.save_button.setEnabled(latest is not None)
        if self.snapshot is None:
            observed = self.history.window_end(time.monotonic())
            duration = self.minutes.value() * 60
            start, end = observed - duration, observed
            if self.history.started_at is not None and start < self.history.started_at:
                start = self.history.started_at
                end = start + duration
            rate = self.history.sample_rate(observed)
            live_key = (start, end, latest, rate)
            if self.live_frame is None or live_key != self.live_frame[0]:
                self.live_frame = (live_key, self.history.window(start, end), time.time() - time.monotonic())
        frame = self.snapshot if self.snapshot is not None else self.live_frame
        (start, end, _, rate), points, wall_offset = frame
        field = self.view.currentData()
        previous = self._displayed_view
        if previous is not None and previous[0] is frame and previous[1] == field:
            if previous[2] == self.selection:
                return
        else:
            self.chart.set_data(points, start, end, field, wall_offset)
        self._displayed_view = (frame, field, self.selection)
        interval = 1500 if len(points) > 30000 else 500
        if self.refresh_timer.interval() != interval:
            self.refresh_timer.setInterval(interval)

        a, b = self.selection or (start, end)
        self.scope.setText("框选统计" if self.selection else "实时统计")
        time_label = "—"
        if points:
            fmt = "%H:%M:%S.%f" if self.selection else "%H:%M:%S"
            labels = [datetime.fromtimestamp(wall_offset + t).strftime(fmt) for t in (a, b)]
            if self.selection:
                labels = [label[:-3] for label in labels]
            time_label = " 至\n".join(labels)
        self.range_text.setText(time_label)
        summary = PowerHistory.summarize(points, a, b)
        for key, value, unit, digits in (
            ("volts", summary.volts, "V", 2), ("amps", summary.amps, "A", 3),
            ("peak_watts", summary.peak_watts, "W", 2), ("average_watts", summary.average.watts, "W", 2),
        ):
            self.values[key].setText(f"— {unit}" if value is None else f"{value:.{digits}f} {unit}")
        average = summary.average
        coverage = f"{average.samples} 个样本 · 有效 {average.seconds:.1f} 秒"
        if rate is not None and not self.selection:
            coverage += f"\n实际 {rate:.1f} Hz"
        self.coverage.setText(coverage)

    def save_data(self):
        points = tuple(self.history.points)
        if not points:
            return
        wall_time_offset = time.time() - time.monotonic()
        default_name = time.strftime("电源曲线_%Y%m%d_%H%M%S.csv")
        dialog = QFileDialog(self, "保存曲线数据", "", "CSV 文件 (*.csv)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix("csv")
        dialog.selectFile(default_name)
        filename = dialog.selectedFiles()[0] if dialog.exec() == QDialog.DialogCode.Accepted else None
        dialog.deleteLater()
        if filename is None:
            return
        try:
            save_csv(filename, points, wall_time_offset)
        except OSError as error:
            QMessageBox.critical(self, "保存失败", f"无法保存曲线数据。{error.strerror or error}")
            return
        QMessageBox.information(self, "数据已保存", f"已保存 {len(points)} 个样本。\n{filename}")

    def open_window(self):
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        self.dialog = QDialog(self)
        self.dialog.setWindowTitle("输出曲线")
        self.dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.dialog.resize(780, 560)
        layout = QVBoxLayout(self.dialog)
        layout.setContentsMargins(20, 18, 20, 18)
        pane = HistoryPane(self.history, standalone=True)
        pane.minutes.setValue(self.minutes.value())
        pane.view.setCurrentIndex(self.view.currentIndex())
        if self.selection is not None:
            pane.snapshot = self.snapshot
            pane.minutes.setEnabled(False)
            pane.resume_button.setEnabled(True)
            pane.select_range(*self.selection)
        layout.addWidget(pane)
        self.dialog.finished.connect(self.window_closed)
        self.dialog.show()

    def window_closed(self):
        self.dialog = None
