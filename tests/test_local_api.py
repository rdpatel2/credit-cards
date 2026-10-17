from datetime import date
import json
import time
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import pytest
from card_compass.local_api import LocalAPI
from card_compass.finance import FinanceStore
from card_compass.planning import PlanningStore
from card_compass.investments import InvestmentStore


def get(api,path,secret=None,method='GET',headers=None):
    headers=dict(headers or {})
    if secret:headers['Authorization']='Bearer '+secret
    request=Request(api.url+path,method=method,headers=headers)
    try:
        with urlopen(request,timeout=5) as response:return response.status,json.load(response),dict(response.headers)
    except HTTPError as exc:return exc.code,json.load(exc),dict(exc.headers)


@pytest.fixture
def api(tmp_path):
    finance=FinanceStore(tmp_path);PlanningStore(tmp_path);InvestmentStore(tmp_path).save(InvestmentStore(tmp_path).state)
    for i in range(3):finance.save_entry('Record '+str(i),'income','Salary',100,date.today().strftime('%Y-%m'))
    service=LocalAPI(tmp_path);service.start()
    yield service
    service.stop()


def dates():return f'start={date.today().replace(day=1)}&end={date.today()}'


def test_auth_scopes_expiry_revocation_and_read_only_methods(api):
    ident,token=api.issue('Selected scope',['cash_flow'])
    status,payload,headers=get(api,'/api/v1/datasets',token)
    assert status==200 and [r['id'] for r in payload['datasets']]==['cash_flow']
    assert headers['Cache-Control']=='no-store' and 'Access-Control-Allow-Origin' not in headers
    assert get(api,'/api/v1/datasets')[0]==401
    assert get(api,'/api/v1/records/trades?'+dates(),token)[0]==403
    assert get(api,'/api/v1/datasets',token,method='POST')[0]==405
    assert get(api,'/api/v2/datasets',token)[0]==404
    api.tokens[ident]['expires']=time.time()-1
    assert get(api,'/api/v1/datasets',token)[0]==401
    api.tokens[ident]['expires']=time.time()+60;api.revoke(ident)
    assert get(api,'/api/v1/datasets',token)[0]==401


def test_stable_snapshot_pagination_cannot_cross_tokens_or_change_dates(api):
    _,token=api.issue('Export',['cash_flow']);_,other=api.issue('Another',['cash_flow'])
    status,page,_=get(api,'/api/v1/records/cash_flow?'+dates()+'&limit=1',token)
    assert status==200 and page['total']==3 and page['next_offset']==1
    assert page['records'][0]['scheduled_usd']=='100'
    assert 'Salary' in str(page['records'])
    # Subsequent reads retain the initial snapshot despite a new local record.
    FinanceStore(api.directory).save_entry('New','income','Salary',200,date.today().strftime('%Y-%m'))
    path='/api/v1/records/cash_flow?'+dates()+'&snapshot='+page['snapshot']+'&offset=1&limit=1'
    status,next_page,_=get(api,path,token)
    assert status==200 and next_page['total']==3 and next_page['offset']==1
    assert get(api,path,other)[0]==403
    assert get(api,'/api/v1/records/cash_flow?'+dates()+'&offset=1',token)[0]==400
    api.snapshots[page['snapshot']]['expires']=time.time()-1
    assert get(api,path,token)[0]==410


def test_browser_rebinding_query_tokens_rate_limit_and_safe_failures(api):
    ident,token=api.issue('Export',['cash_flow'])
    assert get(api,'/api/v1/datasets',token,headers={'Origin':'https://untrusted.example'})[0]==403
    assert get(api,'/api/v1/datasets',token,headers={'Host':'untrusted.example'})[0]==403
    assert get(api,'/api/v1/datasets?token='+token)[0]==401
    assert get(api,'/api/v1/records/cash_flow?'+dates()+'&limit=1001',token)[0]==400
    assert get(api,'/api/v1/records/cash_flow?'+dates()+'&end=2026-01-01',token)[0]==400
    api.tokens[ident]['calls'].extend([time.monotonic()]*60)
    status,_,headers=get(api,'/api/v1/datasets',token)
    assert status==429 and headers['Retry-After']=='60'
    api.tokens[ident]['calls'].clear()
    (api.directory/'investments-workspace.json').write_text('invalid')
    status,payload,_=get(api,'/api/v1/records/cash_flow?'+dates(),token)
    assert status==503 and str(api.directory) not in str(payload)
    assert all(token not in str(row) for row in api.audit)


def test_stop_revokes_all_tokens_and_never_persists_secret(api):
    _,token=api.issue('Scoped',['cash_flow'])
    assert all(token not in str(row) for row in api.token_info())
    api.stop();assert not api.running and not api.tokens and not api.snapshots
