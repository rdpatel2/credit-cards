"""One semantic palette for every finance screen, native control, and painter."""
import re
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

LIGHT = {
    '#f5f7f6':'#f3f6fa', '#233d38':'#233146', '#183a2d':'#17283f',
    '#334e40':'#34465d', '#4d6256':'#607087', '#52665a':'#607087',
    '#236347':'#197b6b', '#236349':'#197b6b', '#187148':'#197b6b', '#194f38':'#126454',
    '#e1e8e2':'#dfe6ee', '#d8e1da':'#dfe6ee', '#dce4dd':'#dfe6ee',
    '#edf4ed':'#eaf3f4', '#dce8dd':'#d8e8e9', '#e6f2e9':'#e4f3ee',
    '#eef4ef':'#edf2f7', '#edf1ee':'#e9eef4', '#edf5ee':'#e5f3ef',
    '#f5f9f6':'#f4f7fa', '#fafcf9':'#f8fafc', '#eef5ef':'#edf5f4',
    '#102e29':'#152332', '#1a3e34':'#20364a', '#254b3e':'#294759',
}
DARK = {
    '#f5f7f6':'#101923', '#ffffff':'#192533', '#233d38':'#e4edf8', '#183a2d':'#f0f5fc',
    '#334e40':'#d4e2f1', '#4d6256':'#a4b5c8', '#52665a':'#a4b5c8', '#69857a':'#a4b5c8',
    '#236347':'#70d9b7', '#236349':'#70d9b7', '#187148':'#70d9b7', '#194f38':'#94e7cb',
    '#e1e8e2':'#304252', '#d8e1da':'#304252', '#dce4dd':'#304252', '#dce8dd':'#304252',
    '#edf4ed':'#192f37', '#e6f2e9':'#23433d', '#eef4ef':'#233545', '#edf1ee':'#293a4a',
    '#edf5ee':'#25463f', '#f5f9f6':'#223544', '#fafcf9':'#1d2c3b', '#eef5ef':'#223b40',
    '#eef4ef':'#263847', '#eef5ef':'#223b40', '#dceadf':'#2d4c45', '#dce6df':'#293d48',
    '#edf1ee':'#253545', '#7b9484':'#637f91', '#98b6a1':'#648b88', '#e6ece7':'#304252',
    '#b5cbbb':'#597b80', '#7b9e89':'#6caca0', '#398161':'#70d9b7', '#7b9484':'#637f91',
    '#102e29':'#0b131d', '#1a3e34':'#162b3b', '#254b3e':'#203d4a',
    '#fde7e7':'#492831', '#792d2d':'#ffbac4', '#e9bbbb':'#7b424d',
    '#fff4ce':'#463c26', '#674d0d':'#f1d692', '#e6d18a':'#79663a',
    '#e3f3e7':'#213e34', '#205735':'#9fe8bd', '#a8d4b4':'#446c57', '#805014':'#f1d692',
    '#fff1c2':'#463c26', '#594000':'#f1d692',
}


def is_dark():
    app = QApplication.instance()
    return bool(app and app.property('financeTheme') == 'dark')


def color(value, dark=None):
    return (DARK if (is_dark() if dark is None else dark) else LIGHT).get(value.lower(), value)


def themed(value, dark=None):
    return re.sub(r'#[0-9a-fA-F]{6}', lambda match: color(match.group(), dark), value)


def stylesheet(base, dark):
    result = themed(base, dark).replace('background: white;', f'background: {color("#ffffff", dark)};')
    result += '\nQWidget#workspaceBody, QWidget#scrollContent { background: transparent; }\n'
    if dark:
        result += '''
QLabel#brand { color: #f0f5fc; }
QPushButton#primary { color: #10241e; }
QPushButton#primary:hover { background: #94e7cb; }
QToolTip { color: #e4edf8; background: #233545; border: 1px solid #304252; }
QCheckBox::indicator { background: #192533; border: 1px solid #637f91; border-radius: 4px; }
QCheckBox::indicator:checked { background: #70d9b7; border-color: #70d9b7; }
'''
    return result


def palette(dark):
    p = QPalette()
    for role, value in {'Window':'#f5f7f6','WindowText':'#233d38','Base':'#ffffff',
                       'AlternateBase':'#eef4ef','Text':'#233d38','Button':'#ffffff','ButtonText':'#233d38',
                       'Highlight':'#236347','HighlightedText':'#ffffff','PlaceholderText':'#52665a',
                       'ToolTipBase':'#102e29','ToolTipText':'#b4cdc4','Link':'#236347',
                       'Light':'#ffffff','Midlight':'#e1e8e2','Mid':'#7b9484','Dark':'#52665a','Shadow':'#102e29'}.items():
        p.setColor(getattr(QPalette.ColorRole, role), QColor(color(value, dark)))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(color('#52665a', dark)))
    return p
