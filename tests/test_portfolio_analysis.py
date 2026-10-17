from copy import deepcopy
from datetime import date
from decimal import Decimal
import pytest
from card_compass.investments import initial_workspace,validate_workspace
from card_compass.portfolio_analysis import allocation


def state_with_positions():
    state=initial_workspace()
    state['investmentAccounts'].append(dict(id='ira',name='IRA',kind='ira',currency='USD',owner='Me'))
    state['trades']={'AAA':[dict(id='a',date='2025-01-01',side='buy',quantity=2,price=50,fees=0)],
                     'BBB':[dict(id='b',date='2025-01-01',side='buy',quantity=3,price=100,fees=0,accountId='ira')]}
    return state


def quotes(ticker):return dict(price=100,currency='USD',source='Test provider',asOf='2025-01-02T12:00:00Z')


def test_complete_market_weights_concentration_and_unclassified_share():
    state=state_with_positions()
    result=allocation(state,quotes)
    assert result['total']==500 and result['complete']
    assert [row['weight'] for row in result['rows']]==[Decimal('.4'),Decimal('.6')]
    assert result['hhi']==Decimal('.52') and result['largest']==Decimal('.6') and result['topFive']==1
    assert result['unclassifiedAsset']==500 and result['unclassifiedSector']==500
    assert result['rows'][0]['source']=='Test provider'


@pytest.mark.parametrize('bad,reason',[
    (None,'unavailable'),({'price':100,'currency':'USD','demo':True},'Demo'),
    ({'price':100,'currency':'USD','stale':True},'expired'),
    ({'price':100,'currency':'CAD'},'currency'),({'price':float('nan'),'currency':'USD'},'Invalid')])
def test_partial_market_values_never_generate_full_weights(bad,reason):
    state=state_with_positions()
    result=allocation(state,lambda ticker:bad if ticker=='BBB' else quotes(ticker))
    assert not result['complete'] and not result['weightsAvailable'] and result['total']==200
    assert all(row['weight'] is None for row in result['rows'])
    assert result['hhi'] is None and reason in result['missing'][0]['reason']
    basis=allocation(state,lambda ticker:bad,'basis')
    assert basis['complete'] and basis['total']==400 and basis['rows'][0]['weight']==Decimal('.25')


def test_account_filter_classification_provenance_and_stale_labels():
    state=state_with_positions()
    state['securityClassifications']['AAA']=dict(assetType='equity',sector='technology',asOf='2020-01-01',source='manual')
    validate_workspace(state)
    result=allocation(state,quotes,account_id='legacy')
    assert len(result['rows'])==1 and result['largest']==1
    assert result['groups']['sector']=={'technology':200}
    assert result['unclassifiedSector']==0 and result['rows'][0]['classificationStale']
    with pytest.raises(ValueError,match='existing investment'):allocation(state,quotes,account_id='missing')
    bad=deepcopy(state);bad['securityClassifications']['AAA']['source']='guessed'
    with pytest.raises(ValueError,match='manual source'):validate_workspace(bad)


def test_empty_closed_and_zero_basis_positions_are_explicit():
    state=state_with_positions()
    state['trades']['AAA'].append(dict(id='sale',date='2025-02-01',side='sell',quantity=2,price=50,fees=0))
    assert allocation(state,quotes,account_id='legacy')['rows']==[]
    state['trades']['BBB'][0]['price']=0
    result=allocation(state,quotes,'basis','ira')
    assert result['total']==0 and not result['weightsAvailable'] and result['hhi'] is None
