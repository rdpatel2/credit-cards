import pytest
from datetime import datetime,timedelta,timezone
from card_compass.plaid_provider import PlaidProvider,ProviderError


def test_real_adapter_hosts_products_redirect_and_tokens_are_server_observed():
    calls=[]
    def transport(endpoint,payload):
        calls.append((endpoint,payload))
        if endpoint=='/link/token/create':return dict(link_token='link-fixture',hosted_link_url='https://secure.plaid.com/hl/fixture',expiration=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat())
        if endpoint=='/link/token/get':return dict(link_sessions=[dict(results=dict(item_add_results=[dict(public_token='public-fixture')]))])
        return dict(access_token='access-fixture',item_id='item-fixture')
    provider=PlaidProvider('fixture-id','fixture-secret',transport=transport)
    provider.create_link('owner',['transactions','investments'],'https://registered.example/redirect')
    assert calls[0][1]['products']==['transactions','investments'] and 'auth' not in calls[0][1]['products']
    assert provider.link_results('link-fixture')==['public-fixture']
    assert provider.exchange('public-fixture')['item_id']=='item-fixture'
    with pytest.raises(ValueError):provider.call('/transfer/create',{})
    with pytest.raises(ValueError):provider.create_link('owner',['auth'])
    with pytest.raises(ValueError):provider.create_link('owner',['transactions'],'http://untrusted.example')


def test_pagination_mutation_restarts_original_cursor_without_partial_commit():
    calls=[];first=True
    def transport(endpoint,payload):
        nonlocal first
        calls.append(payload.get('cursor'))
        if payload['cursor']=='origin':return dict(added=[dict(transaction_id='first')],modified=[],removed=[],next_cursor='page2',has_more=True)
        if first:first=False;raise ProviderError('TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION')
        return dict(added=[],modified=[dict(transaction_id='changed')],removed=[dict(transaction_id='removed')],next_cursor='done',has_more=False,transactions_update_status='HISTORICAL_UPDATE_COMPLETE')
    result=PlaidProvider('fixture','fixture',transport=transport).sync_transactions('fixture','origin')
    assert calls==['origin','page2','origin','page2']
    assert len(result['added'])==1 and result['cursor']=='done' and result['removed'][0]['transaction_id']=='removed'


def test_stalled_pagination_and_empty_initial_fetch_are_not_successful_history():
    def transport(endpoint,payload):return dict(added=[],modified=[],removed=[],next_cursor='same',has_more=True)
    with pytest.raises(ProviderError,match='REPEATED'):PlaidProvider('fixture','fixture',transport=transport).sync_transactions('fixture')
    result=PlaidProvider('fixture','fixture',transport=lambda *args:dict(added=[],modified=[],removed=[],next_cursor='',has_more=False,transactions_update_status='NOT_READY')).sync_transactions('fixture')
    assert result['status']=='NOT_READY' and not result['added']
    provider=PlaidProvider('fixture','fixture',transport=lambda *args:dict(link_token='fixture',hosted_link_url='https://untrusted.example'))
    with pytest.raises(ProviderError,match='HOSTED_LINK'):provider.create_link('owner',['transactions'])
