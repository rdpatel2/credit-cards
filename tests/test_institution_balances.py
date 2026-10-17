from copy import deepcopy
from datetime import date,datetime,timezone,timedelta
import sqlite3
import pytest
from card_compass.planning import PlanningStore
from card_compass.institution_balances import InstitutionBalances,account_value,validate_tables
from card_compass.shared_sync import PlanningCopies
from tests.test_shared_sync import record,account_payload

ORIGIN='https://service.example'


def snapshot(amount=123.45,environment='production',updated='',**changes):
    row=dict(account_id='account-test',name='Provider checking',type='depository',subtype='checking',mask='1234',balances=dict(current=amount,available=99,iso_currency_code='USD',last_updated_datetime=updated))
    result=dict(id='connection-test',environment=environment,status='ready',lastSuccess=datetime.now(timezone.utc).isoformat(),balanceRequestMode='provider_recorded',accounts=[row],transactions=[])
    result.update(changes);return result


def preview(model,source,target=None,name='Provider checking',kind='checking',day=None,identity='alice'):
    return model.preview(ORIGIN,identity,source,'account-test',day or date.today().isoformat(),name,'Reviewed owner',kind,target)


def test_reviewed_balance_idempotent_dated_source_manual_edits_and_detach(tmp_path):
    store=PlanningStore(tmp_path);model=InstitutionBalances(store);source=snapshot();review=preview(model,source)
    with pytest.raises(ValueError,match='Review'):model.commit(review,source)
    ident=model.commit(review,source,reviewed=True)['localId'];row=store.accounts()[0]
    assert row['amount_cents']==12345 and row['source_info']['source_type']=='institution_balance'
    assert row['source_info']['date_basis']=='user_reviewed_unknown_provider_date' and row['source_info']['retrieved_at']==source['lastSuccess']
    assert model.commit(preview(model,source),source,reviewed=True)['status']=='unchanged'
    store.record_balance(ident,50,date.today().isoformat());assert store.accounts()[0]['source_info'] is None
    changed=preview(model,source)
    with pytest.raises(ValueError,match='updating'):model.commit(changed,source,reviewed=True)
    model.commit(changed,source,reviewed=True,replace_local=True)
    model.detach(ORIGIN,'alice','production','connection-test','account-test')
    assert store.accounts()[0]['source_info'] is not None
    store.delete_account(ident)
    with store.connection() as db:assert not db.execute('SELECT 1 FROM institution_balance_sources').fetchone()


def test_provider_timestamp_kept_and_apr_history_preserved(tmp_path):
    store=PlanningStore(tmp_path);model=InstitutionBalances(store)
    earlier=(date.today()-timedelta(days=40)).isoformat();day=(date.today()-timedelta(days=10)).isoformat()
    ident=store.save_account('Local mortgage','mortgage','USD','Me',500,earlier,apr=6.5,minimum_payment=25)
    source=snapshot(400,updated=day+'T12:00:00+00:00');source['accounts'][0].update(type='loan',subtype='mortgage')
    with pytest.raises(ValueError,match='reported balance date'):preview(model,source,ident,kind='mortgage')
    model.commit(preview(model,source,ident,name='Reviewed mortgage',kind='mortgage',day=day),source,reviewed=True,replace_local=True)
    row=store.accounts()[0];assert row['apr_bps']==650 and row['minimum_payment_cents']==2500
    with store.connection() as db:
        assert [r['amount_cents'] for r in db.execute('SELECT * FROM balance_snapshots ORDER BY date')]==[50000,40000]
        validate_tables(db)
    assert row['source_info']['balance_updated_at']==day+'T12:00:00+00:00'


def test_unknown_missing_negative_currency_duplicate_provider_and_attention_rejected(tmp_path):
    store=PlanningStore(tmp_path);model=InstitutionBalances(store)
    for amount in (None,True,-1,float('nan'),float('inf')):
        with pytest.raises(ValueError):preview(model,snapshot(amount))
    for currency in (None,'JPY','BTC'):
        source=snapshot();source['accounts'][0]['balances']['iso_currency_code']=currency
        with pytest.raises(ValueError,match='currency'):preview(model,source)
    source=snapshot();source['accounts'].append(deepcopy(source['accounts'][0]))
    with pytest.raises(ValueError,match='duplicated'):preview(model,source)
    with pytest.raises(ValueError,match='healthy'):preview(model,snapshot(status='attention'))
    source=snapshot();source['accounts'][0]['type']='other'
    with pytest.raises(ValueError,match='classification'):preview(model,source)
    assert not store.accounts()


def test_duplicate_name_currency_side_sandbox_and_after_review_conflicts(tmp_path):
    store=PlanningStore(tmp_path);model=InstitutionBalances(store)
    ident=store.save_account('Provider checking','checking','USD','Me',10,date.today().isoformat())
    source=snapshot(environment='sandbox');review=preview(model,source)
    with pytest.raises(ValueError,match='sandbox'):model.commit(review,source,reviewed=True,duplicates_reviewed=True)
    with pytest.raises(ValueError,match='same-name'):model.commit(review,source,reviewed=True,sandbox_reviewed=True)
    review=preview(model,source,ident)
    store.record_balance(ident,20,date.today().isoformat())
    with pytest.raises(ValueError,match='changed since preview'):model.commit(review,source,reviewed=True,replace_local=True,sandbox_reviewed=True)
    review=preview(model,source,ident);changed=deepcopy(source);changed['accounts'][0]['balances']['current']=999
    with pytest.raises(ValueError,match='after review'):model.commit(review,changed,reviewed=True,replace_local=True,sandbox_reviewed=True)
    assert store.accounts()[0]['amount_cents']==2000
    source['accounts'][0]['balances']['iso_currency_code']='EUR'
    with pytest.raises(ValueError,match='currency'):preview(model,source,ident)


def test_source_mapping_identity_conflict_backup_corruption_and_transaction_rollback(tmp_path):
    store=PlanningStore(tmp_path);model=InstitutionBalances(store);source=snapshot()
    ident=model.commit(preview(model,source),source,reviewed=True)['localId']
    with pytest.raises(ValueError,match='another institution'):preview(model,source,ident,identity='bob')
    with pytest.raises(ValueError,match='institution'):PlanningCopies(store).preview(ORIGIN,'alice',record(account_payload()),ident)
    with store.connection() as db:
        validate_tables(db)
        db.execute("UPDATE institution_balance_sources SET amount_cents=7")
        with pytest.raises(ValueError,match='dated balance'):validate_tables(db)
        db.execute('UPDATE institution_balance_sources SET amount_cents=12345')
        db.execute("CREATE TRIGGER controlled_failure BEFORE INSERT ON institution_balance_sources BEGIN SELECT RAISE(ABORT,'Controlled disk failure'); END")
    changed=snapshot(200)
    with pytest.raises(ValueError,match='Nothing was committed'):model.commit(preview(model,changed,ident),changed,reviewed=True,replace_local=True)
    assert store.accounts()[0]['amount_cents']==12345
    with store.connection() as db:validate_tables(db)


def test_selected_projection_excludes_other_accounts_private_extras_and_older_source(tmp_path):
    store=PlanningStore(tmp_path);model=InstitutionBalances(store);source=snapshot()
    source['accounts'][0]['unexpected_private_note']='Not persisted';source['accounts'].append(dict(source['accounts'][0],account_id='other-private',name='Other private account'))
    review=preview(model,source);assert 'Not persisted' not in review._json and 'Other private account' not in review._json
    ident=model.commit(review,source,reviewed=True)['localId']
    assert b'Not persisted' not in store.path.read_bytes()
    older=deepcopy(source);older['lastSuccess']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    with pytest.raises(ValueError,match='older'):preview(model,older,ident,day=(date.today()-timedelta(days=1)).isoformat())
    changed=deepcopy(source);changed['accounts'][0]['balances']['current']=99
    with pytest.raises(ValueError,match='without a new'):preview(model,changed,ident)
    unknown=snapshot();unknown['accounts'][0].update(type='investment',subtype='unknown-retirement')
    assert account_value(unknown['accounts'][0])['suggested_kind']=='other_asset'


def test_asset_goal_links_survive_balance_import_and_source_clears_on_manual_save(tmp_path):
    store=PlanningStore(tmp_path);model=InstitutionBalances(store);source=snapshot()
    ident=store.save_account('My checking','checking','USD','Me',50,date.today().isoformat())
    goal=store.save_goal('Private plan','savings','USD',1000,(date.today()+timedelta(days=365)).isoformat(),saved=20,account_id=ident,category='Private category')
    model.commit(preview(model,source,ident),source,reviewed=True,replace_local=True)
    row=store.goals()[0];assert row['id']==goal and row['account_id']==ident and row['category']=='Private category' and row['saved_cents']==2000
    store.save_account('Renamed checking','checking','USD','Me',75,date.today().isoformat(),ident=ident)
    assert store.accounts()[0]['source_info'] is None
    with store.connection() as db:validate_tables(db)


def test_detached_institution_can_be_replaced_by_shared_source_without_conflicting_provenance(tmp_path):
    store=PlanningStore(tmp_path);model=InstitutionBalances(store);source=snapshot()
    ident=model.commit(preview(model,source),source,reviewed=True)['localId'];model.detach(ORIGIN,'alice','production','connection-test','account-test')
    payload=account_payload();payload.update(name='Shared reviewed account',asOf=date.today().isoformat(),amountCents=12345,source='S'*120)
    shared=record(payload);copies=PlanningCopies(store)
    copies.commit(copies.preview(ORIGIN,'alice',shared,ident),reviewed=True,replace_local=True)
    assert store.accounts()[0]['source_info']['remote_id']==shared['id'] and len(store.accounts()[0]['source'])<=120
    with store.connection() as db:
        validate_tables(db)
        assert not db.execute('SELECT 1 FROM institution_balance_sources').fetchone()


def test_new_provider_debt_requires_explicit_user_terms_instead_of_inventing_zero_apr(tmp_path):
    store=PlanningStore(tmp_path);model=InstitutionBalances(store);source=snapshot(500);source['accounts'][0].update(type='credit',subtype='credit card')
    with pytest.raises(ValueError,match='reviewed APR'):preview(model,source,kind='credit_card')
    args=(ORIGIN,'alice',source,'account-test',date.today().isoformat(),'Reviewed credit card','Me','credit_card')
    review=model.preview(*args,apr='19.99',minimum_payment='25')
    model.commit(review,source,reviewed=True)
    row=store.accounts()[0];assert row['apr_bps']==1999 and row['minimum_payment_cents']==2500 and row['amount_cents']==50000
