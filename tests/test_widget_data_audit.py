"""Provider contracts and complete registry data flow; no network in tests."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from io import BytesIO
import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from card_compass.market_data import Provider, demo, parse_earnings, parse_news
from card_compass.investments import InvestmentStore, METRICS
from card_compass.dashboard.data_layer import DataLayer
from card_compass.dashboard.widget_registry import REGISTRY, create_widget, requirements


@pytest.fixture
def layer(tmp_path):
    app=QApplication.instance() or QApplication([])
    backend=InvestmentStore(tmp_path);backend.state['dataMode']='demo'
    backend.state['trades']['AAPL']=[dict(id='audit-trade',date='2026-01-01',quantity=2,price=100,fees=0,side='buy')]
    value=DataLayer(backend,tmp_path,False)
    yield value
    value.shutdown();value.deleteLater();app.processEvents()


@pytest.mark.parametrize('kind',[kind for kind in REGISTRY.definitions if kind!='unknown'])
def test_every_widget_receives_its_required_fields_and_renders(layer,kind):
    config=dict(symbol='AAPL',watchlistId='core',range='1mo',earningsDate='2026-10-09',metrics=list(METRICS),_noteKey='audit')
    layer.state['notes']['audit']='A saved thesis'
    expected=requirements(kind,config,layer.state)
    for request in expected:layer.result(*request,demo(*request))
    widget=create_widget(REGISTRY.get(kind),config,None,False,lambda values:None,layer)
    layer.queue_timer.stop()
    assert widget.keys==expected and layer.subscriptions[widget.subscription]==expected
    assert not widget.last_error,(kind,widget.last_error)
    assert widget.currentWidget() is widget.content, (kind,widget.message_label.text())
    if widget.table:assert widget.table.rowCount()>0
    if widget.chart:assert len(widget.chart.points)==61
    if kind=='metrics':assert all(widget.table.item(row,1).text()!='—' for row in range(len(METRICS)))
    if kind=='earnings':
        assert widget.table.item(0,3).text()=='Before market open'
        assert '2026-10-09' in widget.footer_text
    widget.deleteLater()


def test_watchlist_analyst_uses_all_symbols_including_cached_preview(layer):
    for ticker in layer.state['watchlists'][0]['symbols']:
        layer.result('modules',ticker,dict(demo('modules',ticker),stale=True))
    widget=create_widget(REGISTRY.get('analyst'),dict(symbol='TSLA',watchlistId='core',analystScope='watchlist'),None,False,lambda values:None,layer)
    layer.queue_timer.stop()
    assert ('modules','TSLA') not in widget.keys
    count=len(layer.state['watchlists'][0]['symbols'])
    assert widget.table.rowCount()==4 and f'Coverage: {count} of {count}' in widget.footer_text
    assert 'Cached' in widget.footer_text and not widget.last_error
    widget.deleteLater()


def test_provider_negotiation_accepts_plain_text_crumb(monkeypatch):
    provider=Provider();requests=[]
    def open_request(request,timeout):
        requests.append(request)
        return BytesIO(b'plain-text-crumb')
    monkeypatch.setattr(provider.opener,'open',open_request)
    assert provider.fetch('https://example.invalid/crumb',raw=True)==b'plain-text-crumb'
    assert requests[0].get_header('Accept')=='*/*'


def test_company_news_sector_tags_and_earnings_details():
    news=parse_news(b'<rss><channel><item><title>Apple expands AI investment</title><link>https://example.com/apple</link></item></channel></rss>')
    assert news['items'][0]['sectors']==['technology']
    raw=dict(data=dict(rows=[dict(symbol='AAPL',name='Apple',time='time-after-hours',epsForecast='1.20',lastYearEPS='1.10',noOfEsts=20)]))
    event=parse_earnings(raw,'2026-10-09')['items'][0]
    assert event['reportTime']=='After market close' and event['date']=='2026-10-09'
    assert event['lastYearEPS']=='1.10' and event['noOfEsts']==20
    assert parse_earnings(dict(data=dict(rows=None)),'2026-10-09')['items']==[]
    with pytest.raises(ValueError):parse_earnings(dict(data=None),'2026-10-09')


def test_data_mode_change_discards_inflight_results_and_failures(layer,monkeypatch):
    class Worker(QObject):
        result=Signal(str,str,object);failed=Signal(str,str,str);finished=Signal()
        def __init__(self,tasks,mode,parent):super().__init__(parent);self.tasks=tasks;self.mode=mode;self.running=False
        def start(self):self.running=True
        def isRunning(self):return self.running
        def requestInterruption(self):self.running=False
    monkeypatch.setattr('card_compass.dashboard.data_layer.MarketWorker',Worker)
    request=('modules','AAPL');layer.subscribe({request},'widget');layer.queue_timer.stop();layer.start_requests();old=layer.worker
    layer.network_enabled=True;layer.state['dataMode']='auto';layer.refresh();layer.queue_timer.stop()
    old.result.emit(*request,demo(*request));old.failed.emit(*request,'Old demo failure')
    assert not layer.cache and not layer.failures and layer.pending=={request}
    old.finished.emit();layer.queue_timer.stop();layer.start_requests()
    current=layer.worker;assert current.mode=='auto' and current.tasks==[request]
    current.result.emit(*request,dict(values={'trailingPE':20},periods=[],demo=False))
    assert layer.data(*request)['values']['trailingPE']==20 and layer.status(*request)=='ready'
    current.running=False;current.finished.emit()


def test_unavailable_metric_and_company_news_filter_have_clear_results(layer):
    layer.result('modules','AAPL',dict(demo('modules','AAPL'),values={'trailingPE':20}))
    widget=create_widget(REGISTRY.get('metrics'),dict(symbol='AAPL',metrics=['trailingPE','priceToSales']),None,False,lambda values:None,layer)
    assert widget.table.item(1,1).text()=='—' and 'Not supplied' in widget.table.item(1,1).toolTip()
    layer.result('news','AAPL',dict(parse_news(b'<rss><channel><item><title>Apple launches AI product</title><link>https://example.com</link></item></channel></rss>'),demo=True))
    news=create_widget(REGISTRY.get('news'),dict(symbol='AAPL',newsScope='company',sector='technology'),None,False,lambda values:None,layer)
    assert news.table.rowCount()==1 and 'Apple' in news.table.item(0,0).text()
    layer.queue_timer.stop();widget.deleteLater();news.deleteLater()
