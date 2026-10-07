import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication

from card_compass.app import MainWindow
from card_compass.statements import Statement, Transaction


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    owner = MainWindow(tmp_path, auto_refresh=False)
    ledger = owner.imports_page.ledger
    first = Statement('bofa', '0001', '2026-09-30', [
        Transaction('2026-09-01', '2026-09-02', 'CHIPOTLE EXAMPLE', 10000),
        Transaction('2026-09-03', '2026-09-04', 'FOOD LION #0001', 5000),
        Transaction('2026-09-05', '2026-09-06', 'FOOD LION #0001', -2000, 'refund'),
        Transaction('2026-09-07', '2026-09-08', 'PAYMENT THANK YOU', -50000, 'payment')])
    rows = ledger.preview(first, 'bank-of-america-customized-cash-rewards')
    ledger.commit(first, 'bank-of-america-customized-cash-rewards', rows, {
        'CHIPOTLE': {'name': 'Chipotle', 'category': 'dining'},
        'FOOD LION': {'name': 'Food Lion', 'category': 'groceries'}})
    second = Statement('amex', '00002', '2026-10-21', [
        Transaction('2026-10-01', '2026-10-02', 'FOOD LION #0002', 15000)])
    ledger.commit(second, 'american-express-blue-cash-everyday',
                  ledger.preview(second, 'american-express-blue-cash-everyday'), {})
    owner.imports_page.reload()
    yield owner
    owner.close()


def test_breakdown_navigation_totals_and_largest_categories(window):
    window.navigation['breakdown'].click()
    page = window.breakdown_page
    assert window.active_page == 'breakdown'
    assert page.net.value.text() == '$280.00'
    assert page.purchases.value.text() == '$300.00'
    assert page.refunds.value.text() == '$20.00'
    assert page.tables['categories'].item(0, 0).text() == 'Groceries (US supermarkets)'
    assert page.tables['merchants'].item(0, 0).text() == 'Food Lion'
    assert len(page.category_bars.items) == 2
    assert page.tables['months'].item(0, 0).text() == '2026-10'


def test_month_and_card_selection_updates_charts_and_totals(window):
    page = window.breakdown_page
    page.period.setCurrentIndex(page.period.findData('2026-09'))
    assert page.net.value.text() == '$130.00'
    page.card.setCurrentIndex(page.card.findData('american-express-blue-cash-everyday'))
    assert page.net.value.text() == '$0.00'
    assert not page.category_bars.items
    assert 'No purchases or refunds' in page.summary.text()
    page.period.setCurrentIndex(0)
    assert page.net.value.text() == '$150.00'


def test_ledger_edits_and_removal_refresh_breakdown_and_preserve_filters(window):
    page = window.breakdown_page
    page.period.setCurrentIndex(page.period.findData('2026-09'))
    ledger = window.imports_page.ledger
    purchase = next(row for row in ledger.transactions() if row['amount_cents'] == 5000)
    ledger.override_transaction(purchase['id'], 'recreation')
    window.imports_page.reload()
    assert page.period.currentData() == '2026-09'
    assert page.net.value.text() == '$130.00'
    assert any(page.tables['categories'].item(i, 0).text() == 'Recreation / golf' for i in range(page.tables['categories'].rowCount()))
    assert any(page.tables['categories'].item(i, 1).text() == '−$20.00' for i in range(page.tables['categories'].rowCount()))
    first = next(s for s in ledger.imports() if s['closing_date'] == '2026-09-30')
    ledger.remove_import(first['id'])
    window.imports_page.reload()
    assert page.period.currentData() is None
    assert page.net.value.text() == '$150.00'


def test_breakdown_fits_minimum_window_and_has_empty_state(app, tmp_path):
    owner = MainWindow(tmp_path, auto_refresh=False)
    owner.navigate('breakdown')
    owner.resize(1040, 720)
    owner.show()
    app.processEvents()
    assert owner.width() == 1040 and owner.height() == 720
    assert owner.breakdown_page.report['count'] == 0
    assert owner.breakdown_page.tables['categories'].rowCount() == 0
    assert 'Import a statement' in owner.breakdown_page.summary.text()
    owner.close()
