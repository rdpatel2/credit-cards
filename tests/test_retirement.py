from copy import deepcopy
from datetime import date,timedelta
import pytest
from card_compass.retirement import defaults,project,RetirementStore,validate_table
from card_compass.finance import FinanceStore


def simple():
    settings=defaults()
    settings.update(currentAge=64,retireAge=65,endAge=68,openingCents=100000,
        annualContributionCents=10000,annualSpendingCents=60000,incomeStartAge=67,
        annualIncomeCents=20000,lowReturnBps=0,typicalReturnBps=0,highReturnBps=0,inflationBps=0)
    return settings


def test_accumulation_then_withdrawal_benefits_and_unfunded_spending():
    rows=project(simple())['scenarios'][1]['rows']
    assert rows[0]['contribution']==10000 and rows[0]['closing']==110000
    assert rows[1]['fundedWithdrawal']==60000 and rows[1]['closing']==50000
    assert rows[2]['fundedWithdrawal']==50000 and rows[2]['unfunded']==10000 and rows[2]['closing']==0
    assert rows[3]['income']==20000 and rows[3]['requiredWithdrawal']==40000 and rows[3]['unfunded']==40000
    scenario=project(simple())['scenarios'][1]
    assert scenario['firstUnfundedAge']==66 and scenario['totalUnfunded']==50000


def test_fee_after_nominal_growth_and_real_inflation_with_start_of_year_flows():
    settings=simple()
    settings.update(endAge=65+1,retireAge=65,lowReturnBps=1000,typicalReturnBps=1000,highReturnBps=1000,inflationBps=1000,feeBps=100)
    rows=project(settings)['scenarios'][1]['rows']
    assert rows[0]['gain']==11000 and rows[0]['fees']==1210 and rows[0]['closing']==119790
    assert rows[0]['realClosing']==108900
    assert rows[1]['spending']==66000  # Today's dollars increase by 10% in year two.
    assert rows[1]['requiredWithdrawal']==66000


def test_already_retired_zero_funds_outside_income_surplus_and_reserve_warning():
    settings=simple()
    settings.update(currentAge=70,retireAge=65,endAge=72,openingCents=0,annualIncomeCents=100000,
        incomeStartAge=67,reserveCents=10000)
    result=project(settings)
    first=result['scenarios'][1]['rows'][0]
    assert first['contribution']==0 and first['incomeSurplus']==40000 and first['closing']==40000
    assert result['scenarios'][1]['firstBelowReserveAge']==70
    assert result['scenarios'][1]['firstUnfundedAge'] is None
    assert any('Starting funds' in warning for warning in result['warnings'])


def test_loss_scenarios_and_ordered_returns_do_not_generate_negative_assets():
    settings=simple();settings.update(lowReturnBps=-9900,typicalReturnBps=0,highReturnBps=2000)
    result=project(settings)
    assert result['scenarios'][0]['firstUnfundedAge']==65
    assert all(row['closing']>=0 for scenario in result['scenarios'] for row in scenario['rows'])
    assert result['scenarios'][2]['finalRealCents']>=result['scenarios'][1]['finalRealCents']
    bad=deepcopy(settings);bad['highReturnBps']=-100
    with pytest.raises(ValueError,match='low ≤ typical'):project(bad)
    settings['asOf']=(date.today()-timedelta(days=1)).isoformat()
    with pytest.raises(ValueError,match='update the as-of'):project(settings)


def test_optional_settings_are_atomic_and_corrupt_records_fail_validation(tmp_path):
    finance=FinanceStore(tmp_path)
    with finance.connection() as db:validate_table(db)
    store=RetirementStore(tmp_path);settings=simple();store.save(settings)
    bad=deepcopy(settings);bad['endAge']=63
    with pytest.raises(ValueError):store.save(bad)
    assert store.load()==settings
    with store.connection() as db:db.execute("UPDATE retirement_settings SET settings='{}'")
    with store.connection() as db:
        with pytest.raises(ValueError,match='assumptions version'):validate_table(db)


def test_retirement_settings_backup_roundtrip_and_corrupt_restore_rejection(tmp_path):
    from card_compass.backup import create_backup,restore_backup,read_backup
    from card_compass.catalog import write_json,default_profile
    root=tmp_path/'profile';finance=FinanceStore(root);store=RetirementStore(root)
    write_json(root/'profile.json',default_profile())
    settings=simple();store.save(settings)
    path=tmp_path/'backup.zip';create_backup(root,path)
    target=tmp_path/'restored';target.mkdir();restore_backup(target,path)
    assert RetirementStore(target).load()==settings
    with store.connection() as db:db.execute("UPDATE retirement_settings SET settings='{}'")
    create_backup(root,path)
    with pytest.raises(ValueError,match='assumptions version'):read_backup(path)
