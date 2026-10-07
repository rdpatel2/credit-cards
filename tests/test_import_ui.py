import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import QDate, QEventLoop, QTimer

from card_compass.app import MainWindow
from card_compass.import_ui import ImportDialog
from tests.statement_examples import BOFA, AMEX


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def owner(app, tmp_path):
    window = MainWindow(tmp_path, auto_refresh=False)
    window.profile['wallet'] = ['bank-of-america-customized-cash-rewards', 'american-express-blue-cash-everyday']
    window.reload_cards()
    yield window
    window.close()


def dialog(owner, text=BOFA):
    cards = [owner.by_id[c] for c in owner.profile['wallet']]
    return ImportDialog('/nonexistent/synthetic.pdf', owner.imports_page.ledger, cards,
                        owner.imports_page.categories(), owner, extracted_text=text)


def test_grouped_first_import_and_second_import_memory(owner):
    first = dialog(owner)
    first.card.setCurrentIndex(1)
    assert len(first.merchant_widgets) == 9
    for name, category, scope in first.merchant_widgets.values():
        category.setCurrentIndex(category.findData('dining'))
    assert not first.save.isEnabled()
    first.confirm.setChecked(True)
    assert first.save.isEnabled()
    first.commit()
    assert first.result() == QDialog.DialogCode.Accepted
    assert first.imported_count == 13
    second = dialog(owner)
    # Account is remembered; no card selection or repeated category prompts.
    assert second.card.currentData() == 'bank-of-america-customized-cash-rewards'
    assert second.merchant_widgets == {}
    assert all(row['duplicate'] for row in second.rows)
    assert not second.save.isEnabled()
    second.reject()


def test_cancel_import_does_not_save_merchant_choices(owner):
    review = dialog(owner)
    review.card.setCurrentIndex(1)
    for _, category, _ in review.merchant_widgets.values():
        category.setCurrentIndex(category.findData('dining'))
    review.reject()
    assert owner.imports_page.ledger.transactions() == []
    assert owner.imports_page.ledger.rules() == []


def test_incompatible_card_never_enables_save(owner):
    review = dialog(owner)
    review.card.setCurrentIndex(1)
    for _, category, _ in review.merchant_widgets.values():
        category.setCurrentIndex(category.findData('dining'))
    review.confirm.setChecked(True)
    assert review.save.isEnabled()
    review.card.setCurrentIndex(2)
    review.confirm.setChecked(True)
    assert not review.save.isEnabled()
    review.reject()


def test_one_time_override_does_not_change_merchant_default(owner):
    review = dialog(owner)
    review.card.setCurrentIndex(1)
    for _, category, _ in review.merchant_widgets.values():
        category.setCurrentIndex(category.findData('dining'))
    chip_indexes = [i for i, row in enumerate(review.rows) if row['merchant_key'] == 'CHIPOTLE']
    combo = review.row_categories[chip_indexes[0]]
    combo.setCurrentIndex(combo.findData('everything_else'))
    review.confirm.setChecked(True)
    review.commit()
    rows = [r for r in owner.imports_page.ledger.transactions() if r['merchant_key'] == 'CHIPOTLE']
    assert {r['category'] for r in rows} == {'dining', 'everything_else'}
    assert next(r for r in owner.imports_page.ledger.rules() if r['merchant_key'] == 'CHIPOTLE')['category'] == 'dining'


def test_date_change_requires_reparse_and_review(owner):
    review = dialog(owner)
    review.card.setCurrentIndex(1)
    for _, category, _ in review.merchant_widgets.values():
        category.setCurrentIndex(category.findData('dining'))
    review.confirm.setChecked(True)
    review.closing.setDate(QDate(2027, 9, 17))
    assert not review.save.isEnabled()
    review.confirm.setChecked(True)
    assert not review.save.isEnabled()
    review.parse(use_selected_date=True)
    assert review.statement.transactions[1].date.startswith('2027')
    assert not review.confirm.isChecked()
    review.reject()


def test_spending_application_updates_recommendations_and_profile(owner):
    owner.imports_page.set_spending({'groceries': 111.14, 'medical': 10.04, 'auto_services': 4})
    assert owner.active_page == 'spending'
    assert owner.spend_widgets['medical'].value() == 10.04
    assert owner.profile['spend']['groceries'] == 111.14
    assert owner.profile['spend']['dining'] == 0
    assert owner.matches


def test_one_off_import_and_toggle_update_imported_spending_only(owner):
    from PySide6.QtCore import Qt
    review = dialog(owner, AMEX)
    review.card.setCurrentIndex(review.card.findData('american-express-blue-cash-everyday'))
    for _, category, _ in review.merchant_widgets.values():
        category.setCurrentIndex(category.findData('groceries'))
    flight_index = next(i for i, row in enumerate(review.rows) if 'DELTA' in row['description'])
    flight_picker = review.transactions.cellWidget(flight_index, 6)
    flight_picker.setChecked(True)
    assert review.rows[flight_index]['one_off']
    review.confirm.setChecked(True)
    review.commit()
    page = owner.imports_page
    page.reload()
    page.set_spending(page.ledger.monthly_spending()[0])
    assert owner.profile['spending_source'] == 'imports'
    assert owner.profile['spend']['groceries'] == 167.33
    index = next(i for i, row in enumerate(page.history) if 'DELTA' in row['description'])
    page.transactions.item(index, 5).setCheckState(Qt.CheckState.Unchecked)
    assert owner.profile['spend']['groceries'] == 754.13
    assert page.ledger.transactions()[0]['one_off'] == 0
    page.transactions.item(index, 5).setCheckState(Qt.CheckState.Checked)
    assert owner.profile['spend']['groceries'] == 167.33
    # A manual edit opts out of automatic replacement of the spending inputs.
    owner.spend_widgets['groceries'].setValue(250)
    owner.recalculate()
    assert owner.profile['spending_source'] == 'manual'
    page.transactions.item(index, 5).setCheckState(Qt.CheckState.Unchecked)
    assert owner.spend_widgets['groceries'].value() == 250
    assert 'manual inputs are unchanged' in page.status.text()
    page.transactions.item(index, 5).setCheckState(Qt.CheckState.Checked)
    reopened = MainWindow(owner.directory, auto_refresh=False)
    assert any(row['one_off'] for row in reopened.imports_page.history)
    assert reopened.spend_widgets['groceries'].value() == 250
    reopened.close()


def test_actual_pdf_is_extracted_in_background(owner, tmp_path, app):
    import pymupdf
    path = tmp_path / 'statement.pdf'
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((30, 40), 'American Express Closing Date 09/21/26', fontsize=10)
        page.insert_text((30, 60), 'Account Ending 1-00002', fontsize=10)
        page.insert_text((30, 90), '09/06/26 FOOD LION #1573 RALEIGH NC $34.39', fontsize=10)
        document.save(path)
    cards = [owner.by_id[c] for c in owner.profile['wallet']]
    review = ImportDialog(path, owner.imports_page.ledger, cards, owner.imports_page.categories(), owner)
    review.extract()
    loop = QEventLoop()
    review.worker.finished.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()
    assert review.statement.purchases_cents == 3439
    review.card.setCurrentIndex(2)
    assert set(review.merchant_widgets) == {'FOOD LION'}
    review.reject()


def test_cancellation_waits_for_extraction_without_saving(owner, monkeypatch, app):
    import threading
    started, release = threading.Event(), threading.Event()
    def delayed(path):
        started.set()
        release.wait(2)
        return AMEX
    monkeypatch.setattr('card_compass.import_ui.extract_pdf_text', delayed)
    cards = [owner.by_id[c] for c in owner.profile['wallet']]
    review = ImportDialog('/unused.pdf', owner.imports_page.ledger, cards, owner.imports_page.categories(), owner)
    review.extract()
    assert started.wait(1)
    review.reject()
    release.set()
    loop = QEventLoop()
    review.worker.finished.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()
    assert review.cancelled
    assert owner.imports_page.ledger.transactions() == []


def test_unfinished_purchase_category_search_prevents_save(owner):
    from PySide6.QtTest import QTest
    review = dialog(owner)
    review.card.setCurrentIndex(1)
    for _, category, _ in review.merchant_widgets.values():
        category.setCurrentIndex(category.findData('dining'))
    review.confirm.setChecked(True)
    assert review.save.isEnabled()
    category = next(combo for combo in review.row_categories if combo)
    category.lineEdit().selectAll()
    QTest.keyClicks(category.lineEdit(), 'unfinished search')
    assert not review.save.isEnabled()
    category.setCurrentIndex(category.findData('groceries'))
    assert review.save.isEnabled()
    review.reject()


def test_csv_mapping_requires_amount_convention(owner):
    from card_compass.import_ui import CsvMappingDialog
    from tests.test_csv_statements import BOFA
    mapping = CsvMappingDialog(BOFA, owner)
    mapping.validate()
    assert mapping.result() != QDialog.DialogCode.Accepted
    assert mapping.error.text()
    mapping.convention.setCurrentIndex(mapping.convention.findData('positive'))
    mapping.validate()
    assert mapping.result() == QDialog.DialogCode.Accepted
    assert mapping.config()['mapping']['description'] == 'Payee'


def test_csv_review_saves_and_reimports_with_merchant_memory(owner, tmp_path):
    from card_compass.csv_statements import suggested_columns, csv_table
    from tests.test_csv_statements import BOFA
    path = tmp_path / 'statement.csv'
    path.write_text(BOFA)
    config = {'mapping': suggested_columns(csv_table(BOFA)[0]), 'convention': 'positive', 'account_ending': '0001'}
    cards = [owner.by_id[c] for c in owner.profile['wallet']]
    first = ImportDialog(path, owner.imports_page.ledger, cards, owner.imports_page.categories(), owner,
                         extracted_text=BOFA, csv_config=config)
    first.card.setCurrentIndex(1)
    first.closing.setDate(QDate(2026, 9, 17))
    first.parse(use_selected_date=True)
    assert first.statement.issuer == 'bofa'
    for _, category, _ in first.merchant_widgets.values():
        category.setCurrentIndex(category.findData('groceries'))
    first.confirm.setChecked(True)
    assert first.save.isEnabled()
    first.commit()
    assert first.imported_count == 3
    second = ImportDialog(path, owner.imports_page.ledger, cards, owner.imports_page.categories(), owner,
                          extracted_text=BOFA, csv_config=config)
    second.card.setCurrentIndex(1)
    assert second.merchant_widgets == {}
    assert all(row['duplicate'] for row in second.rows)
    assert not second.save.isEnabled()
    second.reject()


def test_csv_accepts_wallet_cards_beyond_pdf_issuers(owner):
    from card_compass.csv_statements import suggested_columns, csv_table
    from tests.test_csv_statements import BOFA
    card = owner.by_id['chase-freedom-unlimited']
    config = {'mapping': suggested_columns(csv_table(BOFA)[0]), 'convention': 'positive'}
    review = ImportDialog('/unused.csv', owner.imports_page.ledger, [card], owner.imports_page.categories(), owner,
                          extracted_text=BOFA, csv_config=config)
    review.card.setCurrentIndex(1)
    assert review.preview_valid
    assert review.statement.issuer == 'chase'
    review.reject()


def test_statement_chooser_offers_csv_and_pdf(owner, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QPushButton
    filters = []
    def choose(*args):
        filters.append(args[-1])
        return '', ''
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', choose)
    owner.imports_page.import_statement()
    assert '*.csv' in filters[0] and '*.pdf' in filters[0]
    assert any(button.text() == 'Import CSV or PDF' for button in owner.imports_page.findChildren(QPushButton))
