"""Real Plaid read-only product adapter; credentials belong on the optional server.

No simulated success or unsupported products. Hosted Link stays in the browser.
Use explicit user actions; do not run this adapter from ordinary desktop startup.
"""
from datetime import datetime,timezone
import json
from urllib.error import HTTPError,URLError
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.parse import urlsplit

ENDPOINTS={'/link/token/create','/link/token/get','/item/public_token/exchange',
           '/accounts/get','/accounts/balance/get','/investments/holdings/get',
           '/transactions/sync','/item/get','/item/remove'}


class ProviderError(ValueError):
    def __init__(self,code,request_id=''):
        self.code=code;self.request_id=request_id
        super().__init__('Plaid request failed: '+code+(f' · request {request_id}' if request_id else '')+'. Review connection status; existing saved data is unchanged.')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):return None


class PlaidProvider:
    def __init__(self,client_id,secret,environment='sandbox',transport=None):
        if environment not in ('sandbox','production'):raise ValueError('Choose the Plaid sandbox or production environment.')
        if not isinstance(client_id,str) or not client_id or not isinstance(secret,str) or not secret:raise ValueError('Configure server-side Plaid credentials before connecting institutions.')
        self.client_id=client_id;self.secret=secret;self.environment=environment;self.transport=transport

    def call(self,endpoint,fields):
        if endpoint not in ENDPOINTS or not isinstance(fields,dict) or {'client_id','secret'}&fields.keys():raise ValueError('Unsupported provider operation or credential override.')
        payload=dict(fields,client_id=self.client_id,secret=self.secret)
        if self.transport:return self.transport(endpoint,payload)
        request=Request('https://'+self.environment+'.plaid.com'+endpoint,
            data=json.dumps(payload,allow_nan=False).encode(),headers={'Content-Type':'application/json','Plaid-Version':'2020-09-14'},method='POST')
        try:
            with build_opener(NoRedirect).open(request,timeout=25) as response:raw=response.read(8_000_001)
        except HTTPError as exc:
            try:
                error=json.loads(exc.read(100_000));code=error.get('error_code','HTTP_'+str(exc.code));request_id=error.get('request_id','')
            except (ValueError,TypeError):code='HTTP_'+str(exc.code);request_id=''
            raise ProviderError(str(code)[:120],str(request_id)[:120]) from None
        except (URLError,TimeoutError,OSError):raise ProviderError('NETWORK_UNAVAILABLE') from None
        if len(raw)>8_000_000:raise ProviderError('RESPONSE_TOO_LARGE')
        try:result=json.loads(raw)
        except (ValueError,TypeError):raise ProviderError('INVALID_RESPONSE') from None
        if not isinstance(result,dict):raise ProviderError('INVALID_RESPONSE')
        if result.get('error_code'):raise ProviderError(str(result['error_code'])[:120],str(result.get('request_id',''))[:120])
        return result

    def create_link(self,user_id,products,redirect_uri=None,access_token=None):
        if not isinstance(user_id,str) or not user_id or not isinstance(products,list) or not products or len(products)!=len(set(products)) or set(products)-{'transactions','investments'}:raise ValueError('Choose explicit read-only transactions and/or investment products.')
        fields=dict(client_name='Finance Compass',country_codes=['US'],language='en',user={'client_user_id':user_id},hosted_link={'url_lifetime_seconds':1800})
        if access_token:fields['access_token']=access_token
        else:fields['products']=products
        if redirect_uri:
            parsed=urlsplit(redirect_uri)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:raise ValueError('Configure a registered HTTPS Plaid OAuth redirect URI.')
            fields['redirect_uri']=redirect_uri
        result=self.call('/link/token/create',fields)
        parsed=urlsplit(result.get('hosted_link_url',''))
        if parsed.scheme!='https' or parsed.hostname!='secure.plaid.com' or parsed.username or parsed.password:raise ProviderError('INVALID_HOSTED_LINK')
        if not isinstance(result.get('link_token'),str) or not result['link_token']:raise ProviderError('INVALID_LINK_TOKEN')
        try:
            expiration=datetime.fromisoformat(result['expiration'].replace('Z','+00:00'))
            if expiration.tzinfo is None or expiration<=datetime.now(timezone.utc):raise ValueError()
        except (ValueError,KeyError,TypeError,AttributeError):raise ProviderError('INVALID_LINK_EXPIRATION') from None
        return result

    def link_results(self,link_token):
        result=self.call('/link/token/get',dict(link_token=link_token));tokens=[]
        for session in result.get('link_sessions',[]):
            for added in session.get('results',{}).get('item_add_results',[]):
                public=added.get('public_token')
                if public and public not in tokens:tokens.append(public)
        # Never take a public token from the desktop; associate the stored Link
        # token and its server-observed results with the authenticated owner.
        return tokens

    def exchange(self,public_token):
        result=self.call('/item/public_token/exchange',dict(public_token=public_token))
        if not isinstance(result.get('access_token'),str) or not result['access_token'] or not isinstance(result.get('item_id'),str) or not result['item_id']:raise ProviderError('INVALID_EXCHANGE_RESULT')
        return result

    def accounts(self,access_token,fresh=False):
        return self.call('/accounts/balance/get' if fresh else '/accounts/get',dict(access_token=access_token))

    def holdings(self,access_token):return self.call('/investments/holdings/get',dict(access_token=access_token))

    def sync_transactions(self,access_token,cursor=None):
        """Stage all pages; pagination mutation restarts the original cursor.

        Caller commits staged changes and cursor together, only after success.
        A null/empty initial update is fetching, not certified zero history.
        """
        for attempt in range(3):
            next_cursor=cursor;added={};modified={};removed={};seen=set();request_ids=[]
            try:
                for page in range(100):
                    fields=dict(access_token=access_token,count=500)
                    if next_cursor is not None:fields['cursor']=next_cursor
                    result=self.call('/transactions/sync',fields)
                    for key,destination in [('added',added),('modified',modified),('removed',removed)]:
                        rows=result.get(key)
                        if not isinstance(rows,list):raise ProviderError('INVALID_TRANSACTION_PAGE')
                        for row in rows:
                            ident=row.get('transaction_id') if isinstance(row,dict) else None
                            if not isinstance(ident,str) or not ident:raise ProviderError('INVALID_TRANSACTION_ID')
                            destination[ident]=row
                    new_cursor=result.get('next_cursor')
                    if not isinstance(new_cursor,str) or type(result.get('has_more')) is not bool:raise ProviderError('INVALID_TRANSACTION_CURSOR')
                    request_ids.append(result.get('request_id',''))
                    if sum(len(r) for r in (added,modified,removed))>50000:raise ProviderError('TRANSACTION_CAPACITY')
                    if not result['has_more']:return dict(added=list(added.values()),modified=list(modified.values()),removed=list(removed.values()),cursor=new_cursor,requestIds=request_ids,fetchedAt=datetime.now(timezone.utc).isoformat(),status=result.get('transactions_update_status','UNKNOWN'))
                    if new_cursor in seen:raise ProviderError('REPEATED_TRANSACTION_CURSOR')
                    seen.add(new_cursor);next_cursor=new_cursor
                raise ProviderError('TOO_MANY_TRANSACTION_PAGES')
            except ProviderError as exc:
                if exc.code!='TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION' or attempt==2:raise
        raise ProviderError('TRANSACTION_SYNC_FAILED')

    def remove(self,access_token):return self.call('/item/remove',dict(access_token=access_token))
