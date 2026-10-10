from copy import deepcopy
from decimal import Decimal
import pytest
from card_compass.investments import InvestmentStore, initial_workspace, validate_workspace, position, simulate_sale, long_term
from card_compass.investment_tax import estimate_tax
from card_compass.market_data import parse_chart, parse_modules, parse_news


def trade(ident,quantity,price,day='2024-01-02',fees=0,side='buy'):
    return dict(id=ident,quantity=quantity,price=price,date=day,fees=fees,side=side)


def test_fifo_and_fractional_positions_match_folio():
    p=position([trade('a',10,100,fees=10),trade('b',10,200,'2025-01-01'),trade('c',15,300,'2026-01-03',15,'sell')])
    assert (p['quantity'],p['basis'],p['averageCost'],p['purchaseAverage'],p['realizedGain'])==(5,1000,200,Decimal('150.5'),2475)
    assert [d['basis'] for d in p['disposals']]==[1010,1000]
    p=position([trade('a',.1,10),trade('b',.2,20),trade('c',.3,30,'2026-01-01',side='sell')])
    assert (p['quantity'],p['basis'],p['averageCost'],p['realizedGain'])==(0,0,None,4)


def test_same_day_order_and_invalid_edits(tmp_path):
    store=InvestmentStore(tmp_path)
    store.trade('AAPL',trade('a',1,10))
    store.trade('AAPL',trade('b',1,20,'2025-01-01',side='sell'))
    before=store.path.read_bytes()
    with pytest.raises(ValueError,match='exceeds'):store.trade('AAPL',trade('a',1,10),delete=True)
    assert store.path.read_bytes()==before
    assert store.portfolio()['basis']==0
    with pytest.raises(ValueError):position([trade('a',1,1,side='sell'),trade('b',1,1)])
    for bad in [trade('a',-1,1),trade('a',1,float('nan')),trade('a',1,1,'2025-02-29')]:
        with pytest.raises(ValueError):position([bad])


def test_sale_scenario_calendar_anniversary_and_no_mutation():
    trades=[trade('a',1,10),trade('b',1,20,'2026-01-02')]
    result=simulate_sale(trades,trade('test',2,30,'2026-02-01',2))
    assert (result['shortGain'],result['longGain'])==(9,19)
    assert len(trades)==2
    assert not long_term('2024-02-29','2025-02-28')
    assert long_term('2024-02-29','2025-03-01')
    with pytest.raises(ValueError,match='follow'):simulate_sale(trades,trade('c',1,30,'2025-01-01'))


def test_folio_import_retains_layouts_notes_overrides_and_trades(tmp_path):
    from card_compass.dashboard.layout_engine import ENGINE
    state=initial_workspace();state['pages'][0]['settings']['compact']=False
    state['pages'][0]['layouts']['lg']=ENGINE.move(state['pages'][0]['layouts']['lg'],state['pages'][0]['widgets'][0]['id'],dict(x=1,w=5,y=3),False)
    state['notes']['AAPL']='My thesis';state['trades']['AAPL']=[trade('a',1.25,100)]
    state['stockOverrides']['AAPL']=deepcopy(state['stockTemplate'])
    state['newsPages']['technology']=deepcopy(state['stockTemplate'])
    state['stockTemplate']['widgets'][0]['config']['showVolume']=False
    candidate=validate_workspace(state)
    assert candidate==state
    store=InvestmentStore(tmp_path);store.save(candidate)
    assert InvestmentStore(tmp_path).state==state
    broken=deepcopy(state);broken['trades']['AAPL'].append(trade('b',2,100,'2026-01-01',side='sell'))
    with pytest.raises(ValueError):store.save(broken)
    assert InvestmentStore(tmp_path).state==state


def test_corrupt_workspace_is_preserved_before_first_save(tmp_path):
    path=tmp_path/'investments-workspace.json';path.write_text('{invalid')
    store=InvestmentStore(tmp_path);assert store.error
    store.save(initial_workspace())
    assert next(tmp_path.glob('investments-unreadable-*.json')).read_text()=='{invalid'


PROFILE=dict(year=2026,state='NC',filingStatus='single',ordinaryIncome=100000,preferentialIncome=0,magi=100000,investmentIncome=0,deduction=16100,stateDeduction=12750)


def test_tax_port_matches_folio_stacking_brackets_niit():
    assert estimate_tax(PROFILE,1000,0)['federal']==pytest.approx(220)
    assert estimate_tax(PROFILE,0,1000)['total']==pytest.approx(189.9)
    assert estimate_tax(dict(PROFILE,ordinaryIncome=16100+50400-500),1000,0)['federal']==pytest.approx(170)
    assert estimate_tax(dict(PROFILE,ordinaryIncome=16100+49000),0,1000)['federal']==pytest.approx(82.5)
    assert estimate_tax(dict(PROFILE,magi=199500),0,1000)['niit']==pytest.approx(19)
    assert estimate_tax(dict(PROFILE,ordinaryIncome=0,magi=0),1000,0)['total']==0
    with pytest.raises(ValueError):estimate_tax(dict(PROFILE,year=2027),0,1000)


def test_chart_parsing_daily_reference_and_missing_points():
    raw=dict(chart=dict(result=[dict(meta=dict(symbol='AAPL',currency='USD',regularMarketPrice=110,regularMarketTime=1736974800,chartPreviousClose=50),timestamp=[1,2,3],indicators=dict(quote=[dict(close=[100,None,110])]))]))
    parsed=parse_chart(raw,100)
    assert parsed['changePercent']==pytest.approx(10)
    assert parsed['points']==[(1,100),(3,110)]
    assert parsed['high'] is None
    raw['chart']['result'][0]['meta']['regularMarketPrice']=float('nan')
    with pytest.raises(ValueError):parse_chart(raw)


def test_fundamentals_units_and_analyst_validation():
    row=dict(period='0m',strongSell=1,sell=2,hold=3,buy=4,strongBuy=5)
    raw=dict(quoteSummary=dict(result=[dict(financialData=dict(debtToEquity=dict(raw=123),profitMargins=dict(raw=.25)),defaultKeyStatistics=dict(trailingEps=dict(raw=-1),trailingPE=dict(raw=10)),recommendationTrend=dict(trend=[row,row,dict(row,period='-1m',buy=-1)]))]))
    parsed=parse_modules(raw)
    assert parsed['values']['debtToEquity']==1.23
    assert parsed['values']['trailingPE'] is None
    assert parsed['periods']==[dict(period='0m',counts=[1,2,3,4,5])]
    with pytest.raises(ValueError):parse_news(b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss/>')
    parsed=parse_news(b'<rss><channel><item><title>X</title><link>file:///tmp/x</link></item><item><title>Y</title><link>https://example.com</link></item></channel></rss>')
    assert [item['title'] for item in parsed['items']]==['Y']
