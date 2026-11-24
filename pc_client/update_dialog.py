"""One non-modal upgrade entry; firmware install always remains explicit."""
from pathlib import Path
import sys
from threading import Thread

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QDialog, QGridLayout, QLabel, QPushButton

from pc_client.online_updates import cache_firmware, catalog_revision, check_catalog, download_client, version_key
from pc_client.pc_upgrade import launch, verify_predecessor
from pc_client.version import PC_VERSION


class UpdateDialog(QDialog):
    done = Signal(object, str)

    def __init__(self, parent, firmware):
        super().__init__(parent)
        self.firmware = firmware
        self.operation = ""
        self.checked = None
        self.cached = None
        self.updates = {}
        self.buttons, self.rows = {}, {}
        self.directory = Path(sys.executable).parent if sys.platform == "win32" and getattr(sys, "frozen", False) else None
        self.setWindowTitle("升级")
        self.setMinimumWidth(510)
        layout = QGridLayout(self)
        for row, kind in enumerate(("pc", "firmware")):
            self.rows[kind] = QLabel()
            self.rows[kind].setTextFormat(Qt.TextFormat.PlainText)
            self.rows[kind].setWordWrap(True)
            layout.addWidget(self.rows[kind], row, 0)
            button = QPushButton("升级")
            button.clicked.connect(self.upgrade_pc if kind == "pc" else self.upgrade_firmware)
            self.buttons[kind] = button
            layout.addWidget(button, row, 1)
        self.status = QLabel("尚未检查。")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status, 2, 0, 1, 2)
        self.check = QPushButton("重新检查")
        self.check.clicked.connect(self.check_all)
        layout.addWidget(self.check, 3, 0)
        self.manual = QPushButton("选择本地固件…")
        self.manual.clicked.connect(lambda: self.install_firmware())
        layout.addWidget(self.manual, 3, 1)
        self.done.connect(self.finished_operation)
        self._render()

    def set_firmware(self, firmware):
        self.firmware = firmware
        self._render()

    def _busy(self, operation, fn):
        self.operation = operation
        self._render()

        def run():
            result, error = None, ""
            try:
                result = fn()
            except Exception as exc:
                error = str(exc) or "操作失败。"
            try:
                self.done.emit(result, error)
            except RuntimeError:
                pass

        Thread(target=run, daemon=True).start()

    def check_all(self):
        if self.operation:
            return
        self.cached = None
        self.status.setText("正在检查客户端和固件版本…")

        def get():
            result = {}
            try:
                revision = catalog_revision()
            except (OSError, ValueError):
                return dict.fromkeys(("pc", "firmware"), (None, "无法验证最新清单版本"))
            for kind in ("pc", "firmware"):
                try:
                    result[kind] = check_catalog(kind, revision=revision), ""
                except Exception:
                    result[kind] = None, "无法读取或验证清单"
            return result

        self._busy("check", get)

    def fetch_firmware(self):
        update = self.updates["firmware"]
        self.status.setText("正在校验客户端目录中的固件，必要时下载新版…")
        self._busy("cache", lambda: cache_firmware(update, self.directory))

    def upgrade_firmware(self):
        if self.cached:
            self.install_firmware(self.cached, self.updates["firmware"])
        else:
            self.fetch_firmware()

    def install_firmware(self, filename=None, verified=None):
        if self.parent().start_firmware_update(filename, verified):
            self.hide()

    def upgrade_pc(self):
        if problem := self.parent().pc_upgrade_blocker():
            self.status.setText(problem)
            return
        update = self.updates["pc"]
        self.status.setText("下载并校验新版客户端，原客户端继续运行…")
        def get_client():
            verify_predecessor(update.version)
            return download_client(update, self.directory)

        self._busy("pc-download", get_client)

    def prepare_pc(self, new_path):
        update = self.updates["pc"]
        self.operation = "pc-disconnect"
        self.status.setText("如有连接，正在断开原设备以切换客户端…")
        self._render()

        def disconnected(resume, error):
            if error:
                self.recover_pc(error)
                return
            self.status.setText("正在启动新版。如原先已连接，将验证并重连同一台设备…")
            self._busy("pc-launch", lambda: launch(new_path, update.sha256, update.version, resume=resume))

        self.parent().prepare_pc_switch(disconnected)

    def recover_pc(self, error):
        self.operation = "pc-recover"

        def finished(reconnect_error):
            self.operation = ""
            self.status.setText(f"切换失败，已保留旧客户端：{error}" +
                                (f"\n重连原设备失败：{reconnect_error}" if reconnect_error else ""))
            self.show()
            self._render()

        self.parent().cancel_pc_switch(finished)

    def _render(self):
        for kind, title, current in (
            ("pc", "Windows 客户端", PC_VERSION), ("firmware", "ESP32 固件", self.firmware),
        ):
            update, problem = (None, "") if self.checked is None else self.checked[kind]
            caption = "尚未检查" if self.checked is None else (
                problem or (f"稳定版 v{update.version}" if update else "暂无稳定版本")
            )
            notes = update.notes if update else ""
            if update and current:
                try:
                    if version_key(update.version) <= version_key(current):
                        caption += " · 当前版本无需升级"
                        update = None
                except ValueError:
                    update = None
                    caption += " · 版本未知"
            self.updates[kind] = update
            if kind == "firmware" and update and self.cached:
                caption += " · 本地已校验"
            self.rows[kind].setText(f"{title}：{('v' + current) if current else '未连接'}；{caption}" +
                                    (f"\n{notes}" if notes else ""))
            button = self.buttons[kind]
            button.setText("下载固件" if kind == "firmware" and not self.cached else "升级")
            available = bool(update and self.directory and not self.operation)
            if kind == "firmware" and self.cached:
                available = available and self.parent().firmware_upgrade_allowed()
            button.setEnabled(available)
        self.check.setEnabled(not self.operation)
        self.manual.setEnabled(not self.operation and self.parent().firmware_upgrade_allowed())

    @Slot(object, str)
    def finished_operation(self, result, error):
        operation, self.operation = self.operation, ""
        if operation == "pc-launch":
            if error:
                self.recover_pc(error)
                return
            try:
                result.commit()
            except Exception as exc:
                result.abort()
                self.recover_pc(str(exc))
                return
            self.parent().close_for_pc_switch()
            return
        if error:
            self.status.setText(f"更新操作失败：{error}")
        elif operation == "check":
            self.checked = result
            self._render()
            self.status.setText("升级客户端会打开新版；固件只有点「升级」并确认才会安装。")
            if self.directory and self.updates["firmware"]:
                self.fetch_firmware()
                return
            if self.directory is None:
                self.status.setText("源码运行不支持自动替换文件；可手动选择本地固件。")
        elif operation == "cache":
            self.cached = result
            self.status.setText("固件已校验。连接设备并关闭主输出和两路继电器后，可手动升级。")
        elif operation == "pc-download":
            self.prepare_pc(result)
            return
        self._render()
