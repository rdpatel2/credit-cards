from copy import deepcopy
from datetime import datetime,timezone,timedelta,date
import csv
import json
from pathlib import Path
from types import SimpleNamespace
import uuid
import pytest
from card_compass.automations import (AutomationStore,execute,latest_slot,validate_job,
    validate_table,pause_restored_jobs,reviewed_directory,report_range)
from card_compass.finance import FinanceStore
from card_compass.planning import PlanningStore
from card_compass.investments import InvestmentStore


def job(now,folder,**changes):
    result=dict(version=1,id=uuid.uuid4().hex,name='Reviewed local job',kind='report',enabled=True,
        activatedAt=(now-timedelta(days=60)).isoformat(),frequency='daily',hour=12,minute=0,
        weekday=0,monthDay=1,threshold=100,maxAgeDays=30,dataset='budgets',range='previous_month',directory=str(folder),target='')
    result.update(changes);return result


@pytest.fixture
def owner(tmp_path):
    directory=tmp_path/'profile'
    return SimpleNamespace(directory=directory,finance_store=FinanceStore(directory),
        planning_store=PlanningStore(directory),investment_store=InvestmentStore(directory))


def test_slots_activation_utc_monthly_catchup_and_one_claim(owner,tmp_path):
    now=datetime(2026,10,11,13,tzinfo=timezone.utc)
    rule=job(now,tmp_path)
    assert latest_slot(rule,now)==datetime(2026,10,11,12,tzinfo=timezone.utc)
    assert latest_slot(dict(rule,activatedAt=now.isoformat()),now) is None
    assert latest_slot(dict(rule,frequency='monthly',monthDay=15),now).date()==date(2026,9,15)
    assert latest_slot(dict(rule,frequency='weekly',weekday=0),now).date()==date(2026,10,5)
    store=AutomationStore(owner.directory);store.save(rule)
    first=execute(owner,store,rule,now)
    assert first and execute(owner,store,rule,now+timedelta(minutes=30)) is None
    run=store.history()[0]
    assert run['status']=='completed' and Path(run['path']).exists()
    assert next(csv.reader(Path(run['path']).read_text(encoding='utf-8-sig').splitlines()))[0]=='schema_version'
    assert execute(owner,store,dict(rule,enabled=False),now+timedelta(days=1)) is None


def test_report_ranges_private_folder_missing_folder_and_failure_is_not_retried(owner,tmp_path):
    now=datetime.now(timezone.utc)
    assert report_range('previous_month',date(2024,3,5))==('2024-02-01','2024-02-29')
    assert report_range('trailing_30',date(2026,1,2))==('2025-12-04','2026-01-02')
    with pytest.raises(ValueError,match='outside'):reviewed_directory(owner.directory,owner.directory)
    rule=job(now,tmp_path/'absent');store=AutomationStore(owner.directory)
    execute(owner,store,rule,now,manual=True)
    assert store.history()[0]['status']=='failed' and not (tmp_path/'absent').exists()
    rule['activatedAt']=(now-timedelta(days=2)).isoformat();rule['hour']=0
    execute(owner,store,rule,now)
    assert execute(owner,store,rule,now) is None


def test_rules_continuous_latch_failed_check_does_not_rearm_and_recovery_rearms(owner,tmp_path):
    now=datetime.now(timezone.utc);month=now.astimezone().strftime('%Y-%m')
    owner.finance_store.set_target(month,'Food',100)
    ident=owner.finance_store.save_entry('Recorded food','expense','Food',120,month)
    owner.finance_store.post(ident,month,True)
    rule=job(now,tmp_path,kind='budget',target='Food');store=AutomationStore(owner.directory)
    execute(owner,store,rule,now,manual=True);assert store.history()[0]['status']=='triggered'
    execute(owner,store,rule,now,manual=True);assert store.history()[0]['status']=='completed'
    execute(owner,store,dict(rule,target='Missing'),now,manual=True);assert store.history()[0]['status']=='failed'
    execute(owner,store,rule,now,manual=True);assert store.history()[0]['status']=='completed'
    owner.finance_store.post(ident,month,False)
    execute(owner,store,rule,now,manual=True);assert store.history()[0]['status']=='no_match'
    owner.finance_store.post(ident,month,True)
    execute(owner,store,rule,now,manual=True);assert store.history()[0]['status']=='triggered'


def test_goal_stale_progress_and_balance_reminders_are_recorded_conditions(owner,tmp_path):
    now=datetime.now(timezone.utc);today=now.astimezone().date();old=(today-timedelta(days=31)).isoformat()
    account=owner.planning_store.save_account('Savings','savings','USD','Me',500,old)
    goal_id=owner.planning_store.save_goal('Buffer','emergency','USD',1000,today.isoformat(),saved=500,as_of=old)
    store=AutomationStore(owner.directory)
    rule=job(now,tmp_path,kind='stale_balance',target=str(account))
    execute(owner,store,rule,now,manual=True);assert store.history()[0]['status']=='triggered'
    rule=job(now,tmp_path,kind='goal',target=str(goal_id),threshold=50)
    execute(owner,store,rule,now,manual=True);assert store.history()[0]['status']=='failed'
    owner.planning_store.save_goal('Buffer','emergency','USD',1000,today.isoformat(),saved=500,as_of=today.isoformat(),ident=goal_id)
    execute(owner,store,rule,now,manual=True);assert store.history()[0]['status']=='triggered'


def test_interrupted_run_restore_consent_schema_and_atomic_capacity(owner,tmp_path):
    now=datetime.now(timezone.utc);rule=job(now,tmp_path);store=AutomationStore(owner.directory);store.save(rule)
    store.claim(rule,latest_slot(rule,now),now)
    store.recover_interrupted(now);assert store.history()[0]['status']=='interrupted'
    assert execute(owner,store,rule,now) is None
    with store.connection() as db:
        validate_table(db);pause_restored_jobs(db)
    assert not store.jobs()[0]['enabled']
    with store.connection() as db:db.execute('UPDATE automation_jobs SET settings=?',(json.dumps(dict(rule,hour=25)),))
    with store.connection() as db:
        with pytest.raises(ValueError):validate_table(db)
