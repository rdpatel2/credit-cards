from copy import deepcopy
from decimal import Decimal
import pytest
from card_compass.investments import InvestmentStore, position, validate_workspace, simulate_sale
from card_compass.broker_import import (parse_trades, parse_positions, build_preview, commit_preview,
                                        new_account, TRADE_COLUMNS, POSITION_COLUMNS)
from card_compass.reports import write_csv


def csv_rows(tmp_path, rows, columns=TRADE_COLUMNS, name='trades.csv'):
    path=tmp_path/name
    write_csv(path,columns,rows)
    return parse_trades(path) if columns==TRADE_COLUMNS else parse_positions(path)


def store_with_accounts(tmp_path):
    store=InvestmentStore(tmp_path)
    accounts=[new_account('Broker taxable','brokerage','Me'),new_account('Retirement','ira','Me')]
    state=deepcopy(store.state)
    state['investmentAccounts'].extend(accounts)
    store.save(state)
    return store,accounts


def test_fifo_cannot_use_another_accounts_lots(tmp_path):
    store,accounts=store_with_accounts(tmp_path)
    a,b=[row['id'] for row in accounts]
    rows=csv_rows(tmp_path,[['A','1','2025-01-01','TEST','buy','10','100','1','USD'],
                           ['B','1','2025-01-01','TEST','buy','2','200','0','USD'],
                           ['B','2','2025-02-01','TEST','sell','1','300','0','USD']])
    preview=build_preview(store.state,rows,{'A':a,'B':b})
    result=position(preview['state']['trades']['TEST'])
    assert result['realizedGain']==100
    assert result['disposals'][0]['accountId']==b
    assert {row['accountId']:row['quantity'] for row in result['lots']}=={a:10,b:1}
    bad=deepcopy(preview['state']['trades']['TEST'])
    bad[-1]['quantity']='3'
    with pytest.raises(ValueError,match='exceeds'):position(bad)
    result=simulate_sale(preview['state']['trades']['TEST'],dict(accountId=a,date='2025-03-01',quantity=1,price=150,fees=0))
    assert result['longGain']==0 and result['shortGain']==Decimal('49.9')


def test_reimport_skips_identical_ids_but_conflicts_are_atomic(tmp_path):
    store,accounts=store_with_accounts(tmp_path)
    mapping={'A':accounts[0]['id']}
    rows=csv_rows(tmp_path,[['A','1','2025-01-01','TEST','buy','10','100','1','USD']])
    preview=build_preview(store.state,rows,mapping)
    assert not store.state['trades']
    commit_preview(store,preview)
    original=store.path.read_bytes()
    again=build_preview(store.state,rows,mapping)
    assert not again['added'] and len(again['skipped'])==1
    rows[0]['price']='101'
    with pytest.raises(ValueError,match='different values'):build_preview(store.state,rows,mapping)
    assert store.path.read_bytes()==original


def test_possible_duplicates_and_reconciliation_require_review(tmp_path):
    store,accounts=store_with_accounts(tmp_path)
    ident=accounts[0]['id']
    store.trade('TEST',dict(id='manual',accountId=ident,date='2025-01-01',side='buy',quantity=1,price=10,fees=0))
    rows=csv_rows(tmp_path,[['A','broker','2025-01-01','TEST','buy','1','10','0','USD']])
    snapshots=csv_rows(tmp_path,[['A','2025-01-02','TEST','1','USD']],POSITION_COLUMNS,'positions.csv')
    preview=build_preview(store.state,rows,{'A':ident},snapshots=snapshots)
    assert len(preview['possible'])==1 and preview['reconciliation'][0]['difference']==1
    with pytest.raises(ValueError,match='duplicate'):commit_preview(store,preview)
    with pytest.raises(ValueError,match='differences'):commit_preview(store,preview,True)
    commit_preview(store,preview,True,True)
    assert store.portfolio()['holdings']['TEST']['quantity']==2


def test_new_accounts_and_trades_commit_together_and_preview_detects_changes(tmp_path):
    store=InvestmentStore(tmp_path)
    account=new_account('New IRA','ira')
    rows=csv_rows(tmp_path,[['A','1','2025-01-01','TEST','buy','1','10','0','USD']])
    preview=build_preview(store.state,rows,{'A':account['id']},[account])
    assert len(store.state['investmentAccounts'])==1
    state=deepcopy(store.state);state['notes']['A']='edited'
    store.save(state)
    with pytest.raises(ValueError,match='changed after preview'):commit_preview(store,preview)
    preview=build_preview(store.state,rows,{'A':account['id']},[account])
    commit_preview(store,preview)
    assert store.state['investmentAccounts'][-1]['kind']=='ira'


@pytest.mark.parametrize('row,message',[
    (['A','','2025-01-01','TEST','buy','1','10','0','USD'],'transaction_id'),
    (['A','1','2025-01-01','TEST','dividend','1','10','0','USD'],'buy or sell'),
    (['A','1','2025-01-01','TEST','buy','nan','10','0','USD'],'finite'),
    (['A','1','2025-01-01','TEST','buy','1','10','0','EUR'],'USD'),
    (['A','1','2025-01-01','TEST','sell','1','10','11','USD'],'fees'),
])
def test_bad_csv_never_reaches_preview(tmp_path,row,message):
    with pytest.raises(ValueError,match=message):csv_rows(tmp_path,[row])


def test_missing_basis_and_duplicate_ids_are_rejected(tmp_path):
    store,accounts=store_with_accounts(tmp_path)
    row=['A','1','2025-01-01','TEST','sell','1','10','0','USD']
    with pytest.raises(ValueError,match='Repeated'):csv_rows(tmp_path,[row,row])
    rows=csv_rows(tmp_path,[row])
    with pytest.raises(ValueError,match='exceeds'):build_preview(store.state,rows,{'A':accounts[0]['id']})
    assert not store.state['trades']


def test_legacy_migration_and_invalid_account_references(tmp_path):
    store=InvestmentStore(tmp_path)
    old=deepcopy(store.state);del old['investmentAccounts']
    old['trades']['TEST']=[dict(id='a',side='buy',date='2025-01-01',quantity=1,price=10,fees=0)]
    old['viewSettings']={'order':{'trades':[5,4,3,2,1,0]},'columns':{'trades':list(range(6))}}
    migrated=validate_workspace(old)
    assert migrated['investmentAccounts'][0]['id']=='legacy'
    assert migrated['viewSettings']['order']['trades']==[5,4,3,2,1,0,6]
    assert position(migrated['trades']['TEST'])['lots'][0]['accountId']=='legacy'
    bad=deepcopy(migrated);bad['trades']['TEST'][0]['accountId']='missing'
    with pytest.raises(ValueError,match='missing investment'):validate_workspace(bad)
