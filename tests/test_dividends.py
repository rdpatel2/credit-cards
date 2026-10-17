from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal
from types import SimpleNamespace
import pytest
from card_compass.investments import initial_workspace,validate_workspace,InvestmentStore
from card_compass.dividends import event_values,save_event,year_review
from card_compass.investment_tax import estimate_tax,coverage,validate_profile
from card_compass.reports import rows_for
from tests.test_investments import PROFILE,trade


def workspace():
    state=initial_workspace()
    state['investmentAccounts'].append(dict(id='taxable',name='Brokerage',kind='brokerage',currency='USD',owner='Me'))
    state['investmentAccounts'].append(dict(id='roth',name='Roth',kind='roth',currency='USD',owner='Me'))
    return state


def event(account='taxable',**kwargs):
    return event_values(account,'TEST',date.today().isoformat(),100,source='Reviewed statement',**kwargs)


def test_qualified_is_part_of_gross_and_account_treatment_is_separate():
    state=workspace()
    state=save_event(state,event(classification='mixed',qualified=60,withheld=10))
    state=save_event(state,event('roth',classification='qualified',qualified=100))
    state=save_event(state,event('legacy'))
    summary=year_review(validate_workspace(state),date.today().year)
    assert summary['taxable']==dict(grossCents=10000,qualifiedCents=6000,nonqualifiedCents=4000,unclassifiedCents=0,withheldCents=1000)
    assert summary['excluded']['grossCents']==20000 and summary['excluded']['count']==2
    assert not state['trades']
    assert year_review(state,date.today().year,'roth')['taxable']['grossCents']==0


def test_unclassified_money_is_not_inferred_to_be_qualified_or_nonqualified():
    state=save_event(workspace(),event())
    result=year_review(state,date.today().year)
    assert result['taxable']['unclassifiedCents']==10000
    assert result['taxable']['nonqualifiedCents']==0
    assert any('unclassified' in warning for warning in result['warnings'])
    assert not year_review(state,date.today().year-1)['events']


@pytest.mark.parametrize('changes',[dict(qualifiedCents=10100),dict(withheldCents=10100),dict(classification='qualified',qualifiedCents=0),dict(classification='mixed',qualifiedCents=0),dict(accountId='missing'),dict(paid=(date.today()+timedelta(days=1)).isoformat()),dict(grossCents=0),dict(currency='EUR')])
def test_invalid_dividend_records_cannot_enter_workspace(changes):
    row=event();row.update(changes)
    state=workspace();state['dividendEvents']=[row]
    with pytest.raises(ValueError):validate_workspace(state)


def test_duplicates_require_review_and_corrections_preserve_identity_and_backups(tmp_path):
    store=InvestmentStore(tmp_path);state=workspace();row=event()
    state=save_event(state,row);before=deepcopy(state)
    with pytest.raises(ValueError,match='matching account'):save_event(state,event())
    assert state==before
    corrected=dict(row,grossCents=12000)
    state=save_event(state,corrected)
    assert len(state['dividendEvents'])==1 and state['dividendEvents'][0]['id']==row['id']
    duplicate=event_values('taxable','TEST',date.today().isoformat(),120,source='A separate payment on the same date')
    with pytest.raises(ValueError,match='matching account'):save_event(state,duplicate)
    state=save_event(state,duplicate,acknowledge_duplicate=True)
    store.save(state)
    assert InvestmentStore(tmp_path).state['dividendEvents']==state['dividendEvents']
    owner=SimpleNamespace(finance_store=None,investment_store=store)
    headers,rows=rows_for(owner,'dividends',date.today().isoformat(),date.today().isoformat())
    assert 'qualified_portion' in headers and len(rows)==2
    assert len(rows[0])==len(headers)


def test_fifo_disposals_are_filtered_by_year_account_and_future_date():
    state=workspace()
    buy=dict(trade('a',2,10,'2024-01-01'),accountId='taxable')
    sale=dict(trade('b',1,20,'2025-01-02',side='sell'),accountId='taxable')
    future=dict(trade('c',1,30,(date.today()+timedelta(days=1)).isoformat(),side='sell'),accountId='taxable')
    state['trades']['TEST']=[buy,sale,future]
    result=year_review(state,2025)
    assert len(result['disposals'])==1 and result['disposals'][0]['gain']==Decimal(10)
    assert not year_review(state,2025,'roth')['disposals']
    assert any('future-dated' in warning for warning in result['warnings'])


def test_supported_year_rates_federal_only_and_unsupported_scope():
    profile=dict(PROFILE,year=2025,deduction=15750)
    assert estimate_tax(profile,0,1000)['total']==pytest.approx(192.5)
    assert estimate_tax(dict(profile,ordinaryIncome=15750+48475-500),1000,0)['federal']==pytest.approx(170)
    assert estimate_tax(dict(profile,ordinaryIncome=15750+48000),0,1000)['federal']==pytest.approx(97.5)
    federal=estimate_tax(dict(PROFILE,state='FEDERAL'),0,1000)
    assert federal['state'] is None and not federal['stateCovered'] and federal['total']==federal['federal']
    for changed in (dict(year=2027),dict(state='CA'),dict(country='CA'),dict(residentUS=False),dict(fullYearNC=False)):
        scope=dict(PROFILE,**changed);validate_profile(scope)
        assert coverage(scope)
        with pytest.raises(ValueError):estimate_tax(scope,0,1000)
    legacy=validate_workspace(dict(workspace(),taxProfile=PROFILE))
    assert estimate_tax(legacy['taxProfile'],1000,0)['federal']==pytest.approx(220)
    with pytest.raises(ValueError,match='Loss scenarios'):estimate_tax(PROFILE,-100,1000)
