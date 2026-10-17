from datetime import date
from copy import deepcopy
import pytest
from card_compass.finance import FinanceStore
from card_compass.cash_forecast import CashForecastStore,defaults,forecast
from card_compass.backup import create_backup,read_backup,restore_backup
from card_compass.catalog import default_profile,write_json


TODAY=date.today()
MONTH=TODAY.strftime('%Y-%m')


def test_current_cash_does_not_count_received_income_or_paid_bills_twice(tmp_path):
    finance=FinanceStore(tmp_path)
    income=finance.save_entry('Income','income','salary',3000,MONTH,True)
    rent=finance.save_entry('Rent','expense','rent',1000,MONTH,True)
    finance.post(income,MONTH,True);finance.post(rent,MONTH,True)
    finance.set_recurring_target(MONTH,'groceries',500)
    settings=defaults();settings.update(openingCash=100000,months=2)
    result=forecast(finance,settings)
    current,next_month=result['scenarios'][0]['rows']
    assert current['remainingReceipts']==0 and current['remainingOutflow']==50000 and current['closing']==50000
    assert next_month['remainingReceipts']==300000 and next_month['remainingOutflow']==150000 and next_month['closing']==200000


def test_variable_income_ranges_month_overrides_and_protected_reserve(tmp_path):
    finance=FinanceStore(tmp_path)
    finance.set_recurring_target(MONTH,'rent',1000)
    settings=defaults();settings.update(openingCash=150000,reserve=100000,incomeMode='range',incomeLow=0,incomeTypical=50000,incomeHigh=150000,months=2)
    result=forecast(finance,settings)
    assert [row['rows'][0]['closing'] for row in result['scenarios']]==[50000,100000,200000]
    assert result['scenarios'][0]['firstBelowReserve']==MONTH
    settings['overrides'][MONTH]=[200000,300000,400000]
    result=forecast(finance,settings)
    assert result['scenarios'][0]['rows'][0]['source']=='Income override'
    assert result['scenarios'][0]['rows'][0]['closing']==250000


def test_pending_expenses_survive_overbudget_actuals_and_savings_scope(tmp_path):
    finance=FinanceStore(tmp_path)
    paid=finance.save_entry('Paid spending','expense','groceries',700,MONTH)
    finance.post(paid,MONTH,True)
    finance.save_entry('Still due','expense','groceries',100,MONTH)
    finance.set_target(MONTH,'groceries',500)
    finance.save_entry('Transfer to counted savings','expense','savings',200,MONTH)
    settings=defaults();settings.update(openingCash=100000,months=1)
    # Scheduled groceries total 800, already paid 700 => 100 still due, even above target.
    assert forecast(finance,settings)['scenarios'][0]['rows'][0]['remainingOutflow']==30000
    settings['savingsConsumeCash']=False
    assert forecast(finance,settings)['scenarios'][0]['rows'][0]['remainingOutflow']==10000


def test_stale_cash_invalid_ranges_missing_plans_and_atomic_settings(tmp_path):
    finance=FinanceStore(tmp_path);store=CashForecastStore(tmp_path)
    settings=defaults();store.save(settings)
    bad=deepcopy(settings);bad['incomeLow']=100
    with pytest.raises(ValueError,match='ranges'):store.save(bad)
    assert store.load()==settings
    bad=deepcopy(settings);bad['cashAsOf']='2020-01-01'
    with pytest.raises(ValueError,match='Update current cash'):forecast(finance,bad)
    result=forecast(finance,settings)
    assert result['scenarios'][0]['warnings'] and result['scenarios'][0]['firstShortfall'] is None
    assert 'not probabilities or guarantees' in result['assumptions']


def test_forecast_assumptions_backup_roundtrip_and_corruption_rejection(tmp_path):
    directory=tmp_path/'profile'
    directory.mkdir()
    finance=FinanceStore(directory)
    write_json(directory/'profile.json',default_profile())
    store=CashForecastStore(directory)
    settings=defaults();settings.update(openingCash=120000,source='Reviewed checking')
    store.save(settings)
    archive=tmp_path/'backup.zip';create_backup(directory,archive)
    restored=tmp_path/'restored';restored.mkdir()
    restore_backup(restored,archive)
    assert CashForecastStore(restored).load()==settings
    with store.connection() as db:
        db.execute("UPDATE cash_forecast_settings SET settings='{}'")
    create_backup(directory,archive)
    with pytest.raises(ValueError,match='settings version'):read_backup(archive)


def test_legacy_forecast_table_absence_and_prorated_buffer(tmp_path):
    from card_compass.cash_forecast import validate_table
    finance=FinanceStore(tmp_path)
    with finance.connection() as db:validate_table(db)  # Legacy archives need no forecast table.
    settings=defaults();settings.update(months=2,expenseBuffer=31000)
    result=forecast(finance,settings)
    import calendar
    days=calendar.monthrange(TODAY.year,TODAY.month)[1]
    assert result['scenarios'][0]['rows'][0]['expenseBuffer']==round(31000*(days-TODAY.day+1)/days)
    assert result['scenarios'][0]['rows'][1]['expenseBuffer']==31000
