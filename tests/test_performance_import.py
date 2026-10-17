from copy import deepcopy
import pytest
from card_compass.investments import InvestmentStore
from card_compass.performance_import import parse_file,preview_import,commit_import
from card_compass.reports import write_csv,rows_for
from types import SimpleNamespace
from card_compass.finance import FinanceStore
from card_compass.planning import PlanningStore


def file_rows(tmp_path,flow=False,amount=100):
    path=tmp_path/'performance.csv'
    headers=['account_id','date','amount_usd','source']
    row=['source','2025-01-01',amount,'Broker statement']
    if flow:headers+=['kind','flow_id'];row+=['contribution','flow-001']
    write_csv(path,headers,[row]);return path


def test_mapped_import_preview_and_portable_export_roundtrip(tmp_path):
    store=InvestmentStore(tmp_path/'profile')
    rows=parse_file(file_rows(tmp_path))
    preview=preview_import(store.state,rows,{'source':'legacy'})
    assert not store.state['performanceRecords']['valuations']
    commit_import(store,preview)
    owner=SimpleNamespace(investment_store=store,finance_store=FinanceStore(tmp_path/'profile'),planning_store=PlanningStore(tmp_path/'profile'))
    headers,rows=rows_for(owner,'valuations','2025-01-01','2025-12-31')
    path=tmp_path/'export.csv';write_csv(path,headers,rows)
    again=preview_import(store.state,parse_file(path),{'legacy':'legacy'})
    assert not again['added'] and not again['changed'] and len(again['skipped'])==1


def test_corrections_require_acknowledgement_and_keep_others(tmp_path):
    store=InvestmentStore(tmp_path/'profile')
    preview=preview_import(store.state,parse_file(file_rows(tmp_path,True,100)),{'source':'legacy'})
    commit_import(store,preview)
    original=store.path.read_bytes()
    correction=preview_import(store.state,parse_file(file_rows(tmp_path,True,200)),{'source':'legacy'})
    with pytest.raises(ValueError,match='corrections'):commit_import(store,correction)
    assert store.path.read_bytes()==original
    commit_import(store,correction,True)
    assert store.state['performanceRecords']['flows'][0]['amount']=='200'
    assert len(store.state['performanceRecords']['flows'])==1


def test_bad_rows_mappings_and_stale_preview_never_save(tmp_path):
    store=InvestmentStore(tmp_path/'profile')
    rows=parse_file(file_rows(tmp_path))
    with pytest.raises(ValueError,match='Map every'):preview_import(store.state,rows,{'source':'missing'})
    with pytest.raises(ValueError,match='repeated record'):preview_import(store.state,[rows[0],rows[0]],{'source':'legacy'})
    preview=preview_import(store.state,rows,{'source':'legacy'})
    state=deepcopy(store.state);state['notes']['sample']='changed';store.save(state)
    with pytest.raises(ValueError,match='changed after'):commit_import(store,preview)
    with pytest.raises(ValueError,match='decimal places'):parse_file(file_rows(tmp_path,amount='1.001'))
