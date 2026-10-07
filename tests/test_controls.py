"""User input stays deliberate when searching or scrolling through forms."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QDate
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from card_compass.ui import ScrollSafeComboBox, ScrollSafeDoubleSpinBox, ScrollSafeDateEdit, STYLE, light_palette
from card_compass.import_ui import category_picker


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setStyleSheet(STYLE)
    instance.setPalette(light_palette())
    return instance


def wheel(widget):
    event = QWheelEvent(QPointF(10, 10), QPointF(widget.mapToGlobal(QPoint(10, 10))),
                        QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize('focused', [False, True])
def test_wheel_preserves_dropdown_numeric_and_date_values(app, focused):
    combo = ScrollSafeComboBox()
    combo.addItems(['First', 'Second', 'Third'])
    combo.setCurrentIndex(1)
    number = ScrollSafeDoubleSpinBox()
    number.setValue(42)
    date = ScrollSafeDateEdit(QDate(2026, 9, 17))
    for control in (combo, number, date):
        control.show()
        control.setFocus() if focused else control.clearFocus()
        app.processEvents()
        wheel(control)
        if isinstance(control, (ScrollSafeDoubleSpinBox, ScrollSafeDateEdit)):
            wheel(control.lineEdit())
    assert combo.currentIndex() == 1
    assert number.value() == 42
    assert date.date() == QDate(2026, 9, 17)
    QTest.keyClick(combo, Qt.Key.Key_Down)
    QTest.keyClick(number, Qt.Key.Key_Up)
    assert combo.currentIndex() == 2
    assert number.value() == 43
    for control in (combo, number, date):
        control.close()


def test_category_contains_search_and_keyboard_selection(app):
    combo = category_picker(['groceries', 'dining', 'gas'])
    combo.show()
    combo.lineEdit().selectAll()
    QTest.keyClicks(combo.lineEdit(), 'SUPERMARKET')
    app.processEvents()
    assert combo.completer().completionCount() == 1
    assert 'supermarket' in combo.completer().currentCompletion().lower()
    assert combo.currentData() is None
    popup = combo.completer().popup()
    popup.setCurrentIndex(combo.completer().completionModel().index(0, 0))
    QTest.keyClick(popup, Qt.Key.Key_Return)
    assert combo.currentData() == 'groceries'
    assert combo.currentText() == 'Groceries (US supermarkets)'
    wheel(combo)
    assert combo.currentData() == 'groceries'
    combo.close()


def test_unrecognized_search_cannot_save_previous_category(app):
    combo = category_picker(['groceries', 'dining'], 'dining')
    count = combo.count()
    combo.lineEdit().selectAll()
    QTest.keyClicks(combo.lineEdit(), 'made-up category')
    QTest.keyClick(combo.lineEdit(), Qt.Key.Key_Return)
    assert combo.currentData() is None
    assert combo.count() == count
    combo.close()


def test_reselecting_same_category_updates_save_validity(app):
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
    container = QWidget()
    layout = QVBoxLayout(container)
    combo = category_picker(['groceries', 'dining'], 'dining')
    save = QPushButton('Save')
    layout.addWidget(combo)
    layout.addWidget(save)
    combo.selectionChanged.connect(lambda: save.setEnabled(bool(combo.currentData())))
    container.show()
    combo.lineEdit().selectAll()
    QTest.keyClicks(combo.lineEdit(), 'unfinished')
    assert not save.isEnabled()
    combo.completer().popup().hide()
    combo.showPopup()
    app.processEvents()
    index = combo.model().index(combo.findData('dining'), 0)
    QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton,
                     pos=combo.view().visualRect(index).center())
    assert combo.currentData() == 'dining'
    assert save.isEnabled()
    container.close()
