from copy import deepcopy
from datetime import date
import pytest
from card_compass.finance import FinanceStore, shift_month
from card_compass.recurring import RecurringStore, detect, next_month_day
from card_compass.backup import create_backup, read_backup, restore_backup
from card_compass.catalog import default_profile, write_json


def history(today=None, account='Test:0000', amounts=(1000,1000,1050)):
    today=today or date.today()
    return [dict(id=i+1, account_key=account, merchant_key='STREAM', merchant_group='STREAM',
        merchant_name='Streaming service', date=shift_month(today.strftime('%Y-%m'),i-2)+'-01',
        amount_cents=amount, kind='purchase', category='subscriptions',one_off=0)
        for i,amount in enumerate(amounts)]


def seed(store):
    rows=history()
    with store.connection() as db:
        db.execute('INSERT INTO accounts VALUES (?,?,?,?)',('Test:0000','Test','0000','test-card'))
        sid=db.execute('INSERT INTO statements(source_hash,filename,account_key,closing_date) VALUES (?,?,?,?)',('synthetic','test.csv','Test:0000',date.today().isoformat())).lastrowid
        for row in rows:
            db.execute('''INSERT INTO transactions(statement_id,account_key,fingerprint,occurrence,date,posting_date,description,merchant_key,amount_cents,kind,reference,issuer_category,page,category)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(sid,row['account_key'],'synthetic-'+str(row['id']),1,row['date'],row['date'],'Streaming service','STREAM',row['amount_cents'],'purchase','','',1,'subscriptions'))
    return rows


def test_monthly_pattern_includes_source_history_price_change_and_refunds():
    rows=history()
    rows.append(dict(rows[-1],id=4,kind='refund',amount_cents=-200))
    candidate=detect(rows)[0]
    assert len(candidate['history'])==3 and len(candidate['refunds'])==1
    assert candidate['amount_cents']==1050 and candidate['typical_cents']==1000
    assert any('differs' in warning for warning in candidate['warnings'])
    assert any('refunds' in warning for warning in candidate['warnings'])
    assert candidate['estimated_next']==next_month_day(rows[-2]['date'])


def test_conservative_no_cross_account_duplicate_month_gaps_variable_amount_or_one_off():
    rows=history()
    rows[-1]['account_key']='Another:1111'
    assert not detect(rows)
    rows=history();rows.append(dict(rows[-1],id=4))
    assert not detect(rows)
    rows=history();rows[1]['date']=shift_month(rows[1]['date'][:7],-3)+'-01'
    assert not detect(rows)
    assert not detect(history(amounts=(1000,1000,2000)))
    rows=history();rows[1]['one_off']=1
    assert not detect(rows)
    assert not detect(history(today=date(2025,1,1)))


def test_month_end_dates_and_date_tolerance():
    rows=history(today=date(2026,3,31))
    for row,day in zip(rows,('2026-01-31','2026-02-28','2026-03-31')):row['date']=day
    candidate=detect(rows,today=date(2026,3,31))[0]
    assert candidate['estimated_next']=='2026-04-30'
    rows[1]['date']='2026-02-20'
    assert not detect(rows,today=date(2026,3,31))


def test_confirm_dismiss_and_immutable_preview_never_change_financial_records(tmp_path):
    finance=FinanceStore(tmp_path);store=RecurringStore(tmp_path);seed(store)
    before=deepcopy(finance.transactions())
    candidate=store.scan()[0]
    record=store.review(candidate,'confirmed')
    assert record['status']=='confirmed' and RecurringStore(tmp_path).reviews()==[record]
    assert finance.transactions()==before and not finance.entries(date.today().strftime('%Y-%m'))
    with store.connection() as db:db.execute('UPDATE transactions SET amount_cents=1060 WHERE id=3')
    with pytest.raises(ValueError,match='evidence changed'):store.review(candidate,'confirmed')
    assert store.reviews()==[record]
    fresh=store.scan()[0];store.review(fresh,'dismissed')
    assert store.reviews()[0]['status']=='dismissed'
    store.remove_review(candidate['id']);assert not store.reviews()


def test_backup_snapshot_survives_missing_import_and_rejects_corruption(tmp_path):
    root=tmp_path/'profile';finance=FinanceStore(root);store=RecurringStore(root);seed(store)
    write_json(root/'profile.json',default_profile())
    record=store.review(store.scan()[0],'confirmed')
    with store.connection() as db:db.execute('DELETE FROM transactions')
    assert not store.scan() and store.reviews()==[record]
    archive=tmp_path/'backup.zip';create_backup(root,archive)
    restored=tmp_path/'restored';restored.mkdir();restore_backup(restored,archive)
    assert RecurringStore(restored).reviews()==[record]
    with store.connection() as db:db.execute("UPDATE recurring_reviews SET record='{}'")
    create_backup(root,archive)
    with pytest.raises(ValueError,match='recurring-charge review'):read_backup(archive)


def test_aliases_combine_known_merchant_names_within_one_account(tmp_path):
    store=RecurringStore(tmp_path);seed(store)
    with store.connection() as db:
        db.execute("UPDATE transactions SET merchant_key='STREAM BILLING' WHERE id=2")
        store.save_rule(db,'STREAM','Streaming service','subscriptions','global')
        store.save_rule(db,'STREAM BILLING','Streaming service','subscriptions','global')
    assert store.scan()[0]['name']=='Streaming service'
