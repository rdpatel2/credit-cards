from copy import deepcopy
import pytest
from card_compass.investments import initial_workspace,validate_workspace
from card_compass.security_research import compare,validate_config


def config(**changes):
    return dict(symbols=['AAA','BBB'],fields=['trailingPE','marketCap','dividendYield'],filterField=None,
                minimum=None,maximum=None,currency='USD',requireComplete=False,includeMissing=False,includeCachedDemo=True,**changes)


def data(ticker):
    return dict(values={'trailingPE':10 if ticker=='AAA' else 20,'marketCap':1000,'dividendYield':.025},currency='USD',financialCurrency='USD',source='Test provider',asOf='2025-01-01')


def test_fields_coverage_currency_and_missing_are_not_zero():
    result=compare(config(),lambda ticker:data(ticker) if ticker=='AAA' else None)
    assert result['rows'][0]['formatted']['dividendYield']=='2.50%'
    assert result['rows'][1]['mode']=='NOT LOADED' and result['rows'][1]['comparable']==0
    assert result['rows'][1]['formatted']['marketCap']=='Unavailable'
    filtered=config();filtered.update(filterField='trailingPE',maximum='15')
    result=compare(filtered,data)
    assert [row['ticker'] for row in result['rows']]==['AAA']
    assert result['excluded'][0]['reason']=='Outside filter bounds'


def test_monetary_filters_never_mix_native_currencies():
    fields=config();fields.update(fields=['freeCashflow'],filterField='freeCashflow',minimum='100')
    japanese=dict(data('AAA'),financialCurrency='JPY',values={'freeCashflow':10000})
    result=compare(fields,lambda ticker:japanese)
    assert not result['rows'] and 'currency mismatch' in result['excluded'][0]['reason']
    fields['includeMissing']=True
    result=compare(fields,lambda ticker:japanese)
    assert result['rows'][0]['formatted']['freeCashflow']=='JPY 10,000.00' and result['rows'][0]['comparable']==0
    fields.update(currency='JPY',requireComplete=True)
    assert len(compare(fields,lambda ticker:japanese)['rows'])==2


def test_coverage_and_source_filters_remain_explicit():
    settings=config();settings['includeCachedDemo']=False
    result=compare(settings,lambda ticker:dict(data(ticker),demo=True) if ticker=='AAA' else dict(data(ticker),stale=True))
    assert not result['rows'] and [row['mode'] for row in result['excluded']]==['DEMO','CACHED']
    settings.update(includeCachedDemo=True,requireComplete=True)
    assert not compare(settings,lambda ticker:dict(data(ticker),values={'trailingPE':10}))['rows']
    assert compare(config(),lambda ticker:dict(data(ticker),values=None))['rows'][0]['comparable']==0


def test_saved_view_validation_and_portability():
    state=initial_workspace();state['researchViews']=[dict(id='view',name='My comparison',config=config())]
    assert validate_workspace(state)['researchViews']==state['researchViews']
    for change in [dict(maximum='nan'),dict(minimum='10',maximum='5'),dict(fields=[]),dict(currency='XYZ')]:
        broken=deepcopy(state);broken['researchViews'][0]['config'].update(change)
        with pytest.raises(ValueError):validate_workspace(broken)
