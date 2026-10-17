from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import csv
import pytest
from card_compass.planning import PlanningStore, debt_scenario
from card_compass.finance import FinanceStore
from card_compass.investments import InvestmentStore
from card_compass.catalog import default_profile, write_json
from card_compass.backup import create_backup, restore_backup, read_backup
from card_compass.onboarding import seed_demo
from card_compass.reports import DATASETS, rows_for, write_csv


def account(store,name='Savings',kind='savings',balance=1000,currency='USD',day='2025-01-01',**kwargs):
    return store.save_account(name,kind,currency,'Me',balance,day,**kwargs)


def test_currency_totals_snapshots_and_partial_history(tmp_path):
    store=PlanningStore(tmp_path)
    savings=account(store)
    debt=account(store,'Loan','loan',500,day='2025-01-02',apr=10,minimum_payment=50)
    account(store,'Euro checking','checking',900,currency='EUR')
    assert store.net_worth('2025-01-01')['USD']['net_worth']==100000
    assert store.net_worth('2025-01-01')['USD']['missing']==1
    assert store.net_worth('2025-01-02')['USD']['net_worth']==50000
    assert store.net_worth('2025-01-02')['EUR']['net_worth']==90000
    store.record_balance(savings,1400,'2025-02-01')
    store.record_balance(debt,400,'2025-02-02')
    assert store.net_worth('2025-02-02')['USD']['net_worth']==100000
    assert [row['net_worth'] for row in store.history()]==[100000,50000,90000,100000]
    store.record_balance(savings,1300,'2025-02-01')
    assert store.net_worth('2025-02-02')['USD']['net_worth']==90000
    reopened=PlanningStore(tmp_path)
    assert reopened.accounts()[0]['name'] in ('Euro checking','Loan','Savings')
    assert reopened.net_worth()['USD']['stale']==2


def test_bad_edit_rolls_back_and_preserves_history(tmp_path):
    store=PlanningStore(tmp_path)
    ident=account(store)
    for kind,currency in [('loan','USD'),('savings','EUR')]:
        with pytest.raises(ValueError):
            account(store,kind=kind,currency=currency,ident=ident,balance=5)
    assert store.net_worth()['USD']['assets']==100000
    with pytest.raises(ValueError):
        store.record_balance(ident,3,'2099-01-01')
    with pytest.raises(ValueError):
        account(store,apr='not-a-rate')
    with pytest.raises(ValueError):
        account(store,balance=float('inf'))


def test_goal_progress_and_overallocation_not_double_counted(tmp_path):
    store=PlanningStore(tmp_path)
    ident=account(store)
    first=store.save_goal('Buffer','emergency','USD',2000,'2027-01-01',saved=600,as_of='2025-01-01',account_id=ident)
    store.save_goal('Holiday','savings','USD',1000,'2027-01-01',saved=500,as_of='2025-01-01',account_id=ident)
    goals=store.goals('2025-01-02')
    assert goals[0]['progress']==.3
    assert all('exceed' in row['warning'] for row in goals)
    assert store.net_worth('2025-01-02')['USD']['assets']==100000
    store.save_goal('Buffer','emergency','USD',2000,'2027-01-01',saved=400,as_of='2025-02-01',account_id=ident,ident=first)
    assert store.goals('2025-01-02')[0]['saved_cents']==60000
    assert store.goals('2025-02-02')[0]['saved_cents']==40000
    store.delete_account(ident)
    assert all(row['account_id'] is None for row in store.goals())
    assert store.goals()[0]['saved_cents']==40000


def test_goal_currency_and_debt_links_rejected(tmp_path):
    store=PlanningStore(tmp_path)
    eur=account(store,currency='EUR')
    debt=account(store,'Debt','loan')
    for ident in (eur,debt):
        with pytest.raises(ValueError):
            store.save_goal('Bad goal','savings','USD',500,'2027-01-01',account_id=ident)
    assert not store.goals()


def test_payoff_math_payment_rollover_and_apr_comparison(tmp_path):
    store=PlanningStore(tmp_path)
    account(store,'Zero rate','loan',100,minimum_payment=10)
    result=debt_scenario(store.accounts())
    assert result['paid_off'] and result['months']==10
    assert result['paid_cents']==10000 and result['interest_cents']==0
    account(store,'High APR','credit_card',4000,apr=25,minimum_payment=50)
    avalanche=debt_scenario(store.accounts(),extra=100,strategy='avalanche')
    snowball=debt_scenario(store.accounts(),extra=100,strategy='snowball')
    assert avalanche['paid_off'] and snowball['paid_off']
    assert avalanche['interest_cents']<=snowball['interest_cents']
    assert all(row['payment_cents']==avalanche['budget_cents'] for row in avalanche['history'][:-1])
    assert avalanche['history'][-1]['remaining_cents']==0
    account(store,'Euro debt','loan',50,currency='EUR',minimum_payment=5)
    with pytest.raises(ValueError):
        debt_scenario(store.accounts())


def test_unpayable_debt_does_not_promise_payoff_or_overflow(tmp_path):
    store=PlanningStore(tmp_path)
    account(store,'Very large debt','loan',1_000_000_000,apr=100,minimum_payment=1)
    result=debt_scenario(store.accounts(),max_months=600)
    assert not result['paid_off'] and result['remaining_cents']>100_000_000_000
    assert 'Not repaid' in result['reason']


def test_backup_roundtrip_covers_accounts_history_goals_and_events(tmp_path):
    root=tmp_path/'profile'
    finance=FinanceStore(root)
    store=PlanningStore(root)
    ident=account(store)
    store.save_goal('Buffer','emergency','USD',2000,'2027-01-01',saved=400,account_id=ident)
    write_json(root/'profile.json',default_profile())
    InvestmentStore(root).save(InvestmentStore(root).state)
    archive=tmp_path/'all.zip'
    create_backup(root,archive)
    store.record_balance(ident,1,'2025-02-01')
    restore_backup(root,archive)
    reopened=PlanningStore(root)
    assert reopened.net_worth()['USD']['assets']==100000
    assert reopened.goals()[0]['saved_cents']==40000
    with reopened.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM planning_events').fetchone()[0]==2


def test_csv_versions_ranges_tax_lots_and_safe_text(tmp_path):
    finance=FinanceStore(tmp_path)
    planning=PlanningStore(tmp_path)
    investment=InvestmentStore(tmp_path)
    owner=SimpleNamespace(finance_store=finance,planning_store=planning,investment_store=investment)
    finance.save_entry('=malicious()','income','salary',500,'2025-01',True)
    account(planning,name=' @injected()')
    investment.state['trades']['TEST']=[
        dict(id='one',side='buy',date='2025-01-01',quantity=10,price=10,fees=1),
        dict(id='two',side='sell',date='2025-02-01',quantity=3,price=8,fees=1),
        dict(id='future',side='buy',date='2025-03-01',quantity=1,price=12,fees=0)]
    for dataset in DATASETS:
        headers,rows=rows_for(owner,dataset,'2025-02-01','2025-02-28')
        path=tmp_path/(dataset+'.csv')
        write_csv(path,headers,rows)
        with path.open(encoding='utf-8-sig',newline='') as handle:
            loaded=list(csv.reader(handle))
        assert loaded[0][0]=='schema_version'
        if dataset=='lots':
            assert Decimal(loaded[1][headers.index('remaining_shares')])==7
            assert Decimal(loaded[1][headers.index('remaining_basis_usd')])==Decimal('70.7')
            assert loaded[1][headers.index('account_id')]=='legacy'
        if dataset=='gains':
            gain=loaded[1][headers.index('gain_usd')]
            assert Decimal(gain)<0 and not gain.startswith("'")
        if dataset=='cash_flow':
            assert loaded[1][5]=="'=malicious()"
    assert not rows_for(owner,'trades','2025-04-01','2025-04-30')[1]


def test_invalid_planning_backup_rejected_before_restoring(tmp_path):
    root=tmp_path/'profile'
    FinanceStore(root)
    store=PlanningStore(root)
    ident=account(store)
    write_json(root/'profile.json',default_profile())
    with store.connection() as db:
        db.execute("UPDATE financial_accounts SET kind='unknown-asset' WHERE id=?",(ident,))
    archive=tmp_path/'invalid.zip'
    create_backup(root,archive)
    with pytest.raises(ValueError,match='invalid financial account'):
        read_backup(archive)
    with store.connection() as db:
        db.execute("UPDATE financial_accounts SET kind='savings' WHERE id=?",(ident,))
    with pytest.raises(ValueError):
        restore_backup(root,archive)
    assert store.accounts()[0]['kind']=='savings'


def test_demo_seed_requires_empty_directory_and_contains_synthetic_finances(tmp_path):
    seed_demo(tmp_path)
    assert FinanceStore(tmp_path).summary(date_month())['received']==300000
    assert PlanningStore(tmp_path).goals()[0]['saved_cents']==400000
    assert InvestmentStore(tmp_path).state['dataMode']=='demo'
    assert set(InvestmentStore(tmp_path).state['trades'])=={'SAMPLE'}
    with pytest.raises(ValueError):
        seed_demo(tmp_path)


def date_month():
    from datetime import date
    return date.today().strftime('%Y-%m')
