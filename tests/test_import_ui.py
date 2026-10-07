import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import QDate

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
