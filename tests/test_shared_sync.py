from copy import deepcopy
from datetime import date,datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest
from card_compass.shared_sync import (PlanningCopies,investment_preview,investment_commit,
    detach_investment,validate_tables)
from card_compass.planning import PlanningStore
from card_compass.finance import FinanceStore
from card_compass.investments import InvestmentStore,initial_workspace,validate_workspace
from card_compass.sharing import manual_snapshot,goal_snapshot,investment_snapshot,validate_snapshot
from card_compass.backup import create_backup,restore_backup,read_backup
from card_compass.catalog import write_json,default_profile
from card_compass.reports import rows_for

ORIGIN='https://service.example'
IDENTITY='reviewed_identity'


def record(payload,kind='manual_account',version=1,ident='reviewed_remote'):
    return dict(id=ident,creator='creator',householdId='household',kind=kind,version=version,updated=datetime.now(timezone.utc).isoformat(),payload=payload)


def account_payload():
    return dict(version=1,name='Reviewed savings',currency='USD',accountType='savings',owner='Partner',asOf='2026-01-01',amountCents=12345,aprBps=0,minimumPaymentCents=0,source='User-reviewed statement')


def investment_payload():
    state=initial_workspace();state['investmentAccounts'].append(dict(id='source',name='Reviewed investments',kind='brokerage',currency='USD',owner='Partner'))
    state['trades']['TEST']=[dict(id='source_buy',accountId='source',date='2025-01-01',side='buy',quantity=5,price=10,fees=1)]
    state['performanceRecords']['valuations']=[dict(accountId='source',date='2025-01-01',amount='51',source='Statement value')]
    state['performanceRecords']['flows']=[dict(id='source_flow',accountId='source',date='2025-01-01',kind='contribution',amount='51',source='Funding statement')]
    state['dividendEvents']=[dict(id='a'*32,accountId='source',symbol='TEST',paid='2025-02-01',currency='USD',grossCents=10,qualifiedCents=0,withheldCents=0,classification='unknown',source='Dividend statement',notes='',exDate='')]
    return investment_snapshot(state,'source')


def test_planning_copy_is_immutable_idempotent_atomic_and_keeps_source_date(tmp_path):
    store=PlanningStore(tmp_path);copies=PlanningCopies(store);remote=record(account_payload())
    preview=copies.preview(ORIGIN,IDENTITY,remote)
    preview.value['record']['payload']['amountCents']=999999
    with pytest.raises(ValueError):copies.commit(preview)
    result=copies.commit(preview,reviewed=True);ident=result['localId'];row=store.accounts()[0]
    assert row['id']==ident and row['amount_cents']==12345 and row['as_of']=='2026-01-01'
    assert row['source_info']['remote_version']==1 and row['source_info']['origin']==ORIGIN
    assert 'Service snapshot' in manual_snapshot(row)['source']
    assert copies.commit(copies.preview(ORIGIN,IDENTITY,remote),reviewed=True)['status']=='unchanged'
    assert len(store.accounts())==1
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM service_copies').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM planning_events').fetchone()[0]==1


def test_manual_edit_conflict_requires_review_and_cas_protects_after_preview(tmp_path):
    store=PlanningStore(tmp_path);copies=PlanningCopies(store);remote=record(account_payload());ident=copies.commit(copies.preview(ORIGIN,IDENTITY,remote),reviewed=True)['localId']
    store.record_balance(ident,200,'2026-02-01')
    update=record(dict(account_payload(),asOf='2026-02-01',amountCents=15000),version=2)
    preview=copies.preview(ORIGIN,IDENTITY,update)
    assert preview.value['conflict'] and not preview.value['unchanged']
    with pytest.raises(ValueError):copies.commit(preview,reviewed=True)
    store.record_balance(ident,300,'2026-02-01')
    with pytest.raises(ValueError,match='changed since preview'):copies.commit(preview,reviewed=True,replace_local=True)
    assert store.accounts()[0]['amount_cents']==30000
    copies.commit(copies.preview(ORIGIN,IDENTITY,update),reviewed=True,replace_local=True)
    assert store.accounts()[0]['amount_cents']==15000 and store.accounts()[0]['source_info']['remote_version']==2
    assert len(store.history())==2
    with pytest.raises(ValueError,match='older'):copies.preview(ORIGIN,IDENTITY,remote)
    with pytest.raises(ValueError,match='without changing'):copies.preview(ORIGIN,IDENTITY,record(dict(update['payload'],amountCents=1),version=2))


def test_goal_update_preserves_private_link_and_source_resets_on_manual_review(tmp_path):
    store=PlanningStore(tmp_path);account=store.save_account('Private linked account','savings','USD','Me',1000,'2026-01-01')
    goal=store.save_goal('Local goal','emergency','USD',2000,'2027-01-01',saved=100,as_of='2026-01-01',account_id=account,category='Private local category')
    copies=PlanningCopies(store);payload=dict(version=1,name='Shared emergency fund',currency='USD',goalType='emergency',asOf='2026-01-01',amountCents=20000,targetCents=300000,targetDate='2027-02-01',source='Reviewed allocation')
    remote=record(payload,kind='goal')
    copies.commit(copies.preview(ORIGIN,IDENTITY,remote,target=goal),reviewed=True,replace_local=True)
    row=store.goals()[0]
    assert row['account_id']==account and row['category']=='Private local category' and row['source_info']
    assert 'account_id' not in goal_snapshot(row) and 'Private local category' not in str(goal_snapshot(row))
    store.save_goal(row['name'],row['kind'],row['currency'],3000,row['target_date'],saved=200,as_of='2026-01-01',account_id=account,category=row['category'],ident=goal)
    assert store.goals()[0]['source_info'] is None
    store.delete_goal(goal)
    with store.connection() as db:validate_tables(db);assert not db.execute('SELECT 1 FROM service_copies').fetchone()


def test_duplicate_new_copy_mapping_isolation_currency_and_live_preview_duplicates(tmp_path):
    store=PlanningStore(tmp_path);copies=PlanningCopies(store);remote=record(account_payload())
    preview=copies.preview(ORIGIN,IDENTITY,remote)
    existing=store.save_account(remote['payload']['name'],'savings','USD','Me',200,'2026-01-01')
    with pytest.raises(ValueError,match='Same-name'):copies.commit(preview,reviewed=True)
    preview=copies.preview(ORIGIN,IDENTITY,remote)
    with pytest.raises(ValueError,match='same-name'):copies.commit(preview,reviewed=True)
    result=copies.commit(copies.preview(ORIGIN,IDENTITY,remote,target=existing),reviewed=True,replace_local=True)
    assert result['localId']==existing and len(store.accounts())==1
    with pytest.raises(ValueError,match='different service'):copies.preview(ORIGIN,'other_identity',remote,target=existing)
    with pytest.raises(ValueError,match='Currencies differ'):copies.preview(ORIGIN,IDENTITY,record(dict(account_payload(),currency='EUR'),version=2))
    copies.detach(ORIGIN,IDENTITY,remote['id']);assert store.accounts()[0]['source_info']
    with store.connection() as db:validate_tables(db)
    assert not copies.preview(ORIGIN,IDENTITY,remote).value['binding']


def test_planning_provenance_backup_reports_legacy_and_corruption(tmp_path):
    profile=tmp_path/'profile';finance=FinanceStore(profile);store=PlanningStore(profile);investment=InvestmentStore(profile);investment.save(investment.state)
    write_json(profile/'profile.json',default_profile());copies=PlanningCopies(store)
    ident=copies.commit(copies.preview(ORIGIN,IDENTITY,record(account_payload())),reviewed=True)['localId']
    owner=SimpleNamespace(finance_store=finance,planning_store=store,investment_store=investment)
    headers,rows=rows_for(owner,'balances','2026-01-01','2026-02-01');assert 'Service snapshot' in rows[0][headers.index('source')]
    archive=tmp_path/'copy.zip';create_backup(profile,archive);read_backup(archive)
    store.record_balance(ident,9,'2026-01-01');assert store.accounts()[0]['source_info'] is None
    restore_backup(profile,archive);assert PlanningStore(profile).accounts()[0]['source_info']['remote_version']==1
    with store.connection() as db:
        db.execute('UPDATE service_record_sources SET amount_cents=0')
        with pytest.raises(ValueError,match='provenance'):validate_tables(db)
    with store.connection() as db:
        db.execute('DROP TABLE service_record_sources');db.execute('DROP TABLE service_copies');validate_tables(db)


def test_planning_failure_rolls_back_values_binding_and_provenance(tmp_path,monkeypatch):
    store=PlanningStore(tmp_path);copies=PlanningCopies(store);preview=copies.preview(ORIGIN,IDENTITY,record(account_payload()))
    monkeypatch.setattr(store,'record',lambda *args:(_ for _ in ()).throw(OSError('Controlled event-write failure')))
    with pytest.raises(OSError):copies.commit(preview,reviewed=True)
    assert not store.accounts()
    with store.connection() as db:assert not db.execute('SELECT 1 FROM service_copies').fetchone() and not db.execute('SELECT 1 FROM service_record_sources').fetchone()


def test_investment_copy_remaps_ids_keeps_other_records_and_repeated_download_skips(tmp_path):
    store=InvestmentStore(tmp_path);store.state['notes']['TEST']='Private research note';remote=record(investment_payload(),kind='investment_account')
    result=investment_commit(store,investment_preview(store,ORIGIN,IDENTITY,remote),reviewed=True);ident=result['localId']
    assert store.state['notes']['TEST']=='Private research note' and ident!='shared'
    buy=store.state['trades']['TEST'][0];dividend=store.state['dividendEvents'][0];flow=store.state['performanceRecords']['flows'][0]
    assert buy['id']!='source_buy' and dividend['id']!='a'*32 and flow['id']!='source_flow'
    assert all(row['accountId']==ident for row in (buy,dividend,flow))
    preview=investment_preview(store,ORIGIN,IDENTITY,remote)
    assert investment_commit(store,preview,reviewed=True)['status']=='unchanged'
    assert len(store.state['investmentAccounts'])==2
    second=record(investment_payload(),kind='investment_account',ident='another_remote')
    result2=investment_commit(store,investment_preview(store,ORIGIN,IDENTITY,second),reviewed=True,duplicates_reviewed=True)
    assert result2['localId']!=ident and len({row['id'] for row in store.state['dividendEvents']})==2
    validate_workspace(store.state)


def test_investment_local_changes_and_disk_changes_stop_stale_full_replacement(tmp_path):
    store=InvestmentStore(tmp_path);remote=record(investment_payload(),kind='investment_account')
    ident=investment_commit(store,investment_preview(store,ORIGIN,IDENTITY,remote),reviewed=True)['localId']
    store.state['trades']['TEST'][0]['quantity']=6
    update=record(investment_payload(),kind='investment_account',version=2)
    preview=investment_preview(store,ORIGIN,IDENTITY,update);assert preview.value['conflict']
    with pytest.raises(ValueError,match='full replacement'):investment_commit(store,preview,reviewed=True)
    store.state['trades']['TEST'][0]['quantity']=7
    with pytest.raises(ValueError,match='changed since preview'):investment_commit(store,preview,reviewed=True,replace_local=True)
    preview=investment_preview(store,ORIGIN,IDENTITY,update)
    prior=store.path.read_bytes();store.path.write_bytes(prior+b' ')
    with pytest.raises(ValueError,match='changed since preview'):investment_commit(store,preview,reviewed=True,replace_local=True)
    store.path.write_bytes(prior)
    investment_commit(store,investment_preview(store,ORIGIN,IDENTITY,update),reviewed=True,replace_local=True)
    assert store.state['trades']['TEST'][0]['quantity']==5 and store.state['serviceCopies'][0]['remote_version']==2
    detach_investment(store,ORIGIN,IDENTITY,remote['id']);assert not store.state['serviceCopies'] and store.state['trades']['TEST']


def test_investment_failed_save_preserves_records_and_mapping(tmp_path,monkeypatch):
    store=InvestmentStore(tmp_path);store.save(store.state);before=deepcopy(store.state);disk=store.path.read_bytes()
    preview=investment_preview(store,ORIGIN,IDENTITY,record(investment_payload(),kind='investment_account'))
    monkeypatch.setattr('card_compass.investments.write_json',lambda *args:(_ for _ in ()).throw(OSError('Controlled save failure')))
    with pytest.raises(OSError):investment_commit(store,preview,reviewed=True)
    assert store.state==before and store.path.read_bytes()==disk


def test_investment_copy_metadata_rejects_credentials_and_outgoing_unknown_fields(tmp_path):
    store=InvestmentStore(tmp_path);remote=record(investment_payload(),kind='investment_account')
    investment_commit(store,investment_preview(store,ORIGIN,IDENTITY,remote),reviewed=True)
    changed=deepcopy(store.state);changed['serviceCopies'][0]['token']='Private credential'
    with pytest.raises(ValueError,match='extra fields'):validate_workspace(changed)
    store.state['trades']['TEST'][0]['privateApiKey']='Private credential'
    selected=investment_snapshot(store.state,store.state['serviceCopies'][0]['local_id'])
    assert 'Private credential' not in str(selected) and 'serviceCopies' not in selected['workspace']
    selected['workspace']['trades']['TEST'][0]['privateApiKey']='Private credential'
    with pytest.raises(ValueError,match='private fields'):validate_snapshot('investment_account',selected)
    selected=investment_payload();selected['workspace']['performanceRecords']['accessToken']='Private credential'
    with pytest.raises(ValueError,match='private performance'):validate_snapshot('investment_account',selected)
    selected=investment_payload();selected['name']='Misleading destination name'
    with pytest.raises(ValueError,match='snapshot name'):validate_snapshot('investment_account',selected)
