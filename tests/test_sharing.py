from copy import deepcopy
from datetime import date
import pytest
from card_compass.sharing import validate_snapshot,investment_snapshot,manual_snapshot,goal_snapshot
from card_compass.investments import initial_workspace
from card_compass.planning import PlanningStore
from card_compass.performance import save_record


def test_selected_manual_goal_fields_exclude_private_links_and_invalid_money(tmp_path):
    store=PlanningStore(tmp_path);today=date.today().isoformat()
    store.save_account('Private checking','checking','USD','Me',100,today)
    store.save_goal('Savings','savings','USD',200,today,saved=100)
    account=manual_snapshot(store.accounts()[0]);goal=goal_snapshot(store.goals()[0])
    assert 'account_id' not in goal and 'id' not in account
    with pytest.raises(ValueError):validate_snapshot('manual_account',dict(account,amountCents=-1))
    with pytest.raises(ValueError):validate_snapshot('manual_account',dict(account,credentials='must never be uploaded'))
    with pytest.raises(ValueError):validate_snapshot('goal',dict(goal,accountId=1))


def test_investment_snapshot_contains_one_selected_account_and_no_notes_or_tax_inputs():
    state=initial_workspace();state['investmentAccounts'].append(dict(id='other',name='Other private account',kind='roth',currency='USD',owner='Partner'))
    state=save_record(state,'legacy','2026-01-02',100,'Selected synthetic statement')
    state=save_record(state,'other','2026-01-02',200,'Private statement')
    snapshot=investment_snapshot(state,'legacy')
    assert len(snapshot['workspace']['performanceRecords']['valuations'])==1
    assert snapshot['workspace']['performanceRecords']['valuations'][0]['accountId']=='shared'
    assert 'Private statement' not in str(snapshot) and 'taxProfile' not in snapshot['workspace']
    changed=deepcopy(snapshot);changed['workspace']['notes']='Private journal'
    with pytest.raises(ValueError):validate_snapshot('investment_account',changed)
