import math
import time
from itertools import groupby
from operator import attrgetter

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from pc_client.history import save_csv


class SeriesChart(QWidget):
    def __init__(self, field, title, unit, color, digits, minimum_tick):
        super().__init__()
        self.value_of = attrgetter(field)
        self.title = title
        self.unit = unit
        self.color = QColor(color)
        self.digits = digits
        self.minimum_tick = minimum_tick
        self.time_axis = False
        self.setMinimumSize(360, 122)
        self.setMouseTracking(True)
        self.points = []
        self.start = self.end = 0.0
        self.hover = None
        self.plot_width = 0
        self.plot_segments = []
        self.setAccessibleName(f"输出{title}曲线")

    def set_data(self, points, start, end):
        self.points, self.start, self.end = points, start, end
        self.plot_width = 0
        self.update()

    def mouseMoveEvent(self, event):
        self.hover = event.position()
        self.update()

    def leaveEvent(self, _event):
        self.hover = None
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(48, 24, -38, -26 if self.time_axis else -10)
        if bounds.width() < 50 or self.start >= self.end:
            return
        segments = self.segments_for_width(bounds.width())
        peak = max((self.value_of(point) for segment in segments for point in segment), default=0)
        tick = next((step for step in (0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 25, 50)
                     if step >= self.minimum_tick and 4 * step >= peak), math.ceil(peak / 4))
        top = tick * 4
        span = self.end - self.start

        def position(point):
            return QPointF(
                bounds.left() + (point.time - self.start) / span * bounds.width(),
                bounds.bottom() - self.value_of(point) / top * bounds.height(),
            )

        painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
        painter.setPen(self.color)
        painter.drawText(QPointF(bounds.left(), 15), f"{self.title}  {self.unit}")
        painter.setFont(QFont("Segoe UI", 8))
        for index in range(5):
            y = bounds.bottom() - bounds.height() * index / 4
            painter.setPen(QPen(QColor("#deebf2"), 1))
            painter.drawLine(QPointF(bounds.left(), y), QPointF(bounds.right(), y))
            painter.setPen(QColor("#586d80"))
            painter.drawText(
                QRectF(0, y - 10, bounds.left() - 9, 20), Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignVCenter, f"{tick * index:g}",
            )
        wall_start = time.time() + (self.start - time.monotonic())
        for index in range(5):
            x = bounds.left() + index * bounds.width() / 4
            painter.setPen(QPen(QColor("#deebf2"), 1))
            painter.drawLine(QPointF(x, bounds.top()), QPointF(x, bounds.bottom()))
            if self.time_axis:
                label = time.strftime("%H:%M:%S", time.localtime(wall_start + index * span / 4))
                painter.setPen(QColor("#586d80"))
                painter.drawText(
                    QRectF(x - 33, bounds.bottom() + 4, 66, 19), Qt.AlignmentFlag.AlignCenter, label,
                )

        painter.save()
        painter.setClipRect(bounds.adjusted(-1, -1, 1, 1))
        self.draw_series(painter, bounds, position, segments)
        if not any(segment[-1].time >= self.start for segment in segments):
            painter.setFont(QFont("Microsoft YaHei UI", 10))
            painter.setPen(QColor("#586d80"))
            hint = f"暂无有效{self.title}读数" if self.points else "开启输出后开始记录"
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, hint)
        if self.hover is not None and bounds.contains(self.hover):
            self.draw_hover(painter, bounds, position, span)
        painter.restore()

    def draw_series(self, painter, bounds, position, segments):
        stroke = QPen(self.color, 1.8)
        gradient = QLinearGradient(0, bounds.top(), 0, bounds.bottom())
        shade = QColor(self.color)
        shade.setAlpha(52)
        gradient.setColorAt(0, shade)
        shade.setAlpha(4)
        gradient.setColorAt(1, shade)

        for points in segments:
            first, last = position(points[0]), position(points[-1])
            path = QPainterPath(first)
            for point in points[1:]:
                path.lineTo(position(point))
            fill = QPainterPath(path)
            fill.lineTo(last.x(), bounds.bottom())
            fill.lineTo(first.x(), bounds.bottom())
            fill.closeSubpath()
            painter.fillPath(fill, gradient)
            painter.strokePath(path, stroke)
            painter.setPen(stroke)
            painter.setBrush(self.color)
            painter.drawEllipse(last, 2, 2)

    def segments_for_width(self, width):
        if self.plot_width != width:
            segments = []
            for (_, missing), group in groupby(self.points, key=lambda p: (p.segment, self.value_of(p) is None)):
                if not missing:
                    points = list(group)
                    segments.append(self.points_for_pixels(points, width) if len(points) > 2 * width else points)
            self.plot_segments = segments
            self.plot_width = width
        return self.plot_segments

    def points_for_pixels(self, points, width):
        scale = width / (self.end - self.start)
        plotted = []
        for _, bucket in groupby(points, key=lambda point: math.floor((point.time - self.start) * scale)):
            column = list(bucket)
            if len(column) > 4:
                lowest = min(column, key=self.value_of)
                highest = max(column, key=self.value_of)
                column = sorted({column[0], lowest, highest, column[-1]}, key=lambda point: point.time)
            plotted.extend(column)
        return plotted

    def draw_hover(self, painter, bounds, position, span):
        t = self.start + span * (self.hover.x() - bounds.left()) / bounds.width()
        seen = (point for segment in self.plot_segments for point in segment if point.time >= self.start)
        point = min(seen, key=lambda item: abs(item.time - t), default=None)
        if point is None:
            return
        mark = position(point)
        painter.setPen(QPen(QColor("#9badbd"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(mark.x(), bounds.top()), QPointF(mark.x(), bounds.bottom()))
        painter.setPen(QPen(QColor("#ffffff"), 1.5))
        painter.setBrush(self.color)
        painter.drawEllipse(mark, 4.0, 4.0)
        box = QRectF(min(mark.x() + 10, bounds.right() - 85), bounds.top() + 8, 81, 25)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#143b51"))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, f"{self.value_of(point):.{self.digits}f} {self.unit}")


class HistoryPane(QWidget):
    def __init__(self, history, standalone=False, parent=None, sampling=None):
        super().__init__(parent)
        self.history = history
        self.dialog = None
        self._displayed_view = None
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
        self.charts = {}
        for field, name, unit, color, digits, minimum_tick in (
            ("volts", "电压", "V", "#2364b3", 2, 0.5),
            ("amps", "电流", "A", "#8b4a9e", 3, 0.05),
            ("watts", "功率", "W", "#097b70", 2, 0.5),
        ):
            self.view.addItem(f"{name}曲线", field)
            self.charts[field] = SeriesChart(field, name, unit, color, digits, minimum_tick)
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
        self.minutes.setToolTip("曲线和平均功率按同一时间范围计算")
        self.minutes.valueChanged.connect(self.refresh)
        filters.addWidget(self.minutes)
        layout.addLayout(filters)
        for chart in self.charts.values():
            layout.addWidget(chart, 1)
        self.view.currentIndexChanged.connect(self.refresh)

        footer = QHBoxLayout()
        footer.addWidget(QLabel("平均功率"))
        self.average = QLabel("— W")
        self.average.setProperty("role", "average")
        footer.addWidget(self.average)
        footer.addStretch()
        self.coverage = QLabel()
        self.coverage.setProperty("role", "muted")
        self.coverage.setToolTip("实时频率按最近 5 秒收到的新测量样本计算")
        footer.addWidget(self.coverage)
        layout.addLayout(footer)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        self.refresh()

    def refresh(self):
        observed_through = self.history.window_end(time.monotonic())
        duration = self.minutes.value() * 60
        start, end = observed_through - duration, observed_through
        if self.history.started_at is not None and start < self.history.started_at:
            start = self.history.started_at
            end = start + duration
        selected = self.view.currentData()
        latest = self.history.points[-1] if self.history.points else None
        rate = self.history.sample_rate(observed_through)
        displayed = (start, end, selected, self.history.active, latest, rate)
        if displayed == self._displayed_view:
            return
        self._displayed_view = displayed
        self.save_button.setEnabled(latest is not None)
        points = self.history.window(start, end)
        interval = 1500 if len(points) > 30000 else 500
        if self.refresh_timer.interval() != interval:
            self.refresh_timer.setInterval(interval)
        average = self.history.average(points, start, end)
        for field, chart in self.charts.items():
            visible = selected is None or field == selected
            chart.setVisible(visible)
            if visible:
                chart.time_axis = selected is not None or field == "watts"
                chart.set_data(points, start, end)
        self.average.setText("— W" if average.watts is None else f"{average.watts:.2f} W")
        rate_text = f"实际 {rate:.1f} Hz  ·  " if rate is not None else ""
        self.coverage.setText(f"{rate_text}{average.samples} 个样本  ·  有效 {average.seconds:.1f} 秒")

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
        layout.addWidget(pane)
        self.dialog.finished.connect(self.window_closed)
        self.dialog.show()

    def window_closed(self):
        self.dialog = None
