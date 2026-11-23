from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


STYLES = """
QWidget { color: #142f40; font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 12px; }
QMainWindow, QDialog, #page { background: #edf3f7; }
QFrame#header { background: #f9fbfd; border-bottom: 1px solid #d7e4ec; }
QFrame#card { background: #ffffff; border: 1px solid #d9e5ec; border-radius: 12px; }
QFrame#meter { background: #143b51; border: 1px solid #1b4a63; border-radius: 11px; }
QFrame#meter[accent="true"] { background: #0d5877; border-color: #0d5877; }
QFrame#meter QLabel { color: #ffffff; background: transparent; }
QFrame#meter QLabel[role="muted"] { color: #b6d5e3; }
QLabel { background: transparent; }
QLabel[role="title"] { font-size: 26px; font-weight: 700; }
QLabel[role="section"] { font-size: 15px; font-weight: 700; }
QLabel[role="muted"] { color: #526a7b; }
QLabel[role="metric"] { font-family: "Segoe UI"; font-size: 35px; font-weight: 600; }
QLabel[role="average"] { font-family: "Segoe UI"; font-size: 23px; font-weight: 650; color: #086487; }
QLabel[role="unit"] { font-family: "Segoe UI"; font-size: 16px; font-weight: 650; }
QLabel[role="pill"] { background: #e9f0f6; border-radius: 9px; color: #36566c; padding: 5px 10px; }
QLabel[role="pill"][tone="ok"] { color: #0e644f; background: #dff3eb; }
QLabel[role="pill"][tone="warn"] { color: #854d14; background: #fdf0dc; }
QLabel#message { color: #3b566a; padding: 4px 2px; }
QLabel#message[level="error"] { color: #a13037; }
QPushButton {
    background: #f8fbfd; border: 1px solid #cbdce7; border-radius: 7px;
    min-height: 31px; padding: 1px 12px; font-weight: 600;
}
QPushButton:hover { background: #e9f4fb; border-color: #9bc8de; }
QPushButton:pressed { background: #dcecf5; }
QPushButton:disabled { background: #f2f5f7; color: #8193a0; border-color: #dce5eb; }
QPushButton[role="primary"] { color: #ffffff; background: #086e92; border-color: #086e92; }
QPushButton[role="primary"]:hover { background: #075a79; border-color: #075a79; }
QPushButton[role="primary"]:disabled { background: #e9f0f4; color: #80909c; border-color: #dce5eb; }
QPushButton[role="stop"] { color: #9e252e; background: #fff4f4; border-color: #ecbfc3; }
QPushButton[role="stop"]:hover { background: #fce4e5; }
QPushButton[role="stop"]:disabled { background: #f2f5f7; color: #8193a0; border-color: #dce5eb; }
QPushButton[active="true"] { color: #0b604c; background: #e1f5ee; border-color: #99d4bf; }
QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit, QPlainTextEdit {
    background: #ffffff; border: 1px solid #cbdce7; border-radius: 7px;
    min-height: 30px; padding: 1px 8px; selection-background-color: #167a9f;
}
QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled, QLineEdit:disabled {
    background: #f1f5f8; color: #7a8b98; border-color: #dce5eb;
}
QComboBox QAbstractItemView { background: white; selection-background-color: #e1f0fa; selection-color: #142f40; }
QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus, QPlainTextEdit:focus { border-color: #1684ad; }
QToolTip { color: #142f40; background: #ffffff; border: 1px solid #bdcfdb; padding: 5px; }
"""


def icon():
    canvas = QPixmap(64, 64)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#0a668a"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(2, 2, 60, 60), 13, 13)
    painter.setPen(QPen(QColor("#ffffff"), 4.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    wave = QPainterPath(QPointF(14, 35))
    wave.lineTo(24, 35)
    wave.lineTo(30, 21)
    wave.lineTo(37, 44)
    wave.lineTo(43, 30)
    wave.lineTo(51, 30)
    painter.drawPath(wave)
    painter.end()
    return QIcon(canvas)
