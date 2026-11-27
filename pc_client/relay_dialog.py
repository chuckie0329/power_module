from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout,
    QFormLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget, QVBoxLayout,
)

from pc_client.relay_sequence import (
    MAX_RELAY_NAME, MAX_STEPS, MAX_WAIT_MS, RelayNames, RelayStep, validate_steps,
)


class RelaySequenceDialog(QDialog):
    def __init__(self, store, name=None, steps=(), parent=None, *, relay_names=None):
        super().__init__(parent)
        self.setWindowTitle("编辑时序" if name else "新建时序")
        self.named_mode = relay_names is not None and not any(
            step.k1 is not None or step.k2 is not None for step in steps)
        self.relay_names = list(relay_names or ())
        self.resize(840 if self.named_mode else 690, 430)
        self.store = store
        self.original = name
        self.saved_name = None
        layout = QVBoxLayout(self)
        explanation = QLabel(
            ("每步先等待，再依次执行：关闭主输出 → 动作 1 → 动作 2 → 开启主输出。"
             "未绑定的名称可以保存，但不能运行。"
             if self.named_mode else
             "此时序按物理通道执行。每步先等待，再依次执行：关闭主输出 → K1 → K2 → 开启主输出。")
            + "保存不会运行时序。"
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
        if self.named_mode:
            self.new_relay = QPushButton("添加继电器名称…")
            self.new_relay.setAutoDefault(False)
            self.new_relay.clicked.connect(self.add_relay_name)
            self.new_relay.setEnabled(parent is not None and hasattr(parent, "edit_relay_names"))
            layout.addWidget(self.new_relay)
        self.table = QTableWidget(0, 6 if self.named_mode else 4)
        self.table.setHorizontalHeaderLabels(
            ["等待（毫秒）", "继电器名称 1", "动作 1", "继电器名称 2", "动作 2", "主输出"]
            if self.named_mode else ["执行前等待（毫秒）", "K1", "K2", "主输出"])
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
        if self.named_mode:
            actions = (*step.named, *((None, None),) * (2 - len(step.named)))
            for col, (name, value) in zip((1, 3), actions):
                target = QComboBox()
                target.setAccessibleName(f"继电器名称 {(col + 1) // 2}")
                self.populate_names(target, name)
                self.table.setCellWidget(row, col, target)
                self.table.setCellWidget(row, col + 1, self.action_box(col + 1, value))
            self.table.setCellWidget(row, 5, self.action_box(5, step.output, output=True))
        else:
            for col, value in enumerate((step.k1, step.k2, step.output), 1):
                self.table.setCellWidget(row, col, self.action_box(col, value, output=col == 3))

    def action_box(self, col, value, *, output=False):
        action = QComboBox()
        action.setAccessibleName("主输出动作" if output else
                                 f"继电器动作 {col // 2}" if self.named_mode else f"K{col} 动作")
        on, off = ("开启", "关闭") if output else ("吸合", "释放")
        for label, data in (("保持不变", None), (on, True), (off, False)):
            action.addItem(label, data)
        action.setCurrentIndex(0 if value is None else 1 if value else 2)
        return action

    def populate_names(self, target, selected):
        target.clear()
        target.addItem("未选择", None)
        for name in self.relay_names:
            target.addItem(name, name)
        if selected is not None and selected not in self.relay_names:
            target.addItem(f"{selected}（不在名称列表）", selected)
        target.setCurrentIndex(max(0, target.findData(selected)))

    def add_relay_name(self):
        editor = self.parent()
        editor.edit_relay_names()
        self.relay_names = list(editor.relay_names.names)
        for row in range(self.table.rowCount()):
            for col in (1, 3):
                target = self.table.cellWidget(row, col)
                self.populate_names(target, target.currentData())

    def step(self, row):
        delay = self.table.cellWidget(row, 0)
        delay.interpretText()
        if not self.named_mode:
            return RelayStep(delay.value(), *(self.table.cellWidget(row, col).currentData() for col in (1, 2, 3)))
        actions = []
        for col in (1, 3):
            name = self.table.cellWidget(row, col).currentData()
            value = self.table.cellWidget(row, col + 1).currentData()
            if value is not None:
                if name is None:
                    raise ValueError(f"第 {row + 1} 步：请为动作选择继电器名称。")
                actions.append((name, value))
        return RelayStep(delay.value(), None, None, self.table.cellWidget(row, 5).currentData(), tuple(actions))

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
            try:
                step = self.step(row)
            except ValueError as error:
                self.status.setText(str(error))
                return
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


class RelayNamesDialog(QDialog):
    def __init__(self, settings, save, parent=None):
        super().__init__(parent)
        self.setWindowTitle("继电器名称与接线")
        self.resize(430, 230)
        self.names = settings.names
        self.save_callback = save
        layout = QVBoxLayout(self)
        note = QLabel("按实际接线为 K1、K2 选择名称。换线后只改这里；保存不会操作设备。")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.ports = (QComboBox(), QComboBox())
        form.addRow("K1", self.ports[0])
        form.addRow("K2", self.ports[1])
        layout.addLayout(form)
        self.populate(settings.bindings)
        add_row = QHBoxLayout()
        self.new_name = QLineEdit()
        self.new_name.setMaxLength(MAX_RELAY_NAME)
        self.new_name.setPlaceholderText("例如：BOOT 或 RESET")
        self.new_name.setAccessibleName("新继电器名称")
        add_row.addWidget(self.new_name, 1)
        add = QPushButton("添加到列表")
        add.setAutoDefault(False)
        add.clicked.connect(self.add_name)
        self.new_name.returnPressed.connect(self.add_name)
        add_row.addWidget(add)
        layout.addLayout(add_row)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch()
        for caption, callback in (("取消", self.reject), ("保存", self.save)):
            button = QPushButton(caption)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)

    def populate(self, bindings):
        for port, name in zip(self.ports, bindings):
            port.clear()
            port.addItem("未绑定", None)
            for item in self.names:
                port.addItem(item, item)
            port.setCurrentIndex(max(0, port.findData(name)))

    def bindings(self):
        return tuple(port.currentData() for port in self.ports)

    def add_name(self):
        value = self.new_name.text().strip()
        try:
            settings = RelayNames((*self.names, value))
        except ValueError as error:
            self.status.setText(str(error))
            return
        selected = self.bindings()
        self.names = settings.names
        self.populate(selected)
        self.new_name.clear()
        self.status.setText(f"已添加「{value}」，保存后生效。")

    def save(self):
        try:
            self.save_callback(RelayNames(self.names, self.bindings()))
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.accept()
