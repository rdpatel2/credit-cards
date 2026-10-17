from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import pytest
from card_compass.investments import initial_workspace
from card_compass.performance import empty_records,save_record,delete_record,calculate,calculate_all,benchmark_return


def value(state,day,amount):return save_record(state,'legacy',day,amount,'Broker statement')
def flow(state,day,amount,kind='contribution'):return save_record(state,'legacy',day,amount,'Broker transfer',kind)


def test_no_flow_returns_match_independent_one_year_fixture():
    state=value(initial_workspace(),'2025-01-01',100)
    state=value(state,'2026-01-01',120)
    result=calculate(state['performanceRecords'],'legacy','2025-01-01','2026-01-01')
    assert result['twr']==Decimal('.2') and result['twrAnnualized']==pytest.approx(.2)
    assert result['mwr']==pytest.approx(.2,abs=1e-10) and result['gain']==20


def test_deposits_are_not_gains_and_daily_flow_returns_chain():
    state=value(initial_workspace(),'2025-01-01',100)
    state=flow(state,'2025-07-02',100)
    state=value(state,'2025-07-02',210)
    state=value(state,'2026-01-01',231)
    result=calculate(state['performanceRecords'],'legacy','2025-01-01','2026-01-01')
    assert result['gain']==31 and result['netFlows']==100 and result['twr']==Decimal('.21')
    assert [row['returnRate'] for row in result['intervals']]==[Decimal('.1'),Decimal('.1')]
    rate=result['mwr']
    # Independent dollar-growth identity, rather than the solver's discounted equation.
    assert 100*(1+rate)+100*(1+rate)**(183/365)==pytest.approx(231,abs=1e-8)


def test_missing_flow_valuation_withholds_twr_but_mwr_can_still_work():
    state=value(initial_workspace(),'2025-01-01',100)
    state=flow(state,'2025-06-01',100)
    state=value(state,'2026-01-01',220)
    result=calculate(state['performanceRecords'],'legacy','2025-01-01','2026-01-01')
    assert result['twr'] is None and '2025-06-01' in result['twrError']
    assert result['mwr']>0 and result['gain']==20


def test_withdrawal_and_start_day_flows_have_explicit_boundary_semantics():
    state=value(initial_workspace(),'2025-01-01',100)
    state=flow(state,'2025-01-01',100)  # included in opening value, outside reporting range
    state=flow(state,'2026-01-01',110,'withdrawal')
    state=value(state,'2026-01-01',0)
    result=calculate(state['performanceRecords'],'legacy','2025-01-01','2026-01-01')
    assert result['twr']==Decimal('.1') and result['mwr']==pytest.approx(.1)
    assert result['netFlows']==-110 and result['gain']==10 and len(result['flows'])==1


def test_zero_capital_and_multiple_sign_changes_are_not_misleading_rates():
    state=value(initial_workspace(),'2025-01-01',100)
    state=flow(state,'2025-03-01',50,'withdrawal');state=value(state,'2025-03-01',50)
    state=flow(state,'2025-05-01',50);state=value(state,'2025-05-01',100)
    state=value(state,'2026-01-01',100)
    result=calculate(state['performanceRecords'],'legacy','2025-01-01','2026-01-01')
    assert result['twr']==0 and result['mwr'] is None and 'multiple internal' in result['mwrError']
    zero=value(initial_workspace(),'2025-01-01',0);zero=value(zero,'2026-01-01',100)
    with pytest.raises(ValueError,match='positive'):calculate(zero['performanceRecords'],'legacy','2025-01-01','2026-01-01')


def test_record_identity_correction_and_validation_are_immutable():
    state=initial_workspace()
    before=deepcopy(state)
    with pytest.raises(ValueError,match='decimal'):value(state,'2025-01-01','10.001')
    with pytest.raises(ValueError,match='valid investment'):save_record(state,'missing','2025-01-01',100,'Broker')
    assert state==before
    state=value(state,'2025-01-01',100);state=value(state,'2025-01-01',200)
    assert len(state['performanceRecords']['valuations'])==1
    state=delete_record(state,'legacy','valuations','2025-01-01')
    assert not state['performanceRecords']['valuations']
    with pytest.raises(ValueError,match='exact opening'):calculate(empty_records(),'legacy','2025-01-01','2026-01-01')


def test_benchmark_exact_dates_currency_demo_and_cached_provenance():
    stamp=lambda day:datetime.fromisoformat(day+'T21:00:00+00:00').timestamp()
    series=dict(currency='USD',timezone='America/New_York',points=[(stamp('2025-01-02'),100),(stamp('2026-01-02'),110)],source='Test provider',asOf='2026-01-02',stale=True)
    result=benchmark_return(series,'2025-01-02','2026-01-02')
    assert result['returnRate']==Decimal('.1') and result['cached']
    assert 'not a total-return index' in result['definition']
    with pytest.raises(ValueError,match='both selected'):benchmark_return(series,'2025-01-01','2026-01-02')
    with pytest.raises(ValueError,match='demo'):benchmark_return(dict(series,demo=True),'2025-01-02','2026-01-02')
    with pytest.raises(ValueError,match='USD'):benchmark_return(dict(series,currency='EUR'),'2025-01-02','2026-01-02')


def test_combined_account_values_require_full_alignment_and_internal_transfers_net_out():
    state=initial_workspace()
    state['investmentAccounts'].append(dict(id='ira',name='IRA',kind='ira',currency='USD',owner='Me'))
    for account,opening,closing in [('legacy',100,55),('ira',100,165)]:
        state=save_record(state,account,'2025-01-01',opening,'Broker')
        state=save_record(state,account,'2026-01-01',closing,'Broker')
    result=calculate_all(state,'2025-01-01','2026-01-01')
    assert result['twr']==Decimal('.1') and result['mwr']==pytest.approx(.1)
    assert result['opening']==200 and result['closing']==220
    state=save_record(state,'legacy','2025-06-01',50,'Account transfer','withdrawal')
    state=save_record(state,'ira','2025-06-01',50,'Account transfer','contribution')
    with pytest.raises(ValueError,match='aligned valuations'):calculate_all(state,'2025-01-01','2026-01-01')
    state=save_record(state,'legacy','2025-06-01',50,'Broker')
    state=save_record(state,'ira','2025-06-01',150,'Broker')
    result=calculate_all(state,'2025-01-01','2026-01-01')
    assert result['netFlows']==0 and result['twr']==Decimal('.1')
