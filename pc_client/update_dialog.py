"""Explicit, non-blocking check and download dialog."""

from threading import Thread

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QDialog, QFileDialog, QGridLayout, QLabel, QPushButton

from pc_client.online_updates import check_catalog, download, version_key
from pc_client.version import PC_VERSION


class UpdateDialog(QDialog):
    done = Signal(object, str)

    def __init__(self, parent, firmware):
        super().__init__(parent)
        self.firmware = firmware
        self.operation = ""
        self.updates = {"pc": None, "firmware": None}
        self.checked = None
        self.buttons = {}
        self.rows = {}
        self.setWindowTitle("检查更新")
        layout = QGridLayout(self)
        self.setMinimumWidth(510)
        for row, (kind, title, current) in enumerate((
            ("pc", "Windows 客户端", f"v{PC_VERSION}"), ("firmware", "ESP32 固件", f"v{firmware}" if firmware else "未连接"),
        )):
            self.rows[kind] = QLabel(f"{title}：{current}；尚未检查")
            self.rows[kind].setTextFormat(Qt.TextFormat.PlainText)
            self.rows[kind].setWordWrap(True)
            layout.addWidget(self.rows[kind], row, 0)
            action = QPushButton("下载")
            action.setEnabled(False)
            action.clicked.connect(lambda _checked=False, k=kind: self.save(k))
            self.buttons[kind] = action
            layout.addWidget(action, row, 1)
        layout.addWidget(QLabel("只下载并校验，绝不自动运行客户端或安装固件。"), 2, 0, 1, 2)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status, 3, 0, 1, 2)
        self.check = QPushButton("检查")
        self.check.clicked.connect(self.check_all)
        layout.addWidget(self.check, 4, 0)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        layout.addWidget(close, 4, 1)
        self.done.connect(self.finished_operation)

    def set_firmware(self, firmware):
        self.firmware = firmware
        self._render()

    def _busy(self, operation, fn):
        if self.operation:
            return
        self.operation = operation
        self.check.setEnabled(False)
        for button in self.buttons.values():
            button.setEnabled(False)

        def run():
            result, error = None, ""
            try:
                result = fn()
            except Exception as exc:
                error = str(exc) or "操作失败。"
            try:
                self.done.emit(result, error)
            except RuntimeError:
                pass  # The whole application already exited.

        Thread(target=run, daemon=True).start()

    def check_all(self):
        self.status.setText("正在检查两个独立的稳定版本…")

        def get():
            result = {}
            for kind in self.updates:
                try:
                    result[kind] = check_catalog(kind), ""
                except Exception:
                    result[kind] = None, "无法读取或验证清单"
            return result

        self._busy("check", get)

    def save(self, kind):
        update = self.updates[kind]
        if not update:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存已验证的下载", update.filename,
                                              "Windows 客户端 (*.exe)" if kind == "pc" else "ESP32 固件 (*.bin)")
        if not path:
            return
        self.status.setText("下载并校验中…")
        self._busy(kind, lambda: download(update, path))

    def _render(self):
        for kind, title, current in (
            ("pc", "Windows 客户端", PC_VERSION), ("firmware", "ESP32 固件", self.firmware),
        ):
            update, problem = (None, "") if self.checked is None else self.checked[kind]
            caption = "尚未检查" if self.checked is None else (
                problem or ("暂无稳定版本" if update is None else f"稳定版 v{update.version}")
            )
            notes = update.notes if update else ""
            if update and current:
                try:
                    if version_key(update.version) <= version_key(current):
                        caption += " · 已是相同或更新版本"
                        update = None
                except ValueError:
                    update = None
                    caption += " · 设备版本未知"
            self.updates[kind] = update
            self.rows[kind].setText(f"{title}：{('v' + current) if current else '未连接'}；{caption}")
            if notes:
                self.rows[kind].setText(self.rows[kind].text() + "\n" + notes)
            self.buttons[kind].setEnabled(not self.operation and update is not None)

    @Slot(object, str)
    def finished_operation(self, result, error):
        operation, self.operation = self.operation, ""
        self.check.setEnabled(True)
        if operation == "check" and not error:
            self.checked = result
            self.status.setText("选择要下载的项目。")
        elif error:
            self.status.setText(f"更新操作失败：{error}")
        else:
            self.status.setText("下载已通过校验。请退出旧客户端后手动运行新客户端。"
                                if operation == "pc" else "下载已通过校验。如确实要安装，请使用主窗口的「固件升级」。")
        self._render()
