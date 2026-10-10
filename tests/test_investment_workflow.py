"""Purchased securities, upcoming earnings, and populated income actions."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
from datetime import date

import pytest
from PySide6.QtCore import QDate, QEvent, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton, QTableWidget

from card_compass.app import MainWindow
from card_compass.investments import InvestmentStore, initial_workspace, validate_workspace
from card_compass.dashboard.widget_registry import REGISTRY, create_widget, requirements, WATCHLIST_MATCH_ROLE
from card_compass.trading_calendar import next_trading_day
from card_compass.theme import color
from tests.test_investments import trade


@pytest.mark.parametrize('today,expected',[
    ('2026-10-08','2026-10-09'),('2026-10-09','2026-10-12'),
    ('2026-10-10','2026-10-12'),('2026-10-11','2026-10-12'),
    ('2026-04-02','2026-04-06'),('2026-06-18','2026-06-22'),
    ('2026-07-02','2026-07-06'),('2026-11-25','2026-11-27'),
    ('2026-12-24','2026-12-28'),('2026-12-31','2027-01-04')])
def test_next_session_skips_exchange_closures_but_includes_early_close_days(today,expected):
    assert next_trading_day(date.fromisoformat(today)).isoformat()==expected


def test_buys_add_stocks_and_etfs_once_to_the_selected_watchlist(tmp_path):
    store=InvestmentStore(tmp_path)
    store.state['watchlists'].append(dict(id='funds',name='My investments',symbols=[]))
    for ticker in ('PLTR','QQQM'):
        store.trade(ticker,trade('first',2,100),watchlist_id='funds')
        store.trade(ticker,trade('second',1,110),watchlist_id='funds')
        store.trade(ticker,trade('first',2,105),watchlist_id='funds')
    saved=InvestmentStore(tmp_path).state
    assert saved['watchlists'][1]['symbols']==['PLTR','QQQM']
    assert all(s not in saved['watchlists'][0]['symbols'] for s in ('PLTR','QQQM'))


def test_existing_and_imported_purchases_are_backfilled_without_duplicate_symbols():
    state=initial_workspace()
    state['watchlists'].append(dict(id='funds',name='Funds',symbols=['QQQM']))
    state['trades']={'QQQM':[trade('q',1,100)],'AMD':[trade('a',1,100)],
                     'SPY':[trade('b',1,100),trade('s',1,110,'2025-01-01',side='sell')]}
    validated=validate_workspace(state)
    assert 'AMD' in validated['watchlists'][0]['symbols'] and 'SPY' in validated['watchlists'][0]['symbols']
    assert 'QQQM' not in validated['watchlists'][0]['symbols']
    assert validated['trades']==state['trades']
    assert validate_workspace(validated)==validated


def test_full_watchlists_do_not_prevent_tracking_new_purchases(tmp_path):
    store=InvestmentStore(tmp_path)
    store.state['watchlists'][0]['symbols']=[f'T{i}' for i in range(30)]
    store.trade('QQQM',trade('q',1,100))
    assert len(store.state['watchlists'][0]['symbols'])==30
    assert store.state['watchlists'][1]['symbols']==['QQQM']


def test_invalid_purchase_does_not_change_watchlists(tmp_path):
    store=InvestmentStore(tmp_path);before=deepcopy(store.state)
    with pytest.raises(ValueError):store.trade('QQQM',trade('q',-1,100))
    assert store.state==before


@pytest.fixture
def owner(tmp_path):
    app=QApplication.instance() or QApplication([])
    for old in app.topLevelWidgets():
        if isinstance(old,MainWindow) and not old.isVisible():old.deleteLater()
    app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    window=MainWindow(tmp_path,auto_refresh=False)
    yield window
    window.close();window.deleteLater();app.sendPostedEvents(None,QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('dark',[False,True])
def test_populated_income_action_button_fits_and_opens_the_selected_source(owner,dark):
    owner.apply_theme(dark,persist=False)
    page=owner.overview_page;page.month.setDate(QDate(2026,10,1))
    ident=owner.finance_store.save_entry('Salary with descenders: gypqj','income','salary',4500,'2026-10',True)
    opened=[];errors=[]
    page.edit_entry=lambda row=None,**kwargs:opened.append(row['id'])
    def inspect():
        dialog=QApplication.activeModalWidget()
        try:
            table=dialog.findChild(QTableWidget);button=table.cellWidget(0,2)
            option=QStyleOptionButton();option.initFrom(button);option.text=button.text()
            area=button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents,option,button)
            assert area.height()>=button.fontMetrics().height()+2
            assert area.width()>=button.fontMetrics().horizontalAdvance(button.text())
            assert table.visualRect(table.model().index(0,2)).contains(button.geometry())
            button.click()
        except Exception as exc:errors.append(exc)
        finally:dialog.reject()
    timer=QTimer(owner);timer.setSingleShot(True);timer.timeout.connect(inspect);timer.start(0)
    page.show_income();timer.stop()
    assert not errors,errors
    assert opened==[ident]


def test_record_purchase_updates_watchlist_and_visible_positions(owner):
    page=owner.stocks_page
    page.store.state['watchlists'].append(dict(id='funds',name='My ETFs',symbols=[]))
    page.positions_component.onConfigChange(dict(watchlistId='funds'))
    def enter_purchase():
        dialog=QApplication.activeModalWidget();dialog.quantity.setValue(2);dialog.price.setValue(100);dialog.accept()
    timer=QTimer(owner);timer.setSingleShot(True);timer.timeout.connect(enter_purchase);timer.start(0)
    page.record_trade('QQQM',watchlist_id='funds');timer.stop()
    assert page.store.state['watchlists'][1]['symbols']==['QQQM']
    assert page.positions_component.table.item(0,0).text()=='QQQM'
    assert page.positions_component.table.item(0,4).text()=='2'
    assert InvestmentStore(owner.directory).state['watchlists'][1]['symbols']==['QQQM']


@pytest.mark.parametrize('dark',[False,True])
def test_earnings_uses_next_session_and_promotes_highlighted_watchlist_matches(owner,monkeypatch,dark):
    owner.apply_theme(dark,persist=False)
    monkeypatch.setattr('card_compass.trading_calendar.market_today',lambda:date(2026,10,9))
    layer=owner.stocks_page.data_layer
    layer.state['watchlists'].append(dict(id='other',name='Other list',symbols=['QQQM']))
    config={'limit':2,'earningsDate':''}
    assert requirements('earnings',config,layer.state)=={('earnings','2026-10-12')}
    items=[dict(symbol=s,name=s,epsForecast=0,date='2026-10-12') for s in ('OTHER','QQQM','AAPL')]
    layer.result('earnings','2026-10-12',dict(items=items,source='Test feed'))
    widget=create_widget(REGISTRY.get('earnings'),config,None,False,lambda changes:None,layer)
    assert not widget.last_error
    assert [widget.table.item(i,0).text() for i in range(2)]==['QQQM','AAPL']
    assert widget.table.item(0,2).text()=='0'
    for row in range(2):
        assert widget.table.item(row,0).font().bold()
        assert 'On your watchlist' in widget.table.item(row,0).toolTip()
        assert widget.table.item(row,0).data(WATCHLIST_MATCH_ROLE)
    widget.resize(720,300);widget.show();QTest.qWait(5)
    rect=widget.table.visualRect(widget.table.model().index(0,0))
    rendered=widget.table.viewport().grab().toImage()
    assert rendered.pixelColor(rect.left()+3,rect.center().y()).name()==color('#187148')
    assert '2026-10-12 · Next trading day' in widget.footer_text
    assert '2 watchlist matches' in widget.footer_text
    layer.state['watchlists'][0]['symbols'].remove('AAPL');layer.changed.emit('workspace')
    assert [widget.table.item(i,0).text() for i in range(2)]==['QQQM','OTHER']
    assert not widget.table.item(1,0).font().bold()
    # An automatic calendar rolls forward while the application remains open.
    monkeypatch.setattr('card_compass.trading_calendar.market_today',lambda:date(2026,10,12))
    widget.update_earnings_session()
    assert widget.keys=={('earnings','2026-10-13')}
    assert '2026-10-13' in widget.footer_text
    widget.set_config(dict(config,earningsDate='2026-10-09'))
    assert widget.keys=={('earnings','2026-10-09')}
    widget.deleteLater()
