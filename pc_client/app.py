from html import escape
import re
import sys
from threading import Event
import time

from PySide6.QtCore import QIODevice, QLocale, QSaveFile, QSettings, QSignalBlocker, Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QStyle, QStyleOptionComboBox, QStylePainter, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from pc_client.chart import HistoryPane
from pc_client.connection import BleLink, LinkWorker, firmware_version, retains_outputs, usb_ports
from pc_client.diagnostics import ACTION as DIAGNOSTIC_ACTION, serialize as serialize_diagnostics
from pc_client.firmware_update import FirmwareImage, MIN_FIRMWARE_VERSION
from pc_client.floating import FloatingPanel
from pc_client.history import PowerHistory, normalize_measurements
from pc_client.pc_upgrade import pending_handoff, run_upgrade_worker, smoke_handoff, smoke_startup
from pc_client.relay_dialog import RelayNamesDialog, RelaySequenceDialog
from pc_client.relay_sequence import RelayNames, SequenceStore, output_profile, resolve_steps, sequence_directory
from pc_client.theme import STYLES, icon
from pc_client.update_dialog import UpdateDialog
from pc_client.version import PC_VERSION


GROUP_NOTE_ROLE = Qt.ItemDataRole.UserRole + 1
GROUP_SWITCH = "切换数据组"
APPLY_PROTECTION = "应用保护阈值"
RELAY_SEQUENCE = "时序控制"
RELAY_NAMES_KEY = "relays/names_and_bindings"
CONTROL_RAIL_WIDTH = 224
MAX_MEMORY_GROUP = 10  # SX150C firmware supports eleven slots, M0 through M10.
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
    "RELAY_STOPPED": "已按请求停止。",
    "RELAY_NOT_READY": "设备状态不再满足运行条件。",
    "RELAY_OUTPUT_CHANGED": "主输出状态或设备设定有变化，未继续执行。",
    "RELAY_OUTPUT_READBACK": "未确认主输出达到目标状态。",
    "RELAY_READBACK": "继电器读回与目标状态不一致，或连接已变化。",
    "BLE_RESCAN": "找不到已选蓝牙设备，请重新扫描并选择设备。",
    "BLE_PAIR_CANCELLED": "已取消蓝牙配对。再次点击连接可重试。",
    "BLE_PAIR_TIMEOUT": "等待 Windows 蓝牙配对超时。请再次连接并查看系统配对提示。",
    "BLE_PAIR_FAILED": "Windows 未能完成带认证的蓝牙配对。可在「蓝牙和设备」中手动配对后重试（PIN 112233）。",
    "BLE_AUTH_REQUIRED": "蓝牙认证失败。请在 Windows「蓝牙和其他设备」中配对电源，输入 PIN 112233 后重新连接。",
    "FIRMWARE_UPDATE": "当前固件可读取状态，更新至 v1.1.0 后可直接控制。",
    "BAD_STREAM_PERIOD": "设备不支持这个采样频率。",
    "NVS_ERROR": "设备配置保存失败。",
    "CANCELLED": "前一操作已中断。",
    "BLE_UNAVAILABLE": "设备蓝牙未就绪，未能清除配对。",
    "BLE_ACTIVE": "设备有活动蓝牙连接。请先从已连接的电脑断开，然后重试。",
    "BONDS_OUTPUT": "请先关闭主输出和两路继电器，并确认读回正常。",
    "USB_ONLY": "只能通过 USB 清除设备端蓝牙配对。",
    "BLE_BONDS_FAILED": "配对没有全部清除。可能已有部分条目被清除，请核对后再试。",
    "BLE_ADVERTISING_FAILED": "配对已清除，但设备端蓝牙广播未恢复。请保持输出关闭，确认安全后再手动重启设备。",
    "DIAG_USB": "请通过 USB 有线连接设备后导出诊断日志。",
    "DIAG_UNSUPPORTED": "当前设备固件不支持 USB 诊断。请先升级 ESP32 固件。",
    "DIAG_PROTOCOL": "设备返回的诊断日志格式不兼容。",
    "DIAG_BUSY": "设备正忙，未能读取诊断日志。请稍后重试。",
    "DIAG_RESTARTED": "读取期间设备已重启。请重新导出当前开机的日志。",
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
    if not isinstance(value, str):
        return value
    code, sep, detail = value.partition(":")
    if sep and code in {"BLE_PAIR_FAILED", "BLE_PAIR_TIMEOUT", "BLE_PAIR_CANCELLED"}:
        if not re.fullmatch(r"[A-Z][A-Z_0-9]{0,47}(?:/(?:[A-Z][A-Z_0-9]{0,47}|0x[0-9A-F]{8}))?", detail):
            return ERRORS[code]
        status = detail.split("/", 1)[0]
        explanation = {
            "PAIRED": "Windows 返回已配对，但保护等级不足，客户端未继续连接。请在 Windows 设置中检查配对。",
            "AUTHENTICATION_FAILURE": "Windows 或设备拒绝了认证。请核对设备和配对提示；若有旧配对记录，可在 Windows 中移除后重试。",
            "AUTHENTICATION_TIMEOUT": "Windows 的认证超时。请重新连接，并在出现系统提示时及时完成配对。",
            "AUTHENTICATION_NOT_ALLOWED": "Windows 或设备不支持这次认证方式。请在 Windows 设置中先手动配对。",
            "PROTECTION_LEVEL_COULD_NOT_BE_MET": "这次配对未达到加密和认证要求。请在 Windows 设置中先手动配对。",
            "REQUIRED_HANDLER_NOT_REGISTERED": "Windows 未能处理需要的配对操作。请在 Windows 设置中先手动配对。",
            "REMOTE_DEVICE_HAS_ASSOCIATION": "设备已有配对关联。请先断开其他电脑，并检查双方的旧配对记录。",
            "ACCESS_DENIED": "Windows 拒绝应用请求配对。请检查蓝牙权限，或在 Windows 设置中先手动配对。",
        }.get(status, ERRORS[code])
        return f"{explanation}［Windows: {detail}］"
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


class MemoryGroupSelector(QComboBox):
    def paintEvent(self, _event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        if self.currentIndex() >= 0:
            edit_width = self.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self,
            ).width()
            option.currentText = option.fontMetrics.elidedText(
                option.currentText, Qt.TextElideMode.ElideRight, max(0, edit_width - 4),
            )
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)

    def wheelEvent(self, event):
        # Scrolling past a closed selector must not turn the main output off.
        event.ignore()

    def keyPressEvent(self, event):
        if (not self.view().isVisible()
                and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home,
                                    Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)
                and not event.modifiers()):
            self.showPopup()
            event.accept()
            return
        super().keyPressEvent(event)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"直流电源控制台 v{PC_VERSION}")
        self.setMinimumSize(990, 690)
        self.resize(1200, 810)
        self.worker = LinkWorker()
        self.worker.connected.connect(self.connected)
        self.worker.info_updated.connect(self.update_info)
        self.worker.disconnected.connect(self.disconnected)
        self.worker.state.connect(self.state_read)
        self.worker.finished.connect(self.finished)
        self.worker.relay_progress.connect(self.relay_progress)
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
        self.update_dialog = None
        try:
            self.relay_names = RelayNames.decode(self.group_settings.value(RELAY_NAMES_KEY, ""))
        except (TypeError, ValueError, RecursionError):
            self.relay_names = RelayNames()
        self.sequence_store = SequenceStore(sequence_directory())
        self.loaded_sequence = None
        self.relay_cancel = Event()
        self.diagnostic_path = None
        self.pc_handoff = False
        self.saved_pc_resume = None
        self.connect_target = None
        self.switch_done = None

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
        self.update_button = button("升级", self.check_updates)
        row.addWidget(self.update_button)
        self.bond_button = button("清除蓝牙配对", self.clear_ble_bonds)
        row.addWidget(self.bond_button)
        self.diagnostic_button = button("诊断", self.export_diagnostics)
        self.diagnostic_button.setAccessibleName("导出固件诊断日志")
        row.addWidget(self.diagnostic_button)
        self.float_button = button("悬浮", self.show_floating)
        row.addWidget(self.float_button)
        header.addLayout(row)
        details = QHBoxLayout()
        self.link_badge = label("未连接", "pill")
        details.addWidget(self.link_badge)
        self.device_line = label("", "muted")
        details.addWidget(self.device_line, 1)
        version = label(f"客户端 v{PC_VERSION}", "muted")
        version.setAccessibleName(f"Windows 客户端版本 {PC_VERSION}")
        details.addWidget(version)
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
        if self.update_dialog is not None:
            self.update_dialog.hide()
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
        left.setFixedWidth(CONTROL_RAIL_WIDTH)
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        memory, out = card()
        out.setContentsMargins(11, 10, 11, 10)
        out.setSpacing(6)
        memory.setStyleSheet("""
            QComboBox, QDoubleSpinBox { padding: 1px 4px; }
            QPushButton { padding: 1px 7px; }
        """)
        group_row = QHBoxLayout()
        group_row.setSpacing(6)
        group_title = label("记忆组", "section")
        group_title.setStyleSheet("font-size: 13px;")
        group_row.addWidget(group_title)
        self.group_selector = MemoryGroupSelector()
        self.group_selector.setAccessibleName("当前记忆组")
        self.group_selector.setPlaceholderText("—")
        self.group_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.group_selector.setMinimumContentsLength(3)
        self.group_selector.view().setMinimumWidth(240)
        self.group_selector.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        for group in range(MAX_MEMORY_GROUP + 1):
            self.group_selector.addItem(f"M{group}", group)
        self.group_selector.activated.connect(self.select_group)
        group_row.addWidget(self.group_selector, 1)
        self.group_edit = button("编辑", self.edit_group_details)
        self.group_edit.setAccessibleName("编辑记忆组名称和备注")
        self.group_edit.setToolTip("编辑当前记忆组的名称和备注")
        group_row.addWidget(self.group_edit)
        out.addLayout(group_row)
        self.group_status = label("", "muted")
        self.group_status.setWordWrap(True)
        self.group_status.hide()
        out.addWidget(self.group_status)
        self.group_note = label("", "muted")
        self.group_note.setTextFormat(Qt.TextFormat.PlainText)
        self.group_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        out.addWidget(self.group_note)
        out.addWidget(label("输出设定", "section"))
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
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background: #e4edf2;")
        out.addWidget(divider)
        out.addWidget(label("保护阈值", "section"))
        self.ovp = spin(0.50, 42, 0.01, "V", 2)
        self.ocp = spin(0.001, 8.2, 0.001, "A", 3)
        self.opp = spin(0.1, MAX_OUTPUT_MW / 1000, 0.1, "W", 1)
        self.ovp.setValue(42)
        self.ocp.setValue(8.2)
        self.opp.setValue(MAX_OUTPUT_MW / 1000)
        self.ovp.valueChanged.connect(self.mark_protection)
        self.ocp.valueChanged.connect(self.mark_protection)
        self.opp.valueChanged.connect(self.mark_opp)
        self.opp.lineEdit().textEdited.connect(self.mark_opp)
        out.addLayout(self.fields(("过压", self.ovp), ("过流", self.ocp), ("过功率", self.opp)))
        for widget, name in ((self.ovp, "过压保护 OVP"), (self.ocp, "过流保护 OCP"), (self.opp, "过功率保护 OPP")):
            widget.setAccessibleName(name)
        self.protection_readback = label("等待阈值读回。", "muted")
        out.addWidget(self.protection_readback)
        self.protection_hint = label("", "muted")
        self.protection_hint.setWordWrap(True)
        out.addWidget(self.protection_hint)
        self.match = button("校对 OCP", self.match_ocp)
        self.apply_protection = button(APPLY_PROTECTION, self.set_protection)
        out.addWidget(self.apply_protection)
        out.addWidget(self.match)
        layout.addWidget(memory)

        relays, relay_layout = card("继电器")
        relay_layout.setContentsMargins(11, 10, 11, 10)
        relays.setStyleSheet("QPushButton { padding: 1px 7px; }")
        relay_row = QHBoxLayout()
        self.relay = {
            i: button(f"K{i} · 状态未知", lambda _checked=False, channel=i: self.toggle_relay(channel))
            for i in (1, 2)
        }
        for item in self.relay.values():
            relay_row.addWidget(item, 1)
        relay_layout.addLayout(relay_row)
        self.relay_name_config = button("设置名称…", self.edit_relay_names)
        relay_layout.addWidget(self.relay_name_config)
        layout.addWidget(relays)

        sequence, seq_layout = card("时序控制")
        seq_layout.setContentsMargins(11, 10, 11, 10)
        sequence.setStyleSheet("QPushButton { padding: 1px 7px; }")
        self.sequence_selector = QComboBox()
        self.sequence_selector.setAccessibleName("已保存的时序")
        self.sequence_selector.setToolTip(f"保存位置：{self.sequence_store.directory}")
        self.sequence_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.sequence_selector.currentIndexChanged.connect(self.load_relay_sequence)
        seq_layout.addWidget(self.sequence_selector)
        self.sequence_run = button("运行", self.run_or_stop_relay_sequence, "primary")
        self.sequence_new = button("新建", lambda: self.edit_relay_sequence(False))
        self.sequence_edit = button("编辑", lambda: self.edit_relay_sequence(True))
        actions_row = QHBoxLayout()
        for control in (self.sequence_run, self.sequence_new, self.sequence_edit):
            actions_row.addWidget(control, 1)
        seq_layout.addLayout(actions_row)
        self.sequence_status = label("", "muted")
        self.sequence_summary = label("", "muted")
        for info in (self.sequence_summary, self.sequence_status):
            info.setWordWrap(True)
            info.setTextFormat(Qt.TextFormat.PlainText)
            seq_layout.addWidget(info)
        self.refresh_sequence_selector()
        layout.addWidget(sequence)
        layout.addStretch(1)
        return left

    @staticmethod
    def fields(*pairs):
        grid = QGridLayout()
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(3)
        for column, (caption, widget) in enumerate(pairs):
            grid.addWidget(label(caption, "muted"), 0, column)
            widget.setAccessibleName(caption)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            grid.addWidget(widget, 1, column)
            grid.setColumnStretch(column, 1)
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
            self.target.setPlaceholderText("")
            self.announce("正在扫描 BLE 设备…")
            self.launch("scan", BleLink.scan())
            return
        old = self.target.currentData()
        with QSignalBlocker(self.target):
            self.target.clear()
            self.target.setPlaceholderText("未找到 CH340 串口")
            for description, name in usb_ports():
                self.target.addItem(description, name)
            if (index := self.target.findData(old)) >= 0:
                self.target.setCurrentIndex(index)
            elif self.target.count():
                self.target.setCurrentIndex(0)
        self.render()

    def transport_changed(self):
        self.kind = self.transport.currentData()
        with QSignalBlocker(self.target):
            self.target.clear()
        self.refresh.setText("扫描" if self.kind == "ble" else "刷新")
        self.refresh_sampling_options()
        self.refresh_targets()

    def toggle_connection(self):
        if self.pc_handoff:
            return
        if self.info:
            self.cancel_relay_sequence()
            self.announce("正在断开…")
            self.launch("disconnect", self.worker.client.close())
        elif self.target.currentData() is not None:
            self.connect_target = self.target.currentData()
            if self.kind == "ble":
                self.announce("正在连接蓝牙。若尚未配对，客户端将请求安全配对；如 Windows 要求确认，请核对设备。")
                self.launch("connect", self.worker.client.connect(self.kind, self.connect_target, allow_pairing=True))
            else:
                self.announce("正在读取设备信息…")
                self.launch("connect", self.worker.client.connect(self.kind, self.connect_target))

    def check_updates(self):
        if self.update_dialog is None:
            self.update_dialog = UpdateDialog(self, self.info.get("firmware"))
        else:
            self.update_dialog.set_firmware(self.info.get("firmware"))
        self.update_dialog.show()
        self.update_dialog.raise_()
        self.update_dialog.activateWindow()
        if self.update_dialog.checked is None:
            self.update_dialog.check_all()

    def pc_upgrade_blocker(self):
        if self.info and not retains_outputs(self.info):
            applied = self.state.get("applied", {})
            if (time.monotonic() - self.last_receipt >= 6
                    or self.state.get("measured", {}).get("fresh") is not True
                    or not all(applied.get(key) is False for key in ("enabled", "relay1", "relay2"))):
                return "旧固件断开时可能关断输出。请先确认主输出和两路继电器均已关闭，再升级客户端。"
        if self.jobs or self.exit_requested or self.pc_handoff:
            return "请等待当前设备操作完成后再升级客户端。"
        if self.info and self.connect_target is None:
            return "无法确定当前设备的连接地址，请先手动重连。"
        return ""

    def prepare_pc_switch(self, done):
        if problem := self.pc_upgrade_blocker():
            done(None, problem)
            return
        self.pc_handoff = True
        self.setEnabled(False)
        self.saved_pc_resume = None
        self.render()
        if not self.info:
            done(None, "")
            return
        target = self.connect_target
        self.saved_pc_resume = dict(mode=self.kind, target=getattr(target, "address", target),
                                    device_id=self.info["device_id"])
        self.switch_done = done
        self.launch("pc-disconnect", self.worker.client.close())

    def resume_pc_link(self, resume, done):
        if not resume:
            done("")
            return
        mode, target, device = (resume[key] for key in ("mode", "target", "device_id"))
        self.kind = mode
        self.refresh.setText("扫描" if mode == "ble" else "刷新")
        self.connect_target = target
        with QSignalBlocker(self.transport), QSignalBlocker(self.target):
            self.transport.setCurrentIndex(self.transport.findData(mode))
            self.target.clear()
            self.target.addItem(target, target)
        self.switch_done = done
        self.launch("pc-reconnect", self.worker.client.connect(mode, target, expected_device=device))

    def cancel_pc_switch(self, done):
        resume, self.saved_pc_resume = self.saved_pc_resume, None
        if resume and not self.info:
            self.resume_pc_link(resume, lambda error: self.finish_pc_switch_recovery(done, error))
        else:
            self.finish_pc_switch_recovery(done, "")

    def finish_pc_switch_recovery(self, done, error):
        self.pc_handoff = False
        self.setEnabled(True)
        self.render()
        done(error)

    def close_for_pc_switch(self):
        self.pc_handoff = False
        self.close()

    def bond_recovery_status(self):
        if self.kind != "usb" or not self.info:
            return "请通过 USB 连接设备。"
        if self.info.get("clear_ble_bonds") is not True:
            return "当前设备固件不支持清除蓝牙配对。"
        if self.jobs or self.pc_handoff or self.exit_requested:
            return "请等待当前设备操作完成。"
        applied = self.state.get("applied", {})
        if (time.monotonic() - self.last_receipt >= 6
                or self.state.get("measured", {}).get("fresh") is not True
                or not all(applied.get(key) is False for key in ("enabled", "relay1", "relay2"))):
            return "请先关闭主输出和两路继电器，并确认读回正常。"
        return ""

    def clear_ble_bonds(self):
        if self.bond_recovery_status():
            return
        device = self.info["device_id"]
        dialog = QMessageBox(self)
        dialog.setWindowTitle("清除设备端蓝牙配对")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"清除 {device} 上保存的全部蓝牙配对？")
        dialog.setInformativeText("所有曾配对的电脑都需要重新配对。不会重置设备或更改电源设置。")
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        dialog.setStandardButtons(yes | no)
        dialog.setDefaultButton(no)
        if dialog.exec() != yes or self.bond_recovery_status() or self.info["device_id"] != device:
            return
        self.launch("清除蓝牙配对", self.worker.client.call(
            "clear_ble_bonds", {"device_id": device, "confirm_all": True}))

    def diagnostic_status(self):
        if self.kind != "usb" or not self.info:
            return ERRORS["DIAG_USB"]
        if self.info.get("diag_log") is not True:
            return ERRORS["DIAG_UNSUPPORTED"]
        if self.jobs or self.pc_handoff or self.exit_requested:
            return "请等待当前设备操作完成。"
        return ""

    def export_diagnostics(self):
        if problem := self.diagnostic_status():
            self.announce(problem, True)
            return
        device = self.info.get("device_id")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出固件诊断日志", "PowerModule-Diagnostics.json", "JSON 日志 (*.json)")
        if not path:
            return
        if problem := self.diagnostic_status():
            self.announce(problem, True)
            return
        if self.info.get("device_id") != device:
            self.announce("当前连接的设备已改变，请重新导出。", True)
            return
        self.diagnostic_path = path
        self.announce("正在读取 ESP32 诊断日志…")
        self.launch(DIAGNOSTIC_ACTION, self.worker.client.read_diagnostics())

    def save_diagnostics(self, result):
        snapshot, firmware = result
        payload = serialize_diagnostics(snapshot, firmware, PC_VERSION)
        file = QSaveFile(self.diagnostic_path)
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            self.announce("无法打开诊断日志保存位置。", True)
            return
        if file.write(payload) != len(payload):
            file.cancelWriting()
            self.announce("无法完整保存诊断日志。", True)
            return
        if not file.commit():
            self.announce("无法保存诊断日志。", True)
            return
        lost = snapshot["overwritten_before_read"] + snapshot["overwritten_during_read"]
        detail = f"；另有 {lost} 条较早的记录已被覆盖" if lost else ""
        self.announce(f"已导出 {len(snapshot['events'])} 条诊断记录{detail}。")

    def firmware_upgrade_allowed(self):
        return bool(not self.pc_handoff and not self.jobs and firmware_version(self.info) >= MIN_FIRMWARE_VERSION)

    def start_firmware_update(self, filename=None, verified=None):
        if not self.firmware_upgrade_allowed():
            self.announce("连接支持在线升级的设备后再安装固件。", True)
            return False
        if filename is None:
            filename, _ = QFileDialog.getOpenFileName(self, "选择升级固件", "", "PowerModule 固件 (*.bin)")
        if not filename:
            return
        try:
            image = FirmwareImage.load(filename)
            if verified is not None and (image.digest, image.version, len(image.data)) != (
                    verified.sha256, verified.version, verified.size):
                self.announce("本地固件已改变，请重新检查或下载。", True)
                return False
        except ValueError as error:
            self.announce(error_text(str(error)), True)
            return
        except OSError:
            self.announce("无法读取选择的固件文件。", True)
            return
        prompt = (f"为 {self.info['device_id']} 安装 v{image.version}。"
                  f"当前版本为 v{self.info['firmware']}。\n"
                  "请关闭主输出和两路继电器。升级校验完成后设备会重启。")
        identity = self.info["device_id"], self.info["firmware"]
        yes = QMessageBox.StandardButton.Yes
        no = QMessageBox.StandardButton.No
        if QMessageBox.question(self, "安装固件", prompt, yes | no, no) != yes:
            return False
        if not self.firmware_upgrade_allowed() or identity != (
                self.info.get("device_id"), self.info.get("firmware")):
            return False
        self.update_cancel = Event()
        self.launch("固件升级", self.worker.client.update_firmware(image, self.update_cancel.is_set))
        self.show_update_progress(0, len(image.data), "transfer")
        return True

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
        if self.update_dialog is not None:
            self.update_dialog.set_firmware(info.get("firmware"))
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
        if self.update_dialog is not None:
            self.update_dialog.set_firmware(None)
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
        if (not self.pc_handoff and self.info.get("stream_period") and period is not None and period != self.period_ms
                and not self.jobs & {"采样频率", "固件升级", RELAY_SEQUENCE}):
            self.launch("采样频率", self.worker.client.call("set_stream_period", {"period_ms": period}))

    @Slot(str, object, str)
    def finished(self, action, result, error):
        self.jobs.discard(action)
        if action == RELAY_SEQUENCE:
            self.finish_relay_sequence(result, error)
            return
        if action == DIAGNOSTIC_ACTION:
            if error:
                self.announce(error_text(error), True)
            elif not self.exit_requested:
                self.save_diagnostics(result)
            self.diagnostic_path = None
            self.render()
            return
        if action == "清除蓝牙配对":
            if error:
                self.announce(error_text(error), True)
            else:
                count = (result or {}).get("data", {}).get("cleared")
                if type(count) is int and count >= 0:
                    self.announce(f"已清除 {count} 条设备端蓝牙配对。请在 Windows 中移除该设备后重新配对。")
                else:
                    self.announce("无法核对设备端配对清除的结果。", True)
            self.render()
            return
        if action in ("pc-disconnect", "pc-reconnect"):
            done, self.switch_done = self.switch_done, None
            if action == "pc-disconnect":
                done(self.saved_pc_resume, error or ("" if result else "未能确认旧客户端已断开。"))
            else:
                done(error or ("" if self.info else "未能确认原设备连接。"))
            self.render()
            return
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
        if action == APPLY_PROTECTION:
            result = result or {}
            verified = result.get("verified", [])
            problem = error or result.get("error")
            if result.get("group") == self.current_group:
                if "set_protection" in verified:
                    self.protection_dirty = False
                if "set_opp" in verified:
                    self.opp_dirty = False
            done = "、".join("OVP/OCP" if name == "set_protection" else "OPP" for name in verified)
            if problem:
                prefix = f"{done} 已读回；其余未确认。" if done else "未确认保护阈值写入。"
                self.announce(f"{prefix}{error_text(problem)}", True)
            elif done:
                self.announce(f"M{result['group']} 的 {done} 已写入并读回。")
            self.render()
            return
        if error:
            self.announce(error_text(error), error != "REARM_REQUIRED")
        elif action in ("应用设定", "开启输出", "关闭输出"):
            if action != "关闭输出":
                self.output_dirty = False
            if action == "应用设定" and result["applied"]["enabled"] is True:
                self.announce("设定已读回，主输出保持开启。")
            else:
                self.announce(f"{action}已读回。")
        elif action == GROUP_SWITCH:
            applied = (result or {}).get("applied", {})
            if type(applied.get("group")) is int and applied.get("enabled") is False:
                self.announce(f"已切换至 M{applied['group']}，主输出已关闭。")
            else:
                self.announce("暂未确认切组结果，请核对设备读回。", True)
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

    def group_switch_available(self):
        measured = self.state.get("measured", {})
        applied = self.state.get("applied", {})
        return (not self.exit_requested and not self.pc_handoff and not self.jobs - {DIAGNOSTIC_ACTION}
                and self.info.get("direct_control") and self.info.get("group_select")
                and time.monotonic() - self.last_receipt < 6 and measured.get("fresh")
                and type(applied.get("enabled")) is bool and type(self.state.get("state_rev")) is int
                and type(self.current_group) is int and applied.get("group") == self.current_group
                and 0 <= self.current_group <= MAX_MEMORY_GROUP)

    @Slot(int)
    def select_group(self, index):
        target = self.group_selector.itemData(index)
        # Keep showing the confirmed group until fresh device readback arrives.
        with QSignalBlocker(self.group_selector):
            self.group_selector.setCurrentIndex(self.group_selector.findData(self.current_group)
                                                if self.current_group is not None else -1)
        if (self.group_switch_available() and type(target) is int
                and 0 <= target <= MAX_MEMORY_GROUP and target != self.current_group):
            self.group_status.setText(f"正在切换到 M{target}，切换时关闭主输出…")
            self.change(GROUP_SWITCH, "select_group", {"group": target}, revision=True)

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
        applied = self.state.get("applied", {})
        revision = self.state.get("state_rev")
        group = self.current_group
        if (not self.apply_protection.isEnabled() or not self.info.get("direct_control")
                or self.exit_requested or self.pc_handoff or self.jobs - {DIAGNOSTIC_ACTION}
                or time.monotonic() - self.last_receipt >= 6
                or not self.state.get("measured", {}).get("fresh")
                or type(group) is not int or applied.get("group") != group
                or applied.get("enabled") is not False or type(revision) is not int):
            return
        protection = self.state.get("protection", {})
        operations = []
        if self.protection_dirty:
            args = {"ovp_mV": round(self.ovp.value() * 1000), "ocp_mA": round(self.ocp.value() * 1000)}
            if any(protection.get(key) != value for key, value in args.items()):
                step = protection.get("ocp_step_mA")
                if not self.info.get("protection_write") or type(step) is not int or step <= 0:
                    self.announce("需先校对 OCP，未写入任何保护阈值。", True)
                    return
                if any(type(protection.get(key)) is not int for key in args):
                    self.announce("等待 OVP/OCP 有效读回，未写入任何保护阈值。", True)
                    return
                if args["ovp_mV"] % 10 or args["ocp_mA"] % step:
                    self.announce(error_text("BAD_STEP"), True)
                    return
                operations.append(("set_protection", args))
        if self.opp_dirty:
            power = round(self.opp.value() * 1000)
            if power != protection.get("opp_mW"):
                if not self.info.get("opp_write") or protection.get("opp_mW") is None:
                    self.announce("当前设备未确认支持写入 OPP，未写入任何保护阈值。", True)
                    return
                if not 100 <= power <= round(self.opp.maximum() * 1000) or power % 100:
                    self.announce(error_text("OPP_RANGE"), True)
                    return
                operations.append(("set_opp", {"opp_mW": power}))
        if not operations:
            self.protection_dirty = self.opp_dirty = False
            self.announce("保护阈值没有改动。")
            self.render()
            return
        self.announce("正在应用保护阈值并核对读回…")
        self.launch(APPLY_PROTECTION, self.worker.client.apply_protection(group, revision, operations))

    def match_ocp(self):
        reading, ok = QInputDialog.getDouble(
            self, "校对过流保护",
            f"请输入 SX150C 面板 M{self.current_group} 的过流保护阈值（A）。",
            self.ocp.value(), 0.001, 8.2, 3,
        )
        if ok:
            self.change("OCP 单位", "match_ocp", {"ocp_mA": round(reading * 1000)}, revision=True)

    @Slot()
    def edit_relay_names(self):
        if RELAY_SEQUENCE in self.jobs or self.exit_requested or self.pc_handoff:
            return
        RelayNamesDialog(self.relay_names, self.save_relay_names, self).exec()

    def save_relay_names(self, config):
        if RELAY_SEQUENCE in self.jobs or self.exit_requested or self.pc_handoff:
            raise ValueError("时序运行中或客户端正在退出，不能修改绑定。")
        self.group_settings.setValue(RELAY_NAMES_KEY, config.encode())
        self.group_settings.sync()
        if self.group_settings.status() != QSettings.Status.NoError:
            raise OSError("未能保存继电器名称设置。")
        self.relay_names = config
        self.render()

    def refresh_sequence_selector(self, selected=None):
        selected = selected or self.sequence_selector.currentData()
        names = []
        problem = ""
        try:
            names = self.sequence_store.names()
        except OSError:
            problem = "无法读取本地时序目录。"
        with QSignalBlocker(self.sequence_selector):
            self.sequence_selector.clear()
            self.sequence_selector.setPlaceholderText("选择时序" if names else "没有保存的时序")
            for name in names:
                self.sequence_selector.addItem(name, name)
            self.sequence_selector.setCurrentIndex(self.sequence_selector.findData(selected) if selected else -1)
        if problem:
            self.loaded_sequence = None
            self.sequence_status.setText(problem)
            self.render()
        else:
            self.load_relay_sequence()

    def load_relay_sequence(self):
        if RELAY_SEQUENCE in self.jobs:
            return
        name = self.sequence_selector.currentData()
        self.loaded_sequence = None
        if name is None:
            self.sequence_status.setText("请选择时序。" if self.sequence_selector.count() else "新建时序后可在这里选择。")
        else:
            try:
                self.loaded_sequence = (name, self.sequence_store.load(name))
                self.sequence_status.setText("已载入。")
            except (ValueError, OSError) as error:
                self.sequence_status.setText(f"未载入：{error}")
        self.render()

    def edit_relay_sequence(self, edit_existing):
        if RELAY_SEQUENCE in self.jobs or self.exit_requested or self.pc_handoff:
            return
        name = self.sequence_selector.currentData() if edit_existing else None
        try:
            steps = self.sequence_store.load(name) if name is not None else ()
        except (OSError, ValueError) as error:
            self.sequence_status.setText(f"无法编辑：{error}")
            return
        dialog = RelaySequenceDialog(self.sequence_store, name, steps, self, relay_names=self.relay_names.names)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_sequence_selector(dialog.saved_name)
        dialog.deleteLater()

    def run_or_stop_relay_sequence(self):
        if RELAY_SEQUENCE in self.jobs:
            self.cancel_relay_sequence()
            return
        if self.loaded_sequence:
            self.start_relay_sequence(self.loaded_sequence[1])

    def render_relay_sequence(self):
        running = RELAY_SEQUENCE in self.jobs
        editable = not (running or self.exit_requested or self.pc_handoff)
        selected = self.sequence_selector.currentData() is not None
        self.sequence_selector.setEnabled(editable and self.sequence_selector.count() > 0)
        self.relay_name_config.setEnabled(editable)
        self.sequence_new.setEnabled(editable)
        self.sequence_edit.setEnabled(editable and selected)
        problem = self.relay_sequence_blocker()
        summary = ""
        if self.loaded_sequence:
            steps = self.loaded_sequence[1]
            summary = f"{len(steps)} 步"
            if any(step.named for step in steps):
                try:
                    resolve_steps(steps, self.relay_names)
                except ValueError as error:
                    problem = str(error)
                    summary += f"\n{problem}"
                else:
                    names_used = {name for step in steps for name, _ in step.named}
                    wiring = " · ".join(f"K{channel}：{name}" for channel, name
                                        in enumerate(self.relay_names.bindings, 1) if name in names_used)
                    summary += f"\n{wiring}"
            elif any(step.k1 is not None or step.k2 is not None for step in steps):
                summary += " · 按 K1/K2"
            if any(step.output is True for step in steps):
                profile = output_profile(self.state) if self.info and self.state.get("measured", {}).get("fresh") else None
                if not problem and (profile is None or type(self.state.get("applied", {}).get("enabled")) is not bool):
                    problem = "尚未读回有效的主输出设定。"
                if profile is not None and (running or not problem):
                    group, voltage, current = profile
                    summary += f" · 会开启主输出（M{group}）\n{voltage / 1000:.2f} V / {current / 1000:.3f} A"
                else:
                    summary += " · 会开启主输出"
        self.sequence_summary.setText(summary)
        self.sequence_summary.setVisible(bool(summary))
        self.sequence_run.setText("停止" if running else "运行")
        dynamic(self.sequence_run, "role", "stop" if running else "primary")
        self.sequence_run.setEnabled(not self.relay_cancel.is_set() if running else bool(self.loaded_sequence) and not problem)
        self.sequence_run.setToolTip(
            "停止后续动作，不会自动关闭主输出或释放继电器。" if running else
            ("请先选择有效的时序。" if not self.loaded_sequence else problem or "立即运行所选时序。")
        )

    def relay_sequence_blocker(self):
        if not self.info.get("direct_control"):
            return "请先连接支持直接控制的设备。"
        if self.jobs or self.pc_handoff or self.exit_requested:
            return "请先等待当前设备操作完成。"
        applied = self.state.get("applied", {})
        if (time.monotonic() - self.last_receipt >= 6
                or self.state.get("measured", {}).get("fresh") is not True
                or self.state.get("mode") in ("protect", "initializing", "stopping", "unknown")
                or not all(type(applied.get(f"relay{k}")) is bool for k in (1, 2))):
            return "请确认设备状态正常且两路继电器已读回。"
        return ""

    @Slot(object)
    def start_relay_sequence(self, steps):
        try:
            steps = resolve_steps(steps, self.relay_names)
        except ValueError as error:
            self.sequence_status.setText(str(error))
            return
        problem = self.relay_sequence_blocker()
        if problem:
            self.sequence_status.setText(problem)
            return
        device = self.info["device_id"]
        opens_output = any(step.output is True for step in steps)
        profile = output_profile(self.state) if opens_output else None
        applied_enabled = self.state.get("applied", {}).get("enabled")
        if opens_output and (profile is None or type(applied_enabled) is not bool):
            self.sequence_status.setText("未确认设备的输出设定，时序未启动。")
            return
        self.relay_cancel = Event()
        self.sequence_status.setText("正在启动时序…")
        self.launch(RELAY_SEQUENCE, self.worker.client.run_relay_sequence(
            device, steps, self.relay_cancel.is_set, profile, applied_enabled,
        ))

    @Slot()
    def cancel_relay_sequence(self):
        if RELAY_SEQUENCE in self.jobs and not self.relay_cancel.is_set():
            self.relay_cancel.set()
            self.sequence_status.setText("正在停止后续步骤；不会自动关断。")
            self.render()

    @Slot(int, int, int)
    def relay_progress(self, index, total, wait_ms):
        if RELAY_SEQUENCE in self.jobs and not self.relay_cancel.is_set():
            self.sequence_status.setText(f"第 {index}/{total} 步 · 等待 {wait_ms} 毫秒后执行")

    def finish_relay_sequence(self, result, error):
        result = result or {}
        problem = error or result.get("error")
        if problem:
            message = f"时序已停止，完整执行 {result.get('done', 0)} 步。{error_text(problem)}"
            if result.get("pending") == "output":
                message += "请核对主输出的实际状态。"
            elif result.get("pending") is not None:
                message += f"请核对 K{result['pending']} 的实际状态。"
            else:
                message += "请核对当前继电器状态。"
        else:
            message = f"时序已完成，共 {result['done']} 步。"
        self.sequence_status.setText(message)
        self.announce(message, bool(problem and problem != "RELAY_STOPPED"))
        self.render()

    def toggle_relay(self, channel):
        enabled = self.state.get("applied", {}).get(f"relay{channel}")
        if isinstance(enabled, bool):
            self.change(f"继电器 K{channel}", "set_relay", {"channel": channel, "enabled": not enabled})

    def change(self, action, operation, args, revision=False):
        if RELAY_SEQUENCE in self.jobs:
            if operation != "output_off":
                return
            self.cancel_relay_sequence()
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
        busy = bool(self.jobs - {DIAGNOSTIC_ACTION})
        link_pending = bool(self.jobs & {"scan", "connect", "disconnect", "固件升级"})
        link_pending = link_pending or self.pc_handoff
        self.transport.setEnabled(not connected and not link_pending)
        self.target.setEnabled(not connected and not link_pending)
        self.refresh.setEnabled(not connected and not link_pending)
        self.connect_button.setEnabled(not link_pending and (connected or self.target.count() > 0))
        self.connect_button.setText("断开" if connected else "连接")
        self.float_button.setEnabled(not self.exit_requested
                                     and not self.jobs & {"固件升级", "disconnect", RELAY_SEQUENCE})
        self.render_relay_sequence()
        self.update_button.setEnabled(not self.exit_requested and not self.pc_handoff)
        bond_problem = self.bond_recovery_status()
        self.bond_button.setEnabled(not bond_problem)
        self.bond_button.setToolTip(bond_problem or "仅清除此 ESP32 保存的全部蓝牙配对。")
        diagnostic_problem = self.diagnostic_status()
        self.diagnostic_button.setEnabled(not diagnostic_problem)
        self.diagnostic_button.setToolTip(diagnostic_problem or "通过 USB 将固件诊断日志保存到本地。")
        if self.update_dialog is not None:
            self.update_dialog._render()
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
        if self.group_selector.currentIndex() != group_index:
            with QSignalBlocker(self.group_selector):
                self.group_selector.setCurrentIndex(group_index)
        self.group_status.setVisible(GROUP_SWITCH in self.jobs)
        selected_note = self.group_selector.currentData(GROUP_NOTE_ROLE) or ""
        selected_tooltip = self.group_selector.currentData(Qt.ItemDataRole.ToolTipRole) or ""
        switch_tip = "选中另一组时自动关闭主输出。"
        self.group_selector.setToolTip(selected_tooltip.replace("</qt>", f"<br>{switch_tip}</qt>")
                                       if selected_tooltip else switch_tip)
        self.group_note.setVisible(bool(selected_note))
        if selected_note:
            self.group_note.setText(self.group_note.fontMetrics().elidedText(
                "备注  " + " ".join(selected_note.splitlines()), Qt.TextElideMode.ElideRight, self.group_note.width(),
            ))
            self.group_note.setToolTip(selected_tooltip)
        normal_control = direct and active and not busy and not self.pc_handoff
        group_ready = enabled is not None and group_index >= 0
        self.group_selector.setEnabled(bool(self.group_switch_available()))
        self.group_edit.setEnabled(connected and group_ready and not busy and not self.pc_handoff)
        settable = normal_control and group_ready
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
        self.sampling.setEnabled(not busy and not self.pc_handoff and (not connected or bool(self.info.get("stream_period"))))
        self.sampling.setToolTip("蓝牙档位取决于连接协商和 SX 串口速率。曲线底部显示实际频率。" if self.kind == "ble"
                                 else "选择 USB 目标采样频率。曲线底部显示实际频率。")

        for channel, widget in self.relay.items():
            engaged = applied.get(f"relay{channel}")
            description = "吸合" if engaged is True else "释放" if engaged is False else "状态未知"
            name = self.relay_names.bindings[channel - 1]
            if name is None:
                widget.setText(f"K{channel}  ·  {description}")
                widget.setToolTip("")
            else:
                width = CONTROL_RAIL_WIDTH // 2 - 34
                short = widget.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, width)
                widget.setText(f"{short} · {description}")
                widget.setToolTip(f"K{channel}：{name} · {description}")
            widget.setAccessibleName(widget.toolTip() or widget.text())
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
            "power": (measured.get("power_mW"), 2),
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
        can_write = (settable and available and bool(step) and enabled is False
                     and type(v) is int and type(i) is int)
        for widget in (self.ovp, self.ocp):
            widget.setEnabled(can_write)
        can_write_opp = settable and opp_available and power is not None and enabled is False
        self.opp.setEnabled(can_write_opp)
        self.apply_protection.setEnabled(can_write or can_write_opp)
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
        self.cancel_relay_sequence()
        if self.pc_handoff:
            event.ignore()
            return
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
        if self.update_dialog is not None:
            self.update_dialog.close()
        self.worker.stop()
        event.accept()


def main():
    worker_exit = run_upgrade_worker()
    if worker_exit is not None:
        return worker_exit
    app = QApplication(sys.argv)
    app.setApplicationName("直流电源控制台")
    app.setApplicationVersion(PC_VERSION)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLES)
    app.setWindowIcon(icon())
    try:
        handoff = pending_handoff()
    except (OSError, ValueError, RuntimeError):
        return 1
    window = Window()
    window.show()
    if smoke_startup(window, app) or (handoff and smoke_handoff(handoff, app)):
        return app.exec()
    if handoff:
        window.pc_handoff = True
        window.setEnabled(False)
        window.announce("正在完成客户端切换…")
        timer = QTimer(window)
        timer.setInterval(200)
        acknowledged = False

        def check_switch():
            if acknowledged and handoff.resume and window.info.get("device_id") != handoff.resume["device_id"]:
                handoff.reject("新版客户端连接在切换时中断。")
            state = handoff.poll()
            if state == "pending":
                return
            timer.stop()
            if state == "abort":
                # Do not send the normal graceful disconnect during a rollback.
                window.worker.stop()
                app.exit(1)
                return
            window.pc_handoff = False
            window.setEnabled(True)
            if state == "complete" and handoff.legacy:
                window.announce("已恢复 PowerModule-PC.exe。下次请从原快捷方式启动。")
            elif handoff.recovery:
                window.announce("升级未能完成，已恢复可运行的客户端。", True)
            else:
                window.announce("新版已启动，但未能清理旧版文件；原文件已保留。" if state == "cleanup-failed"
                                else "客户端升级完成。", state == "cleanup-failed")
            window.render()

        def ready(error):
            nonlocal acknowledged
            if error or not window.isVisible() or window.windowIcon().isNull() or PC_VERSION not in window.windowTitle():
                handoff.reject(error or "新版未能完成界面初始化。")
                return
            handoff.acknowledge()
            acknowledged = True

        def start_switch():
            window.resume_pc_link(handoff.resume, ready)

        timer.timeout.connect(check_switch)
        timer.start()
        QTimer.singleShot(0, start_switch)
    if len(sys.argv) == 3 and sys.argv[1] == "--release-smoke-test":
        from pathlib import Path

        def verify_frozen_startup():
            result = 1
            try:
                import bleak.backends.winrt.client
                import bleak.backends.winrt.scanner

                panel = FloatingPanel()
                panel.show()
                app.processEvents()
                compact = panel.isVisible() and panel.width() < 260 and panel.height() < 90
                panel.hide()
                editor = RelaySequenceDialog(window.sequence_store, parent=window, relay_names=window.relay_names.names)
                sequence_ok = (window.sequence_store.directory == Path(sys.executable).resolve().parent / "sequences"
                               and window.sequence_selector.isVisible() and not hasattr(window, "sequence_load")
                               and window.sequence_run.text() == "运行" and not window.sequence_run.isEnabled()
                               and editor.table.columnCount() == 6 and editor.table.cellWidget(0, 5).count() == 3
                               and window.relay_name_config.isVisible())
                editor.close()
                marker = Path(sys.argv[2])
                if (window.isVisible() and not window.windowIcon().isNull() and PC_VERSION in window.windowTitle()
                        and compact and sequence_ok):
                    marker.write_text(PC_VERSION, encoding="ascii")
                    result = 0
                else:
                    marker.write_text(f"main={window.isVisible()} floating={panel.width()}x{panel.height()} sequence={sequence_ok}",
                                      encoding="ascii")
            finally:
                window.close()
                app.exit(result)

        QTimer.singleShot(0, verify_frozen_startup)
    elif len(sys.argv) == 3 and sys.argv[1] == "--release-catalog-smoke":
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from pc_client.online_updates import cache_firmware, catalog_revision, check_catalog

        def verify_frozen_catalog():
            marker = Path(sys.argv[2])
            result = 1
            try:
                revision = catalog_revision()
                pc = check_catalog("pc", revision=revision)
                firmware = check_catalog("firmware", revision=revision)
                with TemporaryDirectory() as tmp:
                    cache_firmware(firmware, tmp)
                marker.write_text(f"{pc.version} {firmware.version}", encoding="ascii")
                result = 0
            except Exception as error:
                marker.write_text(str(error), encoding="utf-8")
            finally:
                window.close()
                app.exit(result)

        QTimer.singleShot(0, verify_frozen_catalog)
    return app.exec()
