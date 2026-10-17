import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtCore import QEvent
from card_compass.app import MainWindow
from card_compass.broker_import_ui import BrokerImportDialog, InvestmentAccountsDialog
from card_compass.reports import write_csv
from card_compass.broker_import import TRADE_COLUMNS, POSITION_COLUMNS
from card_compass.stocks_ui import TradeDialog
from tests.test_activity_import import buy, sell, write_activity


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path/'profile',auto_refresh=False)
    yield window
    window.close();window.deleteLater()
    app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_preview_maps_new_account_and_reimport_deduplicates(window,tmp_path,monkeypatch):
    path=tmp_path/'broker.csv'
    write_csv(path,TRADE_COLUMNS,[['Broker','b1','2025-01-01','TEST','buy',3,10,0,'USD']])
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(path),''))
    dialog=BrokerImportDialog(window.stocks_page)
    before=deepcopy(window.investment_store.state)
    dialog.load_trades()
    assert dialog.mappings['Broker'].currentData() is None
    dialog.make_preview()
    assert 'Add 1 trades' in dialog.status.text() and dialog.confirm.isEnabled()
    assert window.investment_store.state==before
    dialog.save()
    assert dialog.result()==dialog.DialogCode.Accepted
    assert window.stocks_page.trade_table.item(0,6).text()=='Broker'
    again=BrokerImportDialog(window.stocks_page)
    again.load_trades();again.make_preview()
    assert 'skip 1 identical' in again.status.text()
    assert not again.preview['added']
    trade=TradeDialog(window.stocks_page,'TEST',window.investment_store.state['trades']['TEST'][0])
    assert trade.values()['externalId']=='b1' and trade.account.currentText()=='Broker'
    trade.close();dialog.close();again.close()


def test_snapshot_mismatch_requires_visible_acknowledgement(window,tmp_path,monkeypatch):
    path=tmp_path/'broker.csv'
    write_csv(path,TRADE_COLUMNS,[['Broker','b1','2025-01-01','TEST','buy',3,10,0,'USD']])
    snapshot=tmp_path/'position.csv'
    write_csv(snapshot,POSITION_COLUMNS,[['Broker','2025-02-01','TEST',5,'USD']])
    selected=[str(path),str(snapshot)]
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(selected.pop(0),''))
    dialog=BrokerImportDialog(window.stocks_page)
    dialog.load_trades();dialog.load_positions();dialog.make_preview();dialog.save()
    assert 'not saved' in dialog.status.text() and not window.investment_store.state['trades']
    assert not dialog.variances.isHidden()
    dialog.variances.setChecked(True);dialog.save()
    assert window.investment_store.portfolio()['holdings']['TEST']['quantity']==3
    assert 'differences were acknowledged' in window.stocks_page.status.text()
    dialog.close()


def test_account_metadata_edit_preserves_lot_identity_and_prior_preview_invalidates(window,tmp_path,monkeypatch):
    parent=window.stocks_page
    dialog=InvestmentAccountsDialog(parent)
    dialog.name.setText('My Roth');dialog.owner_name.setText('Partner')
    dialog.kind.setCurrentIndex(dialog.kind.findData('roth'));dialog.save()
    account=window.investment_store.state['investmentAccounts'][-1]
    assert account['kind']=='roth' and account['owner']=='Partner'
    path=tmp_path/'broker.csv'
    write_csv(path,TRADE_COLUMNS,[['Broker','1','2025-01-01','TEST','buy',1,10,0,'USD']])
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(path),''))
    importer=BrokerImportDialog(parent)
    importer.load_trades();importer.make_preview()
    importer.mappings['Broker'].setCurrentIndex(importer.mappings['Broker'].findData(account['id']))
    assert importer.preview is None and not importer.confirm.isEnabled()
    importer.make_preview();importer.save()
    assert window.investment_store.state['trades']['TEST'][0]['accountId']==account['id']
    dialog.close();importer.close()


def test_invalid_selected_file_clears_confirmable_preview(window,tmp_path,monkeypatch):
    valid=tmp_path/'valid.csv';bad=tmp_path/'bad.csv'
    write_csv(valid,TRADE_COLUMNS,[['Broker','1','2025-01-01','TEST','buy',1,10,0,'USD']])
    bad.write_text('wrong,columns\n1,2\n')
    paths=[str(valid),str(bad)]
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(paths.pop(0),''))
    dialog=BrokerImportDialog(window.stocks_page)
    dialog.load_trades();dialog.make_preview()
    assert dialog.confirm.isEnabled()
    dialog.load_trades()
    assert not dialog.confirm.isEnabled() and dialog.preview is None and not dialog.rows
    dialog.close()


def test_activity_upload_previews_buys_and_skips_overlapping_purchases(window,tmp_path,monkeypatch):
    path=write_activity(tmp_path/'activity.csv',[buy(),buy(**{'Type':'Dividend','Quantity':''})])
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(path),''))
    parent=window.stocks_page
    dialog=BrokerImportDialog(parent)
    before=deepcopy(parent.store.state)
    dialog.load_trades()
    assert '1 BUY rows; 0 SELL rows; 1 other activity rows ignored' in dialog.files.text()
    dialog.make_preview()
    assert 'Add 1 trades' in dialog.status.text() and parent.store.state==before
    dialog.save()
    assert parent.store.state['trades']['VOO'][0]['date']=='2025-01-03'
    assert parent.store.portfolio()['holdings']['VOO']['basis']==125
    write_activity(path,[buy(**{'Trade date':'1/10/2025'}),buy()])
    again=BrokerImportDialog(parent)
    again.load_trades()
    assert again.mappings['Vanguard CASH'].currentData() is not None
    again.make_preview()
    assert 'Add 1 trades' in again.status.text() and 'skip 1 identical' in again.status.text()
    assert again.results.rowCount()==2 and again.results.item(1,0).text()=='Skip duplicate'
    assert not again.preview['possible']
    again.save()
    assert len(parent.store.state['trades']['VOO'])==2
    assert parent.store.portfolio()['holdings']['VOO']['quantity']==2.5
    assert 'skipped 1 identical' in parent.status.text()
    dialog.close();again.close()


def test_activity_sale_updates_holdings_and_reupload_skips_buys_and_sells(window,tmp_path,monkeypatch):
    path=write_activity(tmp_path/'activity.csv',[buy(),sell()])
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args:(str(path),''))
    parent=window.stocks_page
    dialog=BrokerImportDialog(parent)
    dialog.load_trades();dialog.make_preview()
    assert '1 BUY rows; 1 SELL rows; 0 other activity rows ignored' in dialog.files.text()
    assert dialog.results.item(1,2).text()=='sell · 2025-01-10'
    dialog.save()
    assert parent.store.portfolio()['holdings']['VOO']['quantity']==0.75
    assert parent.store.portfolio()['holdings']['VOO']['realizedGain']==9
    again=BrokerImportDialog(parent)
    again.load_trades();again.make_preview()
    assert 'Add 0 trades' in again.status.text() and 'skip 2 identical' in again.status.text()
    again.save()
    assert len(parent.store.state['trades']['VOO'])==2
    dialog.close();again.close()


def test_positions_import_csv_action_opens_importer_without_selected_position(window,monkeypatch):
    opened=[]
    monkeypatch.setattr(BrokerImportDialog,'exec',lambda dialog:opened.append(dialog))
    component=window.stocks_page.positions_component
    component.table.clearSelection()
    component.handle_action('import_broker_csv')
    assert len(opened)==1 and isinstance(opened[0],BrokerImportDialog)
    opened[0].close()
