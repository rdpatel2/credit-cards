import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from card_compass.app import MainWindow
from card_compass.recurring_ui import RecurringDialog
from tests.test_recurring import seed


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    seed(window.recurring_store)
    window.imports_page.reload();window.show();app.processEvents()
    yield window
    window.close();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_explicit_review_persists_and_changed_history_needs_another_review(window):
    dialog=RecurringDialog(window)
    assert dialog.table.rowCount()==1 and 'transaction #' in dialog.details.toPlainText()
    before=window.finance_store.transactions()
    dialog.confirm_selected()
    assert dialog.table.item(0,5).text()=='Confirmed'
    assert window.finance_store.transactions()==before
    with window.recurring_store.connection() as db:db.execute('UPDATE transactions SET amount_cents=1060 WHERE id=3')
    dialog.confirm_selected()
    assert 'evidence changed' in dialog.error.text()
    dialog.reload()
    assert dialog.table.item(0,5).text()=='Evidence changed'
    dialog.dismiss_selected()
    assert dialog.table.item(0,5).text()=='Dismissed'
    assert RecurringDialog(window).table.item(0,5).text()=='Dismissed'
    dialog.close()


def test_remove_review_requires_user_confirmation_and_purchases_can_be_opened(window,monkeypatch):
    dialog=RecurringDialog(window);dialog.confirm_selected()
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.No)
    dialog.remove_selected();assert len(window.recurring_store.reviews())==1
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
    dialog.remove_selected();assert not window.recurring_store.reviews()
    dialog.open_purchases()
    assert window.pages.currentWidget() is window.imports_page
    assert window.imports_page.drill_filter[0]=='account_key'
    assert window.imports_page.transactions.rowCount()==3
    dialog.close()


def test_missing_history_and_invalid_saved_review_do_not_fabricate_active_bills(window):
    dialog=RecurringDialog(window);dialog.confirm_selected()
    with window.recurring_store.connection() as db:db.execute('DELETE FROM transactions')
    dialog.reload()
    assert dialog.table.rowCount()==0 and dialog.saved_table.item(0,4).text()=='No recent pattern'
    with window.recurring_store.connection() as db:db.execute("UPDATE recurring_reviews SET record='{}'")
    dialog.reload()
    assert 'Existing records are preserved' in dialog.error.text() and dialog.saved_table.rowCount()==0
    dialog.close()


def test_compact_screen_leaves_review_table_and_close_button_visible(window):
    window.is_demo=True
    dialog=RecurringDialog(window);dialog.resize(980,700);dialog.show()
    for _ in range(3):QApplication.instance().processEvents()
    assert dialog.table.viewport().height()>=140
    assert hasattr(dialog,'demo_banner')
    if hasattr(dialog,'text_scroll'):
        assert dialog.text_scroll.horizontalScrollBar().maximum()==0
    dialog.close()
