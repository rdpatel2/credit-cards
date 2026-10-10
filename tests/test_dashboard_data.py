import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from copy import deepcopy
import time
import pytest
from PySide6.QtWidgets import QApplication
from card_compass.investments import InvestmentStore
from card_compass.dashboard.data_layer import DataLayer
from card_compass.dashboard.widget_registry import REGISTRY, create_widget


@pytest.fixture
def layer(tmp_path):
    app=QApplication.instance() or QApplication([])
    backend=InvestmentStore(tmp_path);value=DataLayer(backend,tmp_path,False)
    yield value
    value.shutdown();value.deleteLater();app.processEvents()


def test_subscriptions_dedupe_drop_unused_requirements_and_refresh_intervals(layer,monkeypatch):
    key=('quote','AAPL|1d');other=('modules','MSFT')
    layer.subscribe({key},'one');layer.subscribe({key,other},'two')
    assert layer.requested=={key,other}
    layer.unsubscribe('one');assert key in layer.requested
    layer.unsubscribe('two');assert not layer.requested
    layer.network_enabled=True;layer.subscribe({key},'one');layer.subscribe({key},'two')
    assert layer.pending=={key}
    layer.queue_timer.stop();layer.pending.clear()
    layer.loaded_at[key]=time.monotonic();calls=[]
    monkeypatch.setattr(layer,'refresh',lambda requirements:calls.append(requirements))
    layer.refresh_due();assert not calls
    layer.loaded_at[key]-=301;layer.refresh_due();assert calls==[{key}]


def test_widget_loading_empty_error_stale_and_success_states_do_not_crash(layer):
    definition=REGISTRY.get('analyst');config=dict(symbol='AAPL',analystScope='stock')
    component=create_widget(definition,config,None,False,lambda values:None,layer)
    assert 'No data' in component.message_label.text()
    layer.loading.add(('modules','AAPL'));layer.changed.emit('modules:AAPL')
    assert 'Loading' in component.message_label.text()
    layer.failed('modules','AAPL','Service unavailable')
    assert 'Use Refresh to retry' in component.message_label.text()
    layer.result('modules','AAPL',dict(periods=[],source='Example',asOf='2026-10-09'))
    assert 'No analyst coverage' in component.message_label.text()
    layer.result('modules','AAPL',dict(periods=[dict(period='0m',counts=[1,2,3,4,5])],source='Example',asOf='2026-10-09'))
    assert component.currentWidget() is component.content
    assert component.table.item(0,1).text().startswith('1 / 2 / 3 / 4 / 5 · score ')
    assert 'Strong sell' in component.table.horizontalHeaderItem(1).toolTip()
    layer.failed('modules','AAPL','Temporary failure')
    assert component.currentWidget() is component.content and 'Cached data' in component.footer_text
    assert 'Refresh to retry' in component.footer_text
    assert not component.last_error
    component.deleteLater()


def test_migrated_widget_options_keep_units_currency_sort_and_positions(layer):
    layer.result('modules','AAPL',dict(values={'profitMargins':.25,'debtToEquity':1.23},periods=[],currency='USD'))
    metrics=create_widget(REGISTRY.get('metrics'),dict(symbol='AAPL',metrics=['profitMargins','debtToEquity']),None,False,lambda values:None,layer)
    assert [metrics.table.item(row,1).text() for row in range(2)]==['25.00%','123.00%']
    layer.result('quote','AAPL|1d',dict(price=100,currency='CAD',changePercent=2,yearHigh=150,yearLow=80))
    stats=create_widget(REGISTRY.get('quote'),dict(symbol='AAPL'),None,False,lambda values:None,layer)
    assert stats.table.item(0,1).text()=='CAD 100' and stats.table.rowCount()==5
    layer.result('quote','MSFT|1d',dict(price=200,currency='USD',changePercent=-1))
    watch=create_widget(REGISTRY.get('watchlist'),dict(watchlistId='core',sort='price',showPositions=True),None,False,lambda values:None,layer)
    assert watch.table.item(0,0).text()=='MSFT' and watch.table.columnCount()==5
    for component in (metrics,stats,watch):component.deleteLater()
