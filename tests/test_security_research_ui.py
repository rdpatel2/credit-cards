import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import pytest
from PySide6.QtWidgets import QApplication,QInputDialog
from PySide6.QtCore import QEvent,Qt
from card_compass.app import MainWindow
from card_compass.security_research_ui import SecurityResearchDialog
from card_compass.investments import InvestmentStore
from tests.test_security_research import data


@pytest.fixture
def window(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(tmp_path,auto_refresh=False)
    for ticker in ('AAA','BBB'):window.stocks_page.data_layer.cache['modules:'+ticker]=data(ticker)
    yield window
    window.close();window.deleteLater();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_selected_tickers_saved_filters_and_missing_fields(window,monkeypatch):
    dialog=SecurityResearchDialog(window.stocks_page)
    dialog.tickers.setText('AAA, BBB')
    dialog.filter_field.setCurrentIndex(dialog.filter_field.findData('trailingPE'));dialog.maximum.setText('15')
    assert dialog.table.rowCount()==1 and dialog.table.item(0,0).text()=='AAA'
    assert dialog.table.item(0,1).text()=='PUBLIC'
    dialog.resize(768,752);dialog.show();QApplication.instance().processEvents()
    assert dialog.table.viewport().height()>=180
    if hasattr(dialog,'text_scroll'):assert dialog.text_scroll.horizontalScrollBar().maximum()==0
    monkeypatch.setattr(QInputDialog,'getText',lambda *args,**kwargs:('My screen',True))
    dialog.save_view()
    saved=window.investment_store.state['researchViews'][0]
    assert saved['config']['maximum']=='15'
    assert InvestmentStore(window.directory).state['researchViews']==[saved]
    dialog.maximum.clear();dialog.tickers.setText('AAA, MISSING')
    assert dialog.table.rowCount()==1  # range filter excludes missing unless included
    dialog.missing.setChecked(True)
    assert dialog.table.rowCount()==2 and dialog.table.item(1,1).text()=='NOT LOADED'
    dialog.views.setCurrentIndex(0);dialog.views.setCurrentIndex(1)
    assert dialog.tickers.text()=='AAA, BBB' and dialog.maximum.text()=='15'
    dialog.reject()


def test_percentage_bounds_source_visibility_and_subscription_cleanup(window):
    dialog=SecurityResearchDialog(window.stocks_page);dialog.tickers.setText('AAA, BBB')
    dialog.filter_field.setCurrentIndex(dialog.filter_field.findData('dividendYield'));dialog.minimum.setText('3')
    assert dialog.config()['minimum']=='0.03' and dialog.table.rowCount()==0
    dialog.minimum.setText('2')
    window.stocks_page.data_layer.cache['modules:AAA']['stale']=True
    window.stocks_page.data_layer.changed.emit('modules:AAA')
    assert dialog.table.item(0,1).text()=='CACHED'
    dialog.cached.setChecked(False)
    assert dialog.table.rowCount()==1 and dialog.table.item(0,0).text()=='BBB'
    dialog.refresh()
    assert dialog.subscription in window.stocks_page.data_layer.subscriptions
    dialog.tickers.setText('BBB')
    assert dialog.subscription not in window.stocks_page.data_layer.subscriptions
    dialog.reject()
