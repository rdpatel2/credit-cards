import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import QApplication
from card_compass.app import MainWindow
from card_compass.finance_ui import EntryDialog, INCOME_CATEGORIES


def test_budget_overview_navigation_and_saved_month(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path, auto_refresh=False)
    assert window.active_page == 'overview'
    assert window.navigation['explore'].isHidden()
    income = window.finance_store.save_entry('Salary', 'income', 'salary', 4500, '2026-10', True)
    window.finance_store.save_entry('Rent', 'expense', 'rent', 1500, '2026-10', True)
    window.finance_store.post(income, '2026-10', True)
    window.navigate('budget')
    page = window.budget_page
    page.month.setDate(QDate(2026, 10, 1))
    page.category.setCurrentIndex(page.category.findData('groceries'))
    page.target.setValue(400)
    page.set_target()
    assert page.metrics[2].value.text() == '$2,600.00'
    assert page.entries_table.rowCount() == 2
    window.navigate('explore')
    assert not window.navigation['wallet'].isHidden()
    window.navigate('stocks')
    assert window.stocks_page.tabs.count() == 4
    assert window.stocks_page.data_layer.worker is None
    window.close()
    reopened = MainWindow(tmp_path, auto_refresh=False)
    assert reopened.finance_store.summary('2026-10')['planned'] == 190000
    reopened.close()


def test_income_categories_follow_type_and_preserve_custom_edit(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path, auto_refresh=False)
    dialog = EntryDialog(window, '2026-10')
    assert dialog.category.currentData() == 'rent'
    dialog.kind.setCurrentIndex(0)
    assert {dialog.category.itemData(i) for i in range(dialog.category.count())} == INCOME_CATEGORIES
    assert dialog.values()['category'] == 'salary'
    dialog.category.setEditText('Consulting')
    dialog.kind.setCurrentIndex(1)
    assert not any(dialog.category.itemData(i) in INCOME_CATEGORIES for i in range(dialog.category.count()))
    dialog.kind.setCurrentIndex(0)
    assert dialog.values()['category'] == 'Consulting'
    ident = window.finance_store.save_entry('Client work', 'income', 'Consulting', 700, '2026-10', True)
    row = next(row for row in window.finance_store.entries('2026-10') if row['id'] == ident)
    edit = EntryDialog(window, '2026-10', row)
    assert edit.values()['category'] == 'Consulting'
    edit.deleteLater()
    dialog.deleteLater()
    window.close()


def test_monthly_income_button_saves_recurring_income_and_pie_tracks_expenses(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path, auto_refresh=False)
    page = window.budget_page
    page.month.setDate(QDate(2026, 10, 1))
    def enter_income():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, EntryDialog)
        assert dialog.kind.currentData() == 'income' and dialog.recurring.isChecked()
        assert dialog.category.currentData() == 'salary'
        dialog.value.setValue(4500)
        dialog.accept()
    QTimer.singleShot(0, enter_income)
    page.add_income.click()
    assert window.finance_store.summary('2026-10')['income'] == 450000
    assert window.finance_store.summary('2026-11')['income'] == 450000
    rent = window.finance_store.save_entry('Rent', 'expense', 'rent', 1500, '2026-10', True)
    window.finance_store.save_entry('Electricity', 'expense', 'utilities', 150, '2026-10', True)
    window.finance_store.post(rent, '2026-10', True)
    page.refresh_all()
    assert page.pie.items == [('Rent / mortgage', 150000)]
    page.pie_mode.setCurrentIndex(1)
    assert ('Utilities', 15000) in page.pie.items
    assert not any('Salary' == name for name, value in page.pie.items)
    window.navigate('budget')
    window.show()
    app.processEvents()
    assert not page.pie.grab().isNull()
    page.month.setDate(QDate(2026, 9, 1))
    # Default card category targets can remain, but scheduled rent is absent before its start.
    assert ('Rent / mortgage', 150000) not in page.pie.items
    window.close()
