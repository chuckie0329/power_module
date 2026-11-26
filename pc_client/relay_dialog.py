from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget, QVBoxLayout,
)

from pc_client.relay_sequence import (
    MAX_STEPS, MAX_WAIT_MS, RelayStep, validate_steps,
)


class RelaySequenceDialog(QDialog):
    def __init__(self, store, name=None, steps=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑时序" if name else "新建时序")
        self.resize(690, 430)
        self.store = store
        self.original = name
        self.saved_name = None
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "每步先等待，再依次执行并核对读回。关主输出排在 K1/K2 之前，开主输出排在之后。"
            "保存只更新本地时序，不会运行。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        naming = QHBoxLayout()
        title = QLabel("时序名称")
        self.name = QLineEdit(name or "")
        self.name.setMaxLength(64)
        self.name.setPlaceholderText("例如：上电测试")
        title.setBuddy(self.name)
        naming.addWidget(title)
        naming.addWidget(self.name, 1)
        layout.addLayout(naming)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["执行前等待（毫秒）", "K1", "K2", "主输出"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        edit = QHBoxLayout()
        for caption, action in (
            ("添加步骤", self.add_step), ("删除步骤", self.remove_step),
            ("上移", lambda: self.move_step(-1)), ("下移", lambda: self.move_step(1)),
        ):
            widget = QPushButton(caption)
            widget.setAutoDefault(False)
            widget.clicked.connect(action)
            edit.addWidget(widget)
        layout.addLayout(edit)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.save = QPushButton("保存")
        self.save.setAutoDefault(False)
        self.save.clicked.connect(self.save_file)
        actions.addWidget(self.save)
        layout.addLayout(actions)
        for row, step in enumerate(steps or (RelayStep(0, None, None),)):
            self.insert_step(row, step)
        self.table.selectRow(0)

    def insert_step(self, row, step):
        self.table.insertRow(row)
        delay = QSpinBox()
        delay.setRange(0, MAX_WAIT_MS)
        delay.setSingleStep(100)
        delay.setValue(step.wait_ms)
        delay.setAccessibleName("执行前等待（毫秒）")
        self.table.setCellWidget(row, 0, delay)
        for col, value in enumerate((step.k1, step.k2, step.output), 1):
            action = QComboBox()
            action.setAccessibleName(f"K{col} 动作" if col != 3 else "主输出动作")
            on, off = ("开启", "关闭") if col == 3 else ("吸合", "释放")
            for label, data in (("保持不变", None), (on, True), (off, False)):
                action.addItem(label, data)
            action.setCurrentIndex(0 if value is None else 1 if value else 2)
            self.table.setCellWidget(row, col, action)

    def step(self, row):
        delay = self.table.cellWidget(row, 0)
        delay.interpretText()
        return RelayStep(delay.value(), *(self.table.cellWidget(row, col).currentData() for col in (1, 2, 3)))

    def steps(self):
        return validate_steps(self.step(row) for row in range(self.table.rowCount()))

    def add_step(self):
        row = self.table.rowCount()
        if row < MAX_STEPS:
            self.insert_step(row, RelayStep(0, None, None))
            self.table.selectRow(row)
        else:
            self.status.setText(f"最多 {MAX_STEPS} 个步骤。")

    def remove_step(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def move_step(self, offset):
        row = self.table.currentRow()
        dest = row + offset
        if row >= 0 and 0 <= dest < self.table.rowCount():
            step = self.step(row)
            self.table.removeRow(row)
            self.insert_step(dest, step)
            self.table.selectRow(dest)

    def save_file(self):
        try:
            self.saved_name = self.store.save(self.name.text(), self.steps(), self.original)
        except PermissionError:
            self.status.setText("程序目录不可写，请把客户端放到有写权限的文件夹后再保存。")
            return
        except (OSError, ValueError) as error:
            self.status.setText(f"未保存：{error}")
            return
        self.accept()
