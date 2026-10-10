"""User flows covering first use, history preservation, recovery and navigation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from pathlib import Path
import json
from zipfile import ZipFile
import pytest
from PySide6.QtCore import QDate, Qt, QTimer, QPoint
from PySide6.QtWidgets import QApplication, QPushButton, QDialog, QDialogButtonBox, QFileDialog, QMessageBox
from PySide6.QtTest import QTest
from card_compass.app import MainWindow
from card_compass.finance import FinanceStore
from card_compass.finance_ui import QuickStartDialog, EntryDialog
from card_compass.import_ui import CsvMappingDialog
from card_compass.stocks_ui import TradeDialog
from card_compass.backup import create_backup, restore_backup, read_backup
from card_compass.statements import Statement, Transaction
from card_compass.investments import widget, InvestmentStore


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    owner = MainWindow(tmp_path, auto_refresh=False)
    owner.resize(1040, 720)
    owner.show()
    app.processEvents()
    yield owner
    owner.close()
    owner.deleteLater()
    app.processEvents()


def add_statement(window):
    ledger = window.imports_page.ledger
    card = 'chase-freedom-unlimited'
    statement = Statement('csv', '1234', '2026-10-31', [
        Transaction('2026-10-01', '2026-10-01', 'Coffee Shop', 1200),
        Transaction('2026-10-02', '2026-10-02', 'Grocery Store', 4000),
        Transaction('2026-10-03', '2026-10-03', 'Grocery Store', -500, 'refund'),
    ])
    ledger.commit(statement, card, ledger.preview(statement, card), {
        'COFFEE SHOP': {'name': 'Coffee Shop', 'category': 'dining'},
        'GROCERY STORE': {'name': 'Grocery Store', 'category': 'groceries'},
    })
    window.imports_page.reload()
    return ledger


def test_spending_defaults_to_numeric_descending_and_header_can_reverse(window):
    window.spend_widgets['dining'].setValue(20)
    window.spend_widgets['groceries'].setValue(1000)
    window.spend_widgets['gas_stations'].setValue(200)
    window.recalculate()
    table=window.spending
    values=[table.item(row,1).data(Qt.ItemDataRole.DisplayRole) for row in range(table.rowCount())]
    assert window.spending_sort_order==Qt.SortOrder.DescendingOrder
    assert values==sorted(values,reverse=True)
    assert table.horizontalHeader().sortIndicatorOrder()==Qt.SortOrder.DescendingOrder
    for row in range(table.rowCount()):
        category=table.item(row,0).data(Qt.ItemDataRole.UserRole)
        assert table.cellWidget(row,1) is window.spend_widgets[category]
    window.sort_spending(1)
    values=[table.item(row,1).data(Qt.ItemDataRole.DisplayRole) for row in range(table.rowCount())]
    assert values==sorted(values) and window.spending_sort_order==Qt.SortOrder.AscendingOrder


def test_first_plan_income_edit_and_shared_month(window):
    page = window.overview_page
    page.month.setDate(QDate(2026, 10, 1))
    assert page.welcome.isVisible()
    assert page.entries_table.isHidden()
    QTest.qWait(20)
    assert page.scroll.verticalScrollBar().maximum() == 0
    def setup():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QuickStartDialog)
        for key, value in [('income', 4500), ('rent', 1500), ('groceries', 400)]:
            dialog.fields[key].setValue(value)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
    QTimer.singleShot(0, setup)
    page.quick_start.click()
    assert not page.welcome.isVisible()
    assert page.tabs.isVisible()
    assert page.metrics[2].value.text() == '$2,600.00'
    salary = next(r for r in page.rows if r['kind'] == 'income')
    page.post_row(salary)
    assert page.summary['received'] == 450000
    page.month.next.click()
    assert window.budget_page.period() == page.period() == '2026-11'
    assert window.finance_store.summary('2026-11')['planned'] == 190000
    def edit():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, EntryDialog)
        assert dialog.scope.currentData() == 'future'
        dialog.value.setValue(5000)
        dialog.accept()
    QTimer.singleShot(0, edit)
    page.edit_entry(next(r for r in page.rows if r['kind'] == 'income'))
    assert window.finance_store.summary('2026-10')['received'] == 450000
    assert window.finance_store.summary('2026-11')['income'] == 500000
    assert window.finance_store.summary('2026-12')['income'] == 500000
    window.budget_page.month.months.setCurrentIndex(0)
    window.budget_page.month.year.setValue(2027)
    assert page.period() == '2027-01'
    reopened = MainWindow(window.directory, auto_refresh=False)
    assert reopened.overview_page.period() == reopened.budget_page.period() == '2027-01'
    reopened.close()


@pytest.mark.parametrize('month', ['2026-09', '2026-10', '2026-11'])
def test_single_occurrence_edit_preserves_surrounding_posted_months(tmp_path, month):
    store = FinanceStore(tmp_path)
    ident = store.save_entry('Salary', 'income', 'salary', 4500, '2026-09', True, end_month='2026-11')
    for m in ['2026-09', '2026-10', '2026-11']:
        store.post(ident, m, True)
    store.edit_occurrences(ident, month, 'month', name='Salary', kind='income', category='salary', amount=4800)
    for m in ['2026-09', '2026-10', '2026-11']:
        assert store.summary(m)['received'] == (480000 if month == m else 450000)
        assert len(store.entries(m)) == 1
    assert not store.entries('2026-12')


def test_invalid_scoped_edit_is_atomic(tmp_path):
    store = FinanceStore(tmp_path)
    ident = store.save_entry('Salary', 'income', 'salary', 4500, '2026-10', True)
    with pytest.raises(ValueError):
        store.edit_occurrences(ident, '2026-09', 'future', name='Salary', kind='income', category='salary', amount=5000)
    assert store.summary('2026-10')['income'] == 450000


def test_repeating_targets_and_one_month_override_are_independent(window):
    store = window.finance_store
    store.set_recurring_target('2026-10', 'groceries', 400)
    store.set_target('2026-11', 'groceries', 0)
    window.spend_widgets['groceries'].setValue(900)
    window.overview_page.month.setDate(QDate(2026, 10, 1))
    window.overview_page.reload()
    assert window.overview_page.summary['planned'] == 40000
    assert store.summary('2026-11')['planned'] == 0
    assert store.summary('2026-12')['planned'] == 40000


def test_copy_budgets_requires_preview_and_cancel_does_not_write(window):
    page = window.budget_page
    page.month.setDate(QDate(2026, 11, 1))
    page.store.set_target('2026-10', 'groceries', 400)
    page.store.set_target('2026-11', 'groceries', 500)
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
    page.copy_targets()
    assert page.store.summary('2026-11')['planned'] == 50000
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().accept())
    page.copy_targets()
    assert page.store.summary('2026-11')['planned'] == 40000


def test_breakdown_drills_into_filtered_search_and_stale_inputs(window):
    ledger = add_statement(window)
    page = window.imports_page
    page.set_spending(ledger.monthly_spending()[0], navigate=False)
    assert not window.profile['spending_stale']
    breakdown = window.breakdown_page
    breakdown.period.setCurrentIndex(breakdown.period.findData('2026-10'))
    breakdown.open_purchases('categories', 0)
    assert window.active_page == 'imports'
    assert len(page.history) == 2
    assert page.period.currentData() == '2026-10'
    page.search.setText('does not exist')
    assert not page.history
    assert not page.edit_button.isEnabled()
    page.clear_filters()
    assert len(page.history) == 3
    row = next(r for r in ledger.transactions() if r['kind'] == 'purchase')
    ledger.override_transaction(row['id'], 'gas')
    page.reload()
    assert window.profile['spending_stale']
    assert not page.stale_button.isHidden()
    page.set_spending(ledger.monthly_spending()[0], navigate=False)
    assert not window.profile['spending_stale']
    window.navigate('imports')
    QApplication.instance().processEvents()
    assert page.transactions.columnWidth(0) >= 240
    assert page.transactions.isColumnHidden(4)


def test_card_selection_does_not_open_details_and_auto_is_single(window):
    window.navigate('explore')
    window.search.setText('freedom')
    window.catalog_table.selectRow(0)
    assert not window.details_dialog.isVisible()
    window.open_catalog.click()
    assert window.details_dialog.isVisible()
    window.details_dialog.close()
    window.profile['wallet'] = ['chase-freedom-unlimited']
    window.reload_cards()
    window.spend_widgets['dining'].setValue(100)
    window.recalculate()
    assert window.coverage_modes['dining'].currentText().startswith('Auto')
    assert window.coverage_widgets['dining'].isHidden()
    assert window.coverage_modes['dining'].property('coverageBand') == 'high'
    assert 'background:' in window.coverage_modes['dining'].styleSheet()
    assert 'Auto' not in window.coverage_widgets['dining'].text()
    assert '%' in window.coverage_widgets['dining'].text()
    window.coverage_modes['dining'].setCurrentIndex(1)
    assert window.coverage_widgets['dining'].isEnabled()
    window.coverage_widgets['dining'].setValue(2)
    window.target.setValue(2)
    window.recalculate()
    assert window.coverage_widgets['dining'].property('coverageBand') == 'high'
    window.coverage_modes['dining'].setCurrentIndex(0)
    assert window.coverage_widgets['dining'].value() == -1


def test_csv_required_fields_and_interpreted_preview(window):
    dialog = CsvMappingDialog('Date,Description,Amount\n2026-10-01,Store,100.00\n2026-10-02,Return,-20.00\n', window)
    assert dialog.optional_box.isHidden()
    dialog.convention.setCurrentIndex(dialog.convention.findData('positive'))
    assert 'Purchase · $100.00' in dialog.interpretation.text()
    assert 'Refund · −$20.00' in dialog.interpretation.text()
    assert dialog.columns['debit'].isHidden()
    dialog.convention.setCurrentIndex(dialog.convention.findData('debit_credit'))
    assert dialog.columns['amount'].isHidden()
    assert not dialog.columns['debit'].isHidden()
    dialog.close()


def test_widget_controls_scope_undo_notes_and_news_resize(window):
    from card_compass.dashboard.widget_registry import REGISTRY
    p=window.stocks_page;window.navigate('stocks');p.open_symbol('AAPL')
    assert 'shared stock layout' in p.research_tabs['AAPL'].scope_label.text()
    p.research_tabs['AAPL'].customize.click()
    assert 'only AAPL' in p.research_tabs['AAPL'].scope_label.text()
    chart=next(w for w in p.current_page()['widgets'] if w['type']=='chart')
    definition=REGISTRY.get('chart');dialog=definition.ConfigPanel(definition,chart['config'],p.data_layer,p)
    assert set(dialog.fields)=={'symbol','followSymbol','range'}
    dialog.close()
    note=next(w for w in p.current_page()['widgets'] if w['type']=='notes');key='stock-AAPL:'+note['id']
    p.note_editors[key].setPlainText('Important research notes');p.remove_widget(note['id']);p.undo_button.click()
    assert p.note_editors[key].toPlainText()=='Important research notes'
    p.tabs.setCurrentWidget(p.news_page);p.edit_layout.setChecked(True)
    canvas=p.active_canvas();block=p.current_page()['widgets'][0];header=canvas.frames[block['id']].header
    before=next(g['h'] for g in canvas.current_layout if g['i']==block['id'])
    QTest.keyClick(header,Qt.Key.Key_Space);QTest.keyClick(header,Qt.Key.Key_Down,Qt.KeyboardModifier.ShiftModifier);QTest.keyClick(header,Qt.Key.Key_Return)
    p.layout_store.flush()
    assert next(g['h'] for g in canvas.current_layout if g['i']==block['id'])==before+1
    assert InvestmentStore(window.directory).state['newsPages']==p.store.state['newsPages']


def test_drag_preview_is_bounded_and_escape_restores_layout(window):
    p=window.stocks_page;window.navigate('stocks');p.edit_layout.setChecked(True)
    canvas=p.active_canvas();before=deepcopy(canvas.current_layout);extent=canvas.sceneRect()
    header=canvas.frames[before[0]['i']].header
    QTest.keyClick(header,Qt.Key.Key_Space);QTest.keyClick(header,Qt.Key.Key_Right)
    assert canvas.placeholder.isVisible() and canvas.sceneRect()==extent
    QTest.keyClick(header,Qt.Key.Key_Escape)
    assert canvas.current_layout==before


def test_partial_market_value_is_visible(window):
    from tests.test_investments import trade
    p = window.stocks_page
    p.store.trade('AAPL', trade('buy', 1, 100))
    p.store.trade('MSFT', trade('buy', 1, 200))
    p.cache['quote:AAPL|1d'] = dict(price=110, currency='USD', stale=False, demo=False)
    p.render_metrics()
    assert p.metrics[2].value.text() == '$110.00'
    assert 'PARTIAL' in p.metrics[2].caption.text()
    assert '1 of 2' in p.metrics[2].description.text()
    assert 'MSFT' in p.metrics[2].description.text()
    assert not p.valuation_details.isHidden()


def test_backup_restores_all_data_and_keeps_recovery(window, tmp_path):
    add_statement(window)
    window.finance_store.save_entry('Salary', 'income', 'salary', 4500, '2026-10', True)
    window.investment_store.save(window.investment_store.state)
    window.save_profile()
    archive = tmp_path / 'backup.zip'
    create_backup(window.directory, archive)
    window.finance_store.save_entry('New bill', 'expense', 'rent', 800, '2026-10')
    recovery = restore_backup(window.directory, archive)
    assert window.finance_store.summary('2026-10')['income'] == 450000
    assert window.finance_store.summary('2026-10')['planned'] == 0
    assert len(window.imports_page.ledger.transactions()) == 3
    assert recovery.exists()
    restore_backup(window.directory, recovery)
    assert window.finance_store.summary('2026-10')['planned'] == 80000
    bad = tmp_path / 'bad.zip'
    with ZipFile(bad, 'w') as output:
        output.writestr('../profile.json', '{}')
    with pytest.raises(ValueError):
        restore_backup(window.directory, bad)
    assert window.finance_store.summary('2026-10')['planned'] == 80000


def test_compact_layout_month_controls_and_investment_padding(window):
    window.budget_page.month.setDate(QDate(2026,10,1))
    for key,current in [('overview',window.overview_page),('budget',window.budget_page)]:
        window.navigate(key);window.finance_store.save_entry('Salary','income','salary',4500,current.period(),True);current.reload()
        for index in range(current.tabs.count()):
            current.tabs.setCurrentIndex(index);QTest.qWait(20)
            assert current.scroll.verticalScrollBar().maximum()==0,(key,index)
    p=window.stocks_page;window.navigate('stocks')
    for tab in (p.dashboard_page,p.watchlist_page,p.history_page,p.news_page):
        margins=tab.layout().contentsMargins()
        assert min(margins.left(),margins.right(),margins.top(),margins.bottom())>=12
        assert tab.canvas.horizontalScrollBarPolicy()==Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_real_header_drag_previews_then_saves_block_position(window):
    from PySide6.QtCore import QPointF
    p=window.stocks_page;window.navigate('stocks');p.edit_layout.setChecked(True)
    p.set_dashboard_settings({'compact':False});canvas=p.active_canvas()
    before=deepcopy(canvas.current_layout);first=before[0];header=canvas.frames[first['i']].header
    start=canvas.mapFromScene(canvas.items[first['i']].pos()+QPointF(70,22))
    end=QPoint(round(8+canvas.column_width*6+70),canvas.viewport().height()-65)
    QTest.mousePress(canvas.viewport(),Qt.MouseButton.LeftButton,pos=start);QTest.mouseMove(canvas.viewport(),end)
    assert canvas.interaction['active'] and canvas.placeholder.isVisible()
    assert p.current_page()['layouts'][canvas.size_key]==before
    QTest.mouseRelease(canvas.viewport(),Qt.MouseButton.LeftButton,pos=end);p.layout_store.flush()
    after=p.current_page()['layouts'][canvas.size_key]
    assert after!=before
    assert InvestmentStore(window.directory).state['pages'][0]['layouts'][canvas.size_key]==after


def test_sale_setup_keeps_entered_scenario(window, monkeypatch):
    p = window.stocks_page
    dialog = TradeDialog(p, 'AAPL', scenario=True)
    dialog.quantity.setValue(3.5)
    dialog.price.setValue(123)
    called = []
    monkeypatch.setattr(p, 'tax_settings', lambda: called.append(True))
    next(b for b in dialog.findChildren(QPushButton) if b.text().startswith('Set up / edit')).click()
    assert called and dialog.quantity.value() == 3.5 and dialog.price.value() == 123
    dialog.close()


def test_restore_ui_reopens_without_overwriting_restored_profile(window, tmp_path, monkeypatch):
    window.spend_widgets['dining'].setValue(100)
    window.save_profile()
    archive = tmp_path / 'ui-backup.zip'
    create_backup(window.directory, archive)
    window.spend_widgets['dining'].setValue(900)
    window.save_profile()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(archive), ''))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **kw: None)
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: errors.append(a[2]))
    window.restore_data()
    assert not errors
    replacement = window.replacement_window
    assert replacement.spend_widgets['dining'].value() == 100
    assert json.loads((window.directory / 'profile.json').read_text())['spend']['dining'] == 100
    replacement.close()
