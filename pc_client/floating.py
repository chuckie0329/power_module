"""Compact topmost controls; the main window still owns the device session."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget


class FloatingPanel(QWidget):
    restore_requested = Signal()
    output_requested = Signal()
    relay_requested = Signal(int)

    def __init__(self):
        # A top-level tool window has no separate Windows taskbar button.
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("直流电源悬浮窗")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._drag_offset = None
        self._positioned = False
        self.setStyleSheet("""
            QFrame#floatingPanel {
                background: rgba(13, 35, 48, 222); border: 1px solid #426174; border-radius: 8px;
            }
            QFrame#floatingPanel QLabel { color: #ffffff; padding: 2px; font: 600 12px "Segoe UI"; }
            QFrame#floatingPanel QPushButton {
                color: #f5fbff; background: #294757; border: 1px solid #557282;
                border-radius: 4px; min-height: 22px; padding: 0px 5px; font-size: 11px;
            }
            QFrame#floatingPanel QPushButton:hover { background: #3d6276; }
            QFrame#floatingPanel QPushButton[energized="true"] { color: #fff1bc; background: #62491b; }
            QFrame#floatingPanel QPushButton[energized="true"]:hover { background: #785b25; }
            QFrame#floatingPanel QPushButton:disabled { color: #a4b4be; background: #233643; }
            QFrame#floatingPanel QPushButton:focus { border-color: #83d9ff; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(self)
        frame.setObjectName("floatingPanel")
        body = QVBoxLayout(frame)
        body.setContentsMargins(6, 5, 6, 5)
        body.setSpacing(3)
        outer.addWidget(frame)

        meters = QHBoxLayout()
        meters.setSpacing(2)
        self.values = {}
        samples = {}
        for key, unit, sample, name in (
            ("voltage", "V", " 40.00 V ", "输出电压"),
            ("current", "A", " 8.000 A ", "输出电流"),
            ("power", "W", " 100.0 W ", "输出功率"),
        ):
            meter = QLabel(f"— {unit}")
            meter.setAccessibleName(name)
            meter.setToolTip(f"{name}；拖动可移动窗口，双击可恢复主窗口")
            meter.setAlignment(Qt.AlignmentFlag.AlignCenter)
            meter.setCursor(Qt.CursorShape.SizeAllCursor)
            samples[key] = sample
            meters.addWidget(meter, 1)
            self.values[key] = (meter, unit)
        body.addLayout(meters)

        controls = QHBoxLayout()
        controls.setSpacing(3)
        self.output = QPushButton("输出 —")
        self.output.clicked.connect(self.output_requested)
        controls.addWidget(self.output, 2)
        self.relays = {}
        for channel in (1, 2):
            switch = QPushButton(f"K{channel} —")
            switch.setAccessibleName(f"继电器 K{channel}")
            switch.clicked.connect(lambda checked=False, ch=channel: self.relay_requested.emit(ch))
            controls.addWidget(switch, 1)
            self.relays[channel] = switch
        body.addLayout(controls)
        self._menu = QMenu(self)
        self._menu.addAction("恢复主窗口", self.restore_requested.emit)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda point: self._menu.popup(self.mapToGlobal(point)))
        # Size to content, not the platform style's full-width push-button hints.
        self.ensurePolished()
        for key, (meter, _) in self.values.items():
            meter.ensurePolished()
            meter.setFixedWidth(meter.fontMetrics().horizontalAdvance(samples[key]) + 5)
        for widget, captions in (
            (self.output, ("输出 开", "输出 关", "输出 —")),
            *((widget, (f"K{channel} 开", f"K{channel} 关", f"K{channel} —"))
              for channel, widget in self.relays.items()),
        ):
            widget.ensurePolished()
            widget.setFixedWidth(max(widget.fontMetrics().horizontalAdvance(caption) for caption in captions) + 14)
        self.setFixedSize(self.sizeHint())

    @staticmethod
    def _energized(button, on):
        if button.property("energized") != on:
            button.setProperty("energized", on)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def sync(self, values, output_state, output_control, relays):
        for key, (meter, unit) in self.values.items():
            meter.setText(f"{values[key]} {unit}")
        self.output.setText("输出 开" if output_state is True else "输出 关" if output_state is False else "输出 —")
        self.output.setToolTip(output_control.toolTip() or output_control.text())
        self.output.setAccessibleDescription(self.output.toolTip())
        self.output.setEnabled(output_control.isEnabled())
        self._energized(self.output, output_state is True)
        for channel, (source, state) in relays.items():
            button = self.relays[channel]
            button.setText(f"K{channel} " + ("开" if state is True else "关" if state is False else "—"))
            button.setToolTip(source.text())
            button.setAccessibleDescription(source.text())
            button.setEnabled(source.isEnabled())
            self._energized(button, state is True)

    def show_near(self, main_window):
        screen = main_window.screen() if not self._positioned else QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = main_window.screen()
            self._positioned = False
        if screen is not None:
            area = screen.availableGeometry()
            if not self._positioned:
                self.move(area.right() - self.width() - 11, area.top() + 12)
                self._positioned = True
            self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                      max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))
        self.show()
        self.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            self.restore_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def closeEvent(self, event):
        event.ignore()
        self.restore_requested.emit()
