import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEvent
from card_compass.app import MainWindow
from card_compass.portfolio_analysis_ui import AllocationDialog,ClassificationDialog
from tests.test_portfolio_analysis import state_with_positions,quotes


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    window.investment_store.save(state_with_positions());window.stocks_page.reload()
    for ticker in ('AAA','BBB'):window.stocks_page.data_layer.cache['quote:'+ticker+'|1d']=quotes(ticker)
    yield window
    window.close();window.deleteLater();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_allocation_dialog_partial_quotes_basis_and_account_filter(window):
    owner=window.stocks_page
    dialog=AllocationDialog(owner)
    assert 'Largest security: 60.0%' in dialog.summary.text()
    assert 'Unclassified sector share: 100.0%' in dialog.details.text()
    owner.data_layer.cache['quote:BBB|1d']['stale']=True
    owner.data_layer.changed.emit('quote:BBB|1d')
    assert 'Weights and concentration are unavailable' in dialog.summary.text()
    assert dialog.table.item(0,3).text()=='Unavailable'
    dialog.mode.setCurrentIndex(dialog.mode.findData('basis'))
    assert dialog.table.item(0,3).text()=='25.0%'
    dialog.account.setCurrentIndex(dialog.account.findData('ira'))
    assert dialog.table.rowCount()==1 and dialog.table.item(0,0).text()=='BBB'
    subscription=dialog.subscription
    assert subscription in owner.data_layer.subscriptions
    dialog.reject()
    assert subscription not in owner.data_layer.subscriptions


def test_classification_saves_explicit_manual_labels_without_trades_changes(window,monkeypatch):
    dialog=AllocationDialog(window.stocks_page)
    before=deepcopy(window.investment_store.state['trades'])
    def accept(editor):
        editor.asset.setCurrentIndex(editor.asset.findData('equity'))
        editor.sector.setCurrentIndex(editor.sector.findData('technology'))
        return editor.DialogCode.Accepted
    monkeypatch.setattr(ClassificationDialog,'exec',accept)
    dialog.table.selectRow(0);dialog.edit_classification()
    assert window.investment_store.state['securityClassifications']['AAA']['source']=='manual'
    assert dialog.group_tables['sector'].item(0,0).text()=='Unclassified'
    assert '60.0%' in dialog.details.text()  # other security remains unclassified
    assert window.investment_store.state['trades']==before
    dialog.reject()
