import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from datetime import date
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication,QMessageBox
from card_compass.app import MainWindow
from card_compass.dividends_ui import DividendEventDialog,DividendReviewDialog
from card_compass.investment_tax_ui import TaxProfileDialog
from tests.test_dividends import workspace


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    window.investment_store.save(workspace());window.stocks_page.reload()
    window.show();app.processEvents()
    yield window
    window.close();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_record_review_export_navigation_and_delete_preserve_trades(window,monkeypatch):
    owner=window.stocks_page;dialog=DividendReviewDialog(owner)
    before=deepcopy(owner.store.state['trades'])
    def accept(editor):
        editor.account.setCurrentIndex(editor.account.findData('taxable'))
        editor.ticker.setText('TEST');editor.gross.setValue(100)
        editor.classification.setCurrentIndex(editor.classification.findData('mixed'));editor.qualified.setValue(60)
        editor.source.setText('Reviewed statement')
        return editor.DialogCode.Accepted
    monkeypatch.setattr(DividendEventDialog,'exec',accept)
    dialog.edit_event(True)
    assert dialog.table.rowCount()==1 and 'USD 60.00' in dialog.summary.toPlainText()
    assert owner.store.state['trades']==before
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
    dialog.table.selectRow(0);dialog.delete_event()
    assert not owner.store.state['dividendEvents']
    dialog.open_exports();assert window.pages.currentWidget() is window.reports_page
    dialog.close()


def test_scope_is_explicit_defaults_are_deliberate_and_unknown_rates_remain_unavailable(window):
    dialog=TaxProfileDialog(window.stocks_page)
    assert dialog.state.currentData()=='FEDERAL'
    dialog.year.setValue(2025);dialog.status.setCurrentIndex(dialog.status.findData('joint'))
    dialog.use_defaults()
    assert dialog.fields['deduction'].value()==31500 and dialog.fields['stateDeduction'].value()==0
    dialog.state.setCurrentIndex(dialog.state.findData('NC'))
    assert 'full-year' in dialog.error.text()
    dialog.nc_resident.setChecked(True);dialog.use_defaults()
    assert dialog.fields['stateDeduction'].value()==25500
    dialog.year.setValue(2027)
    assert 'No reviewed' in dialog.error.text()
    dialog.state.setCurrentIndex(dialog.state.findData('OTHER'));dialog.other_state.setText('California')
    assert dialog.values()['state']=='California'
    dialog.close()


def test_failed_financial_save_does_not_claim_saved_state(window,monkeypatch):
    owner=window.stocks_page;dialog=DividendReviewDialog(owner)
    before=deepcopy(owner.store.state)
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr(owner.store,'save',fail)
    candidate=deepcopy(before);candidate['dividendEvents']=[]
    with pytest.raises(OSError,match='disk full'):dialog.persist(candidate)
    assert owner.store.state==before
    assert owner.commit(candidate) is False
    assert owner.layout_store.dirty and owner.layout_store.status=='Failed – retry'
    monkeypatch.undo();owner.layout_store.flush()
    dialog.close()


def test_tax_scope_saves_unsupported_year_without_fabricating_rates(window,monkeypatch):
    from card_compass.investment_tax import coverage
    def accept(editor):
        editor.year.setValue(2027)
        editor.state.setCurrentIndex(editor.state.findData('OTHER'))
        editor.other_state.setText('California')
        return editor.DialogCode.Accepted
    monkeypatch.setattr(TaxProfileDialog,'exec',accept)
    window.stocks_page.tax_settings()
    profile=window.investment_store.state['taxProfile']
    assert profile['year']==2027 and profile['state']=='California'
    assert 'No reviewed' in coverage(profile)


def test_small_screen_dividend_table_and_tax_footer_are_reachable(window):
    dialog=DividendReviewDialog(window.stocks_page);dialog.resize(980,700);dialog.show()
    for _ in range(3):QApplication.instance().processEvents()
    assert dialog.table.viewport().height()>=200
    tax=TaxProfileDialog(window.stocks_page);tax.resize(740,700);tax.show()
    for _ in range(3):QApplication.instance().processEvents()
    if hasattr(tax,'text_scroll'):
        assert tax.text_scroll.horizontalScrollBar().maximum()==0
    from PySide6.QtWidgets import QDialogButtonBox
    box=tax.findChild(QDialogButtonBox)
    assert box.mapTo(tax,box.rect().bottomRight()).y()<=tax.height()
    dialog.close();tax.close()
