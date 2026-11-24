from html import escape
import sys
from threading import Event
import time

from PySide6.QtCore import QLocale, QSettings, QSignalBlocker, Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

from pc_client.chart import HistoryPane
from pc_client.connection import BleLink, LinkWorker, firmware_version, retains_outputs, usb_ports
from pc_client.firmware_update import FirmwareImage, MIN_FIRMWARE_VERSION
from pc_client.floating import FloatingPanel
from pc_client.history import PowerHistory, normalize_measurements
from pc_client.theme import STYLES, icon


GROUP_NOTE_ROLE = Qt.ItemDataRole.UserRole + 1
MAX_OUTPUT_MW = 75000
DEFAULT_SAMPLING_PERIOD_MS = 50
SAMPLING_PERIODS = (("0.5 Hz", 2000), ("1 Hz", 1000), ("2 Hz", 500), ("5 Hz", 200),
                    ("10 Hz", 100), ("20 Hz", 50), ("30 Hz", 33))

ERRORS = {
    "REV_CONFLICT": "设备设定刚刚变化。请核对读回后重新应用。",
    "RANGE": "参数超出允许范围。",
    "BAD_STEP": "请按参数输入框的步进设置。",
    "POWER_LIMIT": "电压乘限流超出 75 W 设定上限。",
    "INPUT_UNVERIFIED": "SX 输入电压超出允许范围。",
    "SX_CONFIG": "请关闭当前组的上电输出，并将 OPP 设在允许范围内。",
    "OPP_RANGE": "OPP 超出当前固件的允许范围。",
    "SX_TIMEOUT": "SX150C 响应超时，主输出状态待确认。",
    "READBACK_MISMATCH": "SX150C 读回与设定不一致。",
    "MODULE_PROTECT": "SX150C 正处于保护状态。",
    "GROUP_RECALL_ON": "目标组会在调出时开启输出。请在 SX150C 面板关闭该组的 M-OUT 后再切换。",
    "OUTPUT_MUST_BE_OFF": "请关闭主输出后应用保护阈值。",
    "OCP_UNIT_UNKNOWN": "请先用面板上的 OCP 阈值校对单位。",
    "OCP_UNIT_MISMATCH": "面板输入值与设备读回不匹配，请核对当前组的 OCP 阈值。",
    "REPLY_TIMEOUT": "等待设备应答超时。",
    "REARM_REQUIRED": "新设定已写入，主输出已关闭。确认设定后可重新开启。",
    "BUSY": "设备正在处理上一条命令。",
    "BAD_OP": "设备固件尚未提供此功能。",
    "BAD_VERSION": "该端口返回了不兼容的协议。",
    "INITIALIZING": "等待 SX150C 完成状态同步。",
    "LINK_ERROR": "设备连接已失效。",
    "BLE_AUTH_REQUIRED": "蓝牙认证失败。请在 Windows「蓝牙和其他设备」中配对电源，输入 PIN 112233 后重新连接。",
    "FIRMWARE_UPDATE": "当前固件可读取状态，更新至 v1.1.0 后可直接控制。",
    "BAD_STREAM_PERIOD": "设备不支持这个采样频率。",
    "NVS_ERROR": "设备配置保存失败。",
    "CANCELLED": "前一操作已中断。",
    "OTA_BAD_IMAGE": "请选择校验通过的 PowerModule ESP32 应用固件（.bin）。",
    "OTA_NO_SLOT": "设备没有能容纳此固件的备用应用分区。",
    "OTA_OUTPUT": "请确认主输出和两路继电器均已关闭，再进行升级。",
    "OTA_BUSY": "设备正在升级或等待验证。",
    "OTA_CANCELLED": "固件升级已取消。",
    "OTA_FLASH": "固件写入失败。",
    "OTA_CHECKSUM": "固件校验失败，请重新选择升级文件。",
    "OTA_BAD_OFFSET": "固件传输位置不一致，请重试升级。",
    "OTA_INCOMPLETE": "固件尚未传输完整。",
    "OTA_INACTIVE": "本次固件传输已结束，请重新开始升级。",
    "OTA_PROTOCOL": "设备返回了不兼容的升级参数。",
    "OTA_DEVICE": "重连的设备与升级目标不一致。",
    "OTA_BOOT": "未能确认新固件已启动。设备会在启动确认超时后尝试恢复。",
}


def error_text(value):
    return ERRORS.get(value, value)


def label(text, role=None):
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    return widget


def button(text, action, role=None):
    widget = QPushButton(text)
    if role:
        widget.setProperty("role", role)
    widget.clicked.connect(action)
    return widget


def dynamic(widget, name, value):
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def spin(low, high, step, unit, digits):
    widget = QDoubleSpinBox()
    widget.setDecimals(digits)
    widget.setRange(low, high)
    widget.setSingleStep(step)
    widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    widget.setSuffix(f" {unit}")
    widget.setKeyboardTracking(False)
    widget.setLocale(QLocale("en_US"))
    return widget


def card(title=None):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(15, 12, 15, 12)
    layout.setSpacing(7)
    if title:
        layout.addWidget(label(title, "section"))
    return frame, layout


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("直流电源控制台")
        self.setMinimumSize(990, 690)
        self.resize(1200, 810)
        self.worker = LinkWorker()
        self.worker.connected.connect(self.connected)
        self.worker.info_updated.connect(self.update_info)
        self.worker.disconnected.connect(self.disconnected)
        self.worker.state.connect(self.state_read)
        self.worker.finished.connect(self.finished)
        self.worker.notice.connect(self.noticed)
        self.worker.update_progress.connect(self.show_update_progress)
        self.history = PowerHistory()
        self.group_settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                                        "PowerModule", "PowerModule-PC")
        self.info = {}
        self.state = {}
        self.jobs = set()
        self.kind = "usb"
        self.exit_requested = False
        self.update_cancel = Event()
        self.last_receipt = 0.0
        self.last_device = None
        self.current_group = None
        self.output_requests_off = False
        self.output_dirty = self.protection_dirty = self.opp_dirty = False
        self.floating = None
        self.tray = None
        self.hidden_chart_dialog = None

        page = QWidget()
        page.setObjectName("page")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header())
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("page")
        content = QVBoxLayout(body)
        content.setContentsMargins(18, 13, 18, 12)
        content.setSpacing(11)
        content.addLayout(self.meters())
        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addWidget(self.controls())
        chart_card, chart_layout = card()
        self.sampling = QComboBox()
        self.sampling.setAccessibleName("采样频率")
        self.requested_period_ms = DEFAULT_SAMPLING_PERIOD_MS
        self.refresh_sampling_options()
        self.sampling.currentIndexChanged.connect(self.sampling_changed)
        self.period_ms = 500
        self.history_pane = HistoryPane(self.history, sampling=self.sampling)
        chart_layout.addWidget(self.history_pane)
        columns.addWidget(chart_card, 1)
        content.addLayout(columns, 1)
        self.update_panel = QWidget()
        update_row = QHBoxLayout(self.update_panel)
        update_row.setContentsMargins(0, 0, 0, 0)
        self.update_bar = QProgressBar()
        self.update_bar.setRange(0, 100)
        self.update_bar.setAccessibleName("固件传输进度")
        self.update_cancel_button = button("取消升级", self.cancel_firmware_update)
        update_row.addWidget(self.update_bar, 1)
        update_row.addWidget(self.update_cancel_button)
        self.update_panel.hide()
        content.addWidget(self.update_panel)
        self.message = label("请选择设备连接方式。")
        self.message.setObjectName("message")
        self.message.setWordWrap(True)
        content.addWidget(self.message)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.setCentralWidget(page)
        self.refresh_targets()
        self.render()
        watch = QTimer(self)
        watch.setInterval(500)
        watch.timeout.connect(self.render)
        watch.start()

    def header(self):
        frame = QFrame()
        frame.setObjectName("header")
        header = QVBoxLayout(frame)
        header.setContentsMargins(19, 13, 19, 11)
        header.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label("直流电源", "title"))
        row.addStretch()
        self.transport = QComboBox()
        self.transport.addItem("USB 有线", "usb")
        self.transport.addItem("蓝牙 BLE", "ble")
        self.transport.setAccessibleName("连接方式")
        self.transport.currentIndexChanged.connect(self.transport_changed)
        row.addWidget(self.transport)
        self.target = QComboBox()
        self.target.setMinimumWidth(220)
        self.target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.target.setMinimumContentsLength(18)
        self.target.setAccessibleName("设备或串口")
        self.target.currentIndexChanged.connect(self.render)
        row.addWidget(self.target)
        self.refresh = button("刷新", self.refresh_targets)
        row.addWidget(self.refresh)
        self.connect_button = button("连接", self.toggle_connection, "primary")
        row.addWidget(self.connect_button)
        self.update_button = button("固件升级", self.start_firmware_update)
        row.addWidget(self.update_button)
        self.float_button = button("悬浮", self.show_floating)
        row.addWidget(self.float_button)
        header.addLayout(row)
        details = QHBoxLayout()
        self.link_badge = label("未连接", "pill")
        details.addWidget(self.link_badge)
        self.device_line = label("", "muted")
        details.addWidget(self.device_line, 1)
        self.output_badge = label("主输出状态未知", "pill")
        details.addWidget(self.output_badge)
        header.addLayout(details)
        return frame

    def show_floating(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.announce("系统托盘不可用，无法进入悬浮模式。", True)
            return
        if self.jobs & {"固件升级", "disconnect"} or self.exit_requested:
            return
        if self.floating is None:
            self.floating = FloatingPanel()
            self.floating.restore_requested.connect(self.restore_main)
            self.floating.output_requested.connect(self.toggle_floating_output)
            self.floating.relay_requested.connect(self.toggle_floating_relay)
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip("直流电源控制台")
            self.tray_menu = QMenu(self)
            self.tray_menu.addAction("恢复主窗口", self.restore_main)
            self.tray.setContextMenu(self.tray_menu)
            self.tray.activated.connect(self.tray_activated)
        self.render()
        self.tray.show()
        self.floating.show_near(self)
        dialog = self.history_pane.dialog
        if dialog is not None and dialog.isVisible():
            self.hidden_chart_dialog = dialog
            dialog.hide()
        self.hide()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore_main()

    def restore_main(self):
        self.show()
        if self.isMinimized():
            self.showNormal()
        if self.hidden_chart_dialog is not None:
            if self.hidden_chart_dialog is self.history_pane.dialog:
                self.hidden_chart_dialog.show()
            self.hidden_chart_dialog = None
        self.raise_()
        self.activateWindow()
        if self.floating is not None:
            self.floating.hide()
            self.tray.hide()

    def toggle_floating_output(self):
        self.render()
        if self.floating.output.isEnabled():
            self.toggle_output()

    def toggle_floating_relay(self, channel):
        self.render()
        if self.floating.relays[channel].isEnabled():
            self.toggle_relay(channel)

    def meters(self):
        row = QHBoxLayout()
        row.setSpacing(11)
        self.meter_values = {}
        for name, caption, unit in (
            ("voltage", "输出电压", "V"), ("current", "输出电流", "A"), ("power", "输出功率", "W"),
        ):
            frame = QFrame()
            frame.setObjectName("meter")
            if name == "power":
                frame.setProperty("accent", True)
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(15, 8, 15, 8)
            layout.setSpacing(0)
            layout.addWidget(label(caption, "muted"))
            value_row = QHBoxLayout()
            value_row.setSpacing(9)
            self.meter_values[name] = label("—", "metric")
            self.meter_values[name].setAccessibleName(caption)
            value_row.addWidget(self.meter_values[name], 0, Qt.AlignmentFlag.AlignBaseline)
            value_row.addWidget(label(unit, "unit"), 0, Qt.AlignmentFlag.AlignBaseline)
            value_row.addStretch()
            layout.addLayout(value_row)
            row.addWidget(frame, 1)
        return row

    def controls(self):
        left = QWidget()
        left.setFixedWidth(374)
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        output, out = card()
        output_title = QHBoxLayout()
        output_title.addWidget(label("输出设定", "section"))
        self.group_status = label("当前组 —", "muted")
        self.group_status.setTextFormat(Qt.TextFormat.PlainText)
        self.group_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.group_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        output_title.addWidget(self.group_status, 1)
        out.addLayout(output_title)

        group_row = QHBoxLayout()
        self.group_selector = QComboBox()
        self.group_selector.setAccessibleName("目标数据组")
        self.group_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.group_selector.setMinimumContentsLength(7)
        for group in range(11):
            self.group_selector.addItem(f"M{group}", group)
        self.group_selector.currentIndexChanged.connect(self.render)
        group_row.addWidget(self.group_selector, 1)
        self.group_edit = button("名称和备注", self.edit_group_details)
        self.group_edit.setToolTip("编辑下拉列表中选中的数据组")
        group_row.addWidget(self.group_edit)
        self.switch_group = button("切换数据组", self.select_group)
        group_row.addWidget(self.switch_group)
        out.addLayout(group_row)
        self.group_note = label("", "muted")
        self.group_note.setTextFormat(Qt.TextFormat.PlainText)
        self.group_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        out.addWidget(self.group_note)
        self.voltage = spin(0.50, 40, 0.01, "V", 2)
        self.limit = spin(0, 8, 0.01, "A", 3)
        self.voltage.setValue(3.30)
        self.limit.setValue(2.000)
        self.voltage.valueChanged.connect(self.mark_output)
        self.limit.valueChanged.connect(self.mark_output)
        out.addLayout(self.fields(("电压", self.voltage), ("限流", self.limit)))
        self.setpoint = label("等待设定值读回", "muted")
        out.addWidget(self.setpoint)
        out_row = QHBoxLayout()
        self.apply_output = button("应用设定", self.apply_setpoint)
        self.output_switch = button("开启输出", self.toggle_output, "primary")
        out_row.addWidget(self.apply_output, 1)
        out_row.addWidget(self.output_switch, 1)
        out.addLayout(out_row)
        layout.addWidget(output)

        protections, protect = card()
        self.protection_title = label("保护阈值", "section")
        protect.addWidget(self.protection_title)
        self.ovp = spin(0.50, 42, 0.01, "V", 2)
        self.ocp = spin(0.001, 8.2, 0.001, "A", 3)
        self.opp = spin(0.1, MAX_OUTPUT_MW / 1000, 0.1, "W", 1)
        self.ovp.setValue(42)
        self.ocp.setValue(8.2)
        self.opp.setValue(MAX_OUTPUT_MW / 1000)
        self.ovp.valueChanged.connect(self.mark_protection)
        self.ocp.valueChanged.connect(self.mark_protection)
        self.opp.valueChanged.connect(self.mark_opp)
        protect.addLayout(self.fields(("过压 OVP", self.ovp), ("过流 OCP", self.ocp), ("过功率 OPP", self.opp)))
        self.protection_readback = label("等待阈值读回。", "muted")
        protect.addWidget(self.protection_readback)
        self.protection_hint = label("", "muted")
        self.protection_hint.setWordWrap(True)
        protect.addWidget(self.protection_hint)
        protect_row = QHBoxLayout()
        self.match = button("校对 OCP", self.match_ocp)
        self.apply_protection = button("应用 OVP/OCP", self.set_protection)
        self.apply_opp = button("应用 OPP", self.set_opp)
        protect_row.addWidget(self.match)
        protect_row.addWidget(self.apply_protection, 1)
        protect_row.addWidget(self.apply_opp)
        protect.addLayout(protect_row)
        layout.addWidget(protections)

        relays, relay_layout = card("继电器")
        relay_row = QHBoxLayout()
        self.relay = {
            i: button(f"K{i} · 状态未知", lambda _checked=False, channel=i: self.toggle_relay(channel))
            for i in (1, 2)
        }
        for item in self.relay.values():
            relay_row.addWidget(item, 1)
        relay_layout.addLayout(relay_row)
        layout.addWidget(relays)
        layout.addStretch(1)
        return left

    @staticmethod
    def fields(*pairs):
        grid = QGridLayout()
        grid.setHorizontalSpacing(11)
        grid.setVerticalSpacing(3)
        for column, (caption, widget) in enumerate(pairs):
            grid.addWidget(label(caption, "muted"), 0, column)
            widget.setAccessibleName(caption)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            grid.addWidget(widget, 1, column)
        return grid

    def announce(self, text, error=False):
        self.message.setText(text)
        dynamic(self.message, "level", "error" if error else "")
        if error and self.floating is not None and self.floating.isVisible():
            self.tray.showMessage("直流电源", text, QSystemTrayIcon.MessageIcon.Warning)

    def noticed(self, title, detail):
        self.announce(f"{title}。{error_text(detail)}", True)

    def launch(self, action, coroutine):
        self.jobs.add(action)
        self.worker.submit(action, coroutine)
        self.render()

    def refresh_targets(self):
        if self.info or "connect" in self.jobs or "scan" in self.jobs:
            return
        if self.kind == "ble":
            self.announce("正在扫描 BLE 设备…")
            self.launch("scan", BleLink.scan())
            return
        old = self.target.currentData()
        with QSignalBlocker(self.target):
            self.target.clear()
            for description, name in usb_ports():
                self.target.addItem(description, name)
            if (index := self.target.findData(old)) >= 0:
                self.target.setCurrentIndex(index)
        self.render()

    def transport_changed(self):
        self.kind = self.transport.currentData()
        with QSignalBlocker(self.target):
            self.target.clear()
        self.refresh.setText("扫描" if self.kind == "ble" else "刷新")
        self.refresh_sampling_options()
        self.refresh_targets()

    def toggle_connection(self):
        if self.info:
            self.announce("正在断开…")
            self.launch("disconnect", self.worker.client.close())
        elif self.target.currentData() is not None:
            self.announce("正在读取设备信息…")
            self.launch("connect", self.worker.client.connect(self.kind, self.target.currentData()))

    def start_firmware_update(self):
        if self.jobs or firmware_version(self.info) < MIN_FIRMWARE_VERSION:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "选择升级固件", "", "PowerModule 固件 (*.bin)")
        if not filename:
            return
        try:
            image = FirmwareImage.load(filename)
        except ValueError as error:
            self.announce(error_text(str(error)), True)
            return
        except OSError:
            self.announce("无法读取选择的固件文件。", True)
            return
        prompt = (f"为 {self.info['device_id']} 安装 v{image.version}。"
                  f"当前版本为 v{self.info['firmware']}。\n"
                  "请关闭主输出和两路继电器。升级校验完成后设备会重启。")
        yes = QMessageBox.StandardButton.Yes
        no = QMessageBox.StandardButton.No
        if QMessageBox.question(self, "安装固件", prompt, yes | no, no) != yes:
            return
        self.update_cancel = Event()
        self.launch("固件升级", self.worker.client.update_firmware(image, self.update_cancel.is_set))
        self.show_update_progress(0, len(image.data), "transfer")

    def cancel_firmware_update(self):
        self.update_cancel.set()
        self.update_cancel_button.setEnabled(False)

    @Slot(int, int, str)
    def show_update_progress(self, current, total, stage):
        if "固件升级" not in self.jobs:
            return
        self.update_bar.setValue(current * 100 // total)
        self.update_cancel_button.setEnabled(stage == "transfer" and not self.update_cancel.is_set())
        self.update_panel.show()
        if self.update_cancel.is_set() and stage == "transfer":
            self.announce("正在取消固件升级…")
        else:
            self.announce("正在传输固件…" if stage == "transfer" else "正在校验固件并等待设备重启…")

    @Slot(object, object)
    def connected(self, info, state):
        if self.last_device and self.last_device != info["device_id"]:
            self.history.clear()
        self.last_device = info["device_id"]
        self.info = info
        self.period_ms = info.get("stream_period_ms", 500)
        self.refresh_sampling_options()
        self.refresh_opp_range()
        self.current_group = None
        self.output_dirty = self.protection_dirty = self.opp_dirty = False
        self.load_group_details()
        self.state_read(state, time.monotonic(), False)
        if "固件升级" not in self.jobs:
            self.announce("设备已连接，可以直接操作。" if info.get("direct_control") else ERRORS["FIRMWARE_UPDATE"])
        self.update_sampling()

    @Slot(str)
    def disconnected(self, reason):
        hold = retains_outputs(self.info)
        self.info, self.state = {}, {}
        self.refresh_sampling_options()
        self.current_group = None
        self.history.pause()
        self.load_group_details()
        if "固件升级" not in self.jobs:
            message = reason.rstrip("。") + "。" if reason else "设备已断开。"
            if hold:
                message += "主输出和继电器由设备保持。"
            self.announce(message, bool(reason))
        self.render()
        if self.exit_requested and not self.jobs & {"disconnect", "固件升级"}:
            self.close()

    @Slot(object, float, bool)
    def state_read(self, state, when, streamed):
        measured = normalize_measurements(state.get("measured", {}), self.info)
        self.state = {**state, "measured": measured}
        self.last_receipt = when
        applied = state.get("applied", {})
        group = applied.get("group")
        if measured.get("fresh") and isinstance(group, int) and group != self.current_group:
            self.current_group = group
            self.history.pause()
            for widget in (self.voltage, self.limit, self.ovp, self.ocp, self.opp):
                if widget.hasFocus():
                    widget.clearFocus()
            self.output_dirty = self.protection_dirty = self.opp_dirty = False
            with QSignalBlocker(self.group_selector):
                self.group_selector.setCurrentIndex(self.group_selector.findData(group))
        self.history.record(self.state, when, streamed=streamed)
        self.render()

    @Slot(object)
    def update_info(self, info):
        self.info = info
        self.period_ms = info.get("stream_period_ms", 500)
        self.refresh_sampling_options()
        self.refresh_opp_range()
        self.update_sampling()
        self.render()

    def refresh_opp_range(self):
        maximum = 100.0 if firmware_version(self.info) >= (1, 1, 3) else 75.0
        if self.opp.maximum() != maximum:
            with QSignalBlocker(self.opp):
                self.opp.setMaximum(maximum)

    def refresh_sampling_options(self):
        minimum = self.info.get("min_stream_period_ms", 200)
        if not self.info:
            minimum = SAMPLING_PERIODS[-1][1]
        elif self.kind == "ble" and firmware_version(self.info) < (1, 1, 6):
            minimum = max(minimum, 500)
        with QSignalBlocker(self.sampling):
            self.sampling.clear()
            if self.kind == "ble" and minimum == 2500:
                self.sampling.addItem("0.4 Hz", 2500)
            for label, period in SAMPLING_PERIODS:
                if period >= minimum:
                    self.sampling.addItem(label, period)
            index = self.sampling.findData(self.requested_period_ms)
            self.sampling.setCurrentIndex(index if index >= 0 else self.sampling.count() - 1)

    def sampling_changed(self):
        self.requested_period_ms = self.sampling.currentData()
        self.update_sampling()

    def update_sampling(self):
        period = self.sampling.currentData()
        if (self.info.get("stream_period") and period is not None and period != self.period_ms
                and not self.jobs & {"采样频率", "固件升级"}):
            self.launch("采样频率", self.worker.client.call("set_stream_period", {"period_ms": period}))

    @Slot(str, object, str)
    def finished(self, action, result, error):
        self.jobs.discard(action)
        if action == "固件升级":
            self.update_panel.hide()
            if error:
                self.announce(error_text(error), error != "OTA_CANCELLED")
            else:
                self.announce(f"固件 v{result['firmware']} 已启动并确认。")
                self.update_sampling()
            self.render()
            if self.exit_requested:
                self.close()
            return
        if action == "scan" and not error:
            with QSignalBlocker(self.target):
                self.target.clear()
                for name, device in result:
                    self.target.addItem(name, device)
            self.announce("请选择 BLE 设备。" if result else "未找到共享本项目服务的 BLE 设备。")
        if action == "采样频率":
            if error:
                with QSignalBlocker(self.sampling):
                    self.sampling.setCurrentIndex(self.sampling.findData(self.period_ms))
            else:
                self.period_ms = self.sampling.currentData()
        if error:
            self.announce(error_text(error), error != "REARM_REQUIRED")
        elif action in ("应用设定", "开启输出", "关闭输出"):
            if action != "关闭输出":
                self.output_dirty = False
            if action == "应用设定" and result["applied"]["enabled"] is True:
                self.announce("设定已读回，主输出保持开启。")
            else:
                self.announce(f"{action}已读回。")
        elif action == "应用保护阈值":
            self.protection_dirty = False
            self.announce(f"M{self.current_group} 的 OVP 和 OCP 已写入并读回。")
        elif action == "应用 OPP":
            self.opp_dirty = False
            group = result["applied"]["group"]
            opp_watts = result["protection"]["opp_mW"] / 1000
            self.announce(f"M{group} 的 OPP 已写入并读回 {opp_watts:.1f} W。")
        elif action == "切换数据组":
            self.announce(f"已切换至 M{self.current_group}，主输出已关闭。")
        elif action == "采样频率":
            self.announce(f"采样频率设为 {self.sampling.currentText()}。")
        elif action not in ("scan", "connect", "disconnect"):
            self.announce(f"{action}已确认。")
        if error == "REARM_REQUIRED":
            self.output_dirty = False
        self.render()
        if action == "disconnect" and self.exit_requested and not self.info:
            self.close()

    def mark_output(self):
        self.output_dirty = True

    def mark_protection(self):
        self.protection_dirty = True

    def mark_opp(self):
        self.opp_dirty = True

    def apply_setpoint(self):
        self.set_output(self.state.get("applied", {}).get("enabled") is True, apply_only=True)

    def set_output(self, enabled, apply_only=False):
        v, i = round(self.voltage.value() * 1000), round(self.limit.value() * 1000)
        step = self.info.get("step", 10)
        if v % step or i % step:
            self.announce("电压和限流设定的步进分别为 0.01 V 和 0.01 A。", True)
        elif v * i > self.info.get("max_output_mW", MAX_OUTPUT_MW) * 1000:
            self.announce(ERRORS["POWER_LIMIT"], True)
        else:
            self.change("应用设定" if apply_only else "开启输出", "set_output", {
                "voltage_mV": v, "current_mA": i, "enabled": enabled,
            }, revision=True)

    def toggle_output(self):
        if self.output_requests_off:
            self.change("关闭输出", "output_off", {})
        else:
            self.set_output(True)

    def select_group(self):
        self.change("切换数据组", "select_group", {"group": self.group_selector.currentData()}, revision=True)

    def group_details(self, key):
        details = self.group_settings.value(key)
        if isinstance(details, list) and len(details) == 2 and all(isinstance(text, str) for text in details):
            return details
        return "", ""

    def load_group_details(self):
        device = self.info.get("device_id")
        for index in range(self.group_selector.count()):
            group = self.group_selector.itemData(index)
            name, note = self.group_details(f"groups/{device}/M{group}") if device else ("", "")
            caption = f"M{group} · {name}" if name else f"M{group}"
            tooltip = escape(caption)
            if note:
                tooltip += "<br>" + escape(note).replace("\n", "<br>")
            self.group_selector.setItemText(index, caption)
            self.group_selector.setItemData(index, note, GROUP_NOTE_ROLE)
            self.group_selector.setItemData(index, f"<qt>{tooltip}</qt>", Qt.ItemDataRole.ToolTipRole)

    def edit_group_details(self):
        group = self.group_selector.currentData()
        device = self.info.get("device_id")
        if not device or group is None:
            return

        key = f"groups/{device}/M{group}"
        name, note = self.group_details(key)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"编辑 M{group} 的名称和备注")
        dialog.resize(430, 260)
        form = QVBoxLayout(dialog)
        form.setContentsMargins(18, 14, 18, 14)
        group_name = QLineEdit(name)
        group_name.setPlaceholderText("例如 12 V 风扇")
        group_note = QPlainTextEdit(note)
        group_note.setPlaceholderText("记录用途、电压电流或接线信息")
        for caption, field in (("名称", group_name), ("备注", group_note)):
            heading = label(caption)
            heading.setBuddy(field)
            form.addWidget(heading)
            form.addWidget(field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addWidget(buttons)
        group_name.setFocus()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = " ".join(group_name.text().splitlines()).strip()
            note = group_note.toPlainText().strip()
            if name or note:
                self.group_settings.setValue(key, [name, note])
            else:
                self.group_settings.remove(key)
            self.group_settings.sync()
            self.load_group_details()
            saved = self.group_settings.status() == QSettings.Status.NoError
            self.announce(f"M{group} 的名称和备注已保存。" if saved else "无法保存数据组信息，请检查客户端设置文件。", not saved)
            self.render()
        dialog.deleteLater()

    def set_protection(self):
        self.change("应用保护阈值", "set_protection", {
            "ovp_mV": round(self.ovp.value() * 1000),
            "ocp_mA": round(self.ocp.value() * 1000),
        }, revision=True)

    def set_opp(self):
        self.change("应用 OPP", "set_opp", {"opp_mW": round(self.opp.value() * 1000)}, revision=True)

    def match_ocp(self):
        reading, ok = QInputDialog.getDouble(
            self, "校对过流保护",
            f"请输入 SX150C 面板 M{self.current_group} 的过流保护阈值（A）。",
            self.ocp.value(), 0.001, 8.2, 3,
        )
        if ok:
            self.change("OCP 单位", "match_ocp", {"ocp_mA": round(reading * 1000)}, revision=True)

    def toggle_relay(self, channel):
        enabled = self.state.get("applied", {}).get(f"relay{channel}")
        if isinstance(enabled, bool):
            self.change(f"继电器 K{channel}", "set_relay", {"channel": channel, "enabled": not enabled})

    def change(self, action, operation, args, revision=False):
        self.launch(action, self.worker.client.change(
            operation, args, self.state.get("state_rev") if revision else None,
        ))

    def render(self):
        if not hasattr(self, "message"):
            return
        connected = bool(self.info)
        active = connected and time.monotonic() - self.last_receipt < 6
        measured = self.state.get("measured", {}) if active else {}
        fresh = bool(measured.get("fresh"))
        applied = self.state.get("applied", {}) if active else {}
        fault = self.state.get("fault", {}).get("code") if active else None
        enabled = applied.get("enabled") if fresh else None
        busy = bool(self.jobs)
        link_pending = bool(self.jobs & {"scan", "connect", "disconnect", "固件升级"})
        self.transport.setEnabled(not connected and not link_pending)
        self.target.setEnabled(not connected and not link_pending)
        self.refresh.setEnabled(not connected and not link_pending)
        self.connect_button.setEnabled(not link_pending and (connected or self.target.count() > 0))
        self.connect_button.setText("断开" if connected else "连接")
        self.float_button.setEnabled(not self.exit_requested and not self.jobs & {"固件升级", "disconnect"})
        supports_update = firmware_version(self.info) >= MIN_FIRMWARE_VERSION
        self.update_button.setEnabled(connected and supports_update and not busy)
        self.update_button.setToolTip("先用 COM3 安装 v1.2.0 或更新版本以启用升级。"
                                      if connected and not supports_update else "")
        direct = bool(self.info.get("direct_control"))
        self.link_badge.setText(("已连接" if direct else "只读连接") if active
                                else "等待数据" if connected else "未连接")
        self.link_badge.setToolTip(ERRORS["FIRMWARE_UPDATE"] if connected and not direct else "")
        dynamic(self.link_badge, "tone", "ok" if active else "warn" if connected else "")
        mode = self.state.get("mode")
        state_text = "主输出开启" if enabled else "主输出关闭" if enabled is False else "主输出状态未知"
        if mode == "protect" and fresh:
            state_text = f"SX 保护 {self.state.get('fault', {}).get('sx_protect_raw', '')}"
        elif fault and fault not in ("BAD_JSON", "BAD_CRC"):
            state_text += f" · {fault}"
        self.output_badge.setText(state_text)
        self.output_badge.setToolTip(error_text(fault) if fault else "")
        dynamic(self.output_badge, "tone", "warn" if fault else "ok" if enabled else "")
        self.render_meters(measured if fresh else {})
        sdk = f"{self.info['device_id']}  ·  固件 {self.info['firmware']}" if connected else ""
        if fresh and measured.get("input_mV") is not None:
            sdk += f"  ·  输入 {measured['input_mV'] / 1000:.2f} V  ·  {measured.get('cvcc', '—')}"
        self.device_line.setText(sdk)

        v, i = (applied.get(key) for key in ("voltage_mV", "current_mA"))
        if not self.output_dirty and v is not None and i is not None:
            self.load_value(self.voltage, v)
            self.load_value(self.limit, i)
        self.setpoint.setText(f"读回 {v / 1000:.2f} V  /  {i / 1000:.3f} A" if fresh and v is not None and i is not None
                              else "等待设定值读回")
        group_index = self.group_selector.findData(self.current_group) if fresh and self.current_group is not None else -1
        group_caption = self.group_selector.itemText(group_index) if group_index >= 0 else "—"
        self.group_status.setText(self.group_status.fontMetrics().elidedText(
            f"当前组 {group_caption}", Qt.TextElideMode.ElideRight, self.group_status.width(),
        ))
        self.group_status.setToolTip(self.group_selector.itemData(group_index, Qt.ItemDataRole.ToolTipRole) or "")
        selected_note = self.group_selector.currentData(GROUP_NOTE_ROLE) or ""
        selected_tooltip = self.group_selector.currentData(Qt.ItemDataRole.ToolTipRole) or ""
        self.group_selector.setToolTip(selected_tooltip)
        self.group_note.setVisible(bool(selected_note))
        if selected_note:
            self.group_note.setText(self.group_note.fontMetrics().elidedText(
                "备注  " + " ".join(selected_note.splitlines()), Qt.TextElideMode.ElideRight, self.group_note.width(),
            ))
            self.group_note.setToolTip(selected_tooltip)
        self.protection_title.setText(f"M{self.current_group} 保护阈值" if fresh and self.current_group is not None
                                      else "保护阈值")
        normal_control = direct and active and not busy
        group_ready = enabled is not None and self.current_group is not None
        can_select_group = normal_control and group_ready and self.info.get("group_select")
        same_group = self.group_selector.currentData() == self.current_group
        self.group_selector.setEnabled(connected and not busy)
        self.group_edit.setEnabled(connected and not busy)
        self.switch_group.setEnabled(bool(can_select_group) and not same_group)
        settable = normal_control and group_ready and same_group
        self.voltage.setEnabled(settable)
        self.limit.setEnabled(settable)
        self.apply_output.setEnabled(settable)
        self.output_requests_off = direct and (enabled is not False or "开启输出" in self.jobs)
        self.output_switch.setText("关闭输出" if self.output_requests_off else "开启输出")
        dynamic(self.output_switch, "role", "stop" if self.output_requests_off else "primary")
        self.output_switch.setEnabled(
            not self.jobs & {"关闭输出", "disconnect"} if self.output_requests_off else settable and not fault
        )
        self.output_switch.setToolTip(
            "主输出状态待确认，可发送关断命令。" if self.output_requests_off and enabled is None else ""
        )
        self.sampling.setEnabled(not busy and (not connected or bool(self.info.get("stream_period"))))
        self.sampling.setToolTip("蓝牙档位取决于连接协商和 SX 串口速率。曲线底部显示实际频率。" if self.kind == "ble"
                                 else "选择 USB 目标采样频率。曲线底部显示实际频率。")

        for channel, widget in self.relay.items():
            engaged = applied.get(f"relay{channel}")
            description = "吸合" if engaged is True else "释放" if engaged is False else "状态未知"
            widget.setText(f"K{channel}  ·  {description}")
            dynamic(widget, "active", engaged is True)
            widget.setEnabled(normal_control and isinstance(engaged, bool))

        if self.floating is not None:
            self.floating.sync(
                {key: widget.text() for key, widget in self.meter_values.items()},
                None if "开启输出" in self.jobs else enabled, self.output_switch,
                {channel: (widget, applied.get(f"relay{channel}")) for channel, widget in self.relay.items()},
            )
            if "固件升级" in self.jobs:
                self.floating.output.setEnabled(False)
                self.floating.output.setToolTip("固件升级时不允许控制输出。")
                self.floating.output.setAccessibleDescription(self.floating.output.toolTip())
            elif self.output_dirty and not self.output_requests_off:
                self.floating.output.setEnabled(False)
                self.floating.output.setToolTip("请恢复主窗口，先应用尚未提交的输出设定。")
                self.floating.output.setAccessibleDescription(self.floating.output.toolTip())

        self.render_protection(settable, enabled, fresh)

    def render_meters(self, measured):
        values = {
            "voltage": (measured.get("voltage_mV"), 2),
            "current": (measured.get("current_mA"), 3),
            "power": (measured.get("power_mW"), 1),
        }
        for name, (value, digits) in values.items():
            self.meter_values[name].setText("—" if value is None else f"{value / 1000:.{digits}f}")

    def render_protection(self, settable, enabled, fresh):
        protection = self.state.get("protection", {}) if fresh else {}
        v, i, power = (protection.get(key) for key in ("ovp_mV", "ocp_mA", "opp_mW"))
        step = protection.get("ocp_step_mA")
        available = bool(self.info.get("protection_write"))
        opp_available = bool(self.info.get("opp_write"))
        match = available and bool(protection) and not step
        self.match.setVisible(match)
        self.match.setEnabled(settable)
        can_write = settable and available and bool(step) and enabled is False
        for widget in (self.ovp, self.ocp, self.apply_protection):
            widget.setEnabled(can_write)
        can_write_opp = settable and opp_available and power is not None and enabled is False
        self.opp.setEnabled(can_write_opp)
        self.apply_opp.setEnabled(can_write_opp)
        if step and self.ocp.singleStep() != step / 1000:
            # Reapplying the step discards text that is still being edited.
            self.ocp.setSingleStep(step / 1000)
        if not self.protection_dirty:
            self.load_value(self.ovp, v)
            self.load_value(self.ocp, i)
        maximum_watts = self.opp.maximum()
        valid_opp = power is not None and 100 <= power <= round(maximum_watts * 1000)
        if not self.opp_dirty:
            self.load_value(self.opp, power if valid_opp else MAX_OUTPUT_MW)
        self.opp.setToolTip(f"可设置 0.1～{maximum_watts:.1f} W。当前阈值见下方读回。")
        readings = (
            "— V" if v is None else f"{v / 1000:.2f} V",
            "— A" if i is None else f"{i / 1000:.3f} A",
            "— W" if power is None else f"{power / 1000:.1f} W",
        )
        self.protection_readback.setText("读回 " + " / ".join(readings) if protection else "等待当前组阈值读回。")

        hint = ""
        if self.info and not self.info.get("direct_control"):
            hint = "更新至 ESP32 固件 v1.1.0 后可设置阈值。"
        elif enabled is True:
            hint = "关闭主输出后可修改保护阈值。"
        elif power is not None and not opp_available:
            hint = "更新至 ESP32 固件 v1.1.1 后可修改 OPP。"
        elif power is not None and not valid_opp:
            hint = f"当前 OPP 超出范围，请写入 0.1～{maximum_watts:.1f} W。"
        elif match:
            hint = "输入面板当前的 OCP 阈值以校对转换单位。"
        self.protection_hint.setText(hint)
        self.protection_hint.setVisible(bool(hint))

    @staticmethod
    def load_value(widget, value):
        if value is not None and not widget.hasFocus():
            with QSignalBlocker(widget):
                widget.setValue(value / 1000)

    def closeEvent(self, event):
        if "固件升级" in self.jobs:
            self.exit_requested = True
            self.cancel_firmware_update()
            event.ignore()
            return
        if self.info or "disconnect" in self.jobs:
            self.exit_requested = True
            event.ignore()
            if "disconnect" not in self.jobs:
                self.toggle_connection()
            return
        if self.floating is not None:
            self.floating.hide()
            self.tray.hide()
        self.worker.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("直流电源控制台")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLES)
    app.setWindowIcon(icon())
    window = Window()
    window.show()
    return app.exec()
