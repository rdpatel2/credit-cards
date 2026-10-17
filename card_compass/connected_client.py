"""Opt-in desktop service client. Session credentials remain in memory only."""
import json
import time
import re
from urllib.parse import urlsplit,urlencode
from urllib.request import Request,build_opener
from urllib.error import HTTPError,URLError
from .plaid_provider import NoRedirect


class ConnectionError(ValueError):
    def __init__(self,status,message):self.status=status;super().__init__(message)


def service_url(value,allow_local_http=False):
    if not isinstance(value,str):raise ValueError('Enter the optional service HTTPS address.')
    parsed=urlsplit(value.strip())
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):raise ValueError('Use the service origin without credentials, paths or query parameters.')
    try:parsed.port
    except ValueError:raise ValueError('Use a valid service port.') from None
    if not parsed.hostname or (parsed.scheme!='https' and not (allow_local_http and parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost','::1'))):raise ValueError('HTTPS is required. Explicit development HTTP is limited to loopback.')
    return value.strip().rstrip('/')


class ConnectedClient:
    def __init__(self,url,allow_local_http=False,transport=None):
        self.url=service_url(url,allow_local_http);self.transport=transport;self.token='';self.expires=0;self.identity=None

    @property
    def signed_in(self):return bool(self.token and self.expires>time.time())

    def call(self,method,path,payload=None,query=None,authenticated=True):
        parsed=urlsplit(path)
        if method not in ('GET','POST','PUT','PATCH','DELETE') or not path.startswith('/service/v1/') or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:raise ValueError('Use a documented relative service endpoint.')
        if authenticated and not self.signed_in:raise ConnectionError(401,'Sign in to the reviewed optional service; local records remain available.')
        headers={'Accept':'application/json'}
        if authenticated:headers['Authorization']='Bearer '+self.token
        data=None
        if payload is not None:
            data=json.dumps(payload,allow_nan=False).encode()
            if len(data)>600000:raise ValueError('Request exceeds 600 KB; reduce the selected records.')
            headers['Content-Type']='application/json'
        url=self.url+path+('?'+urlencode(query) if query else '')
        if self.transport:return self.transport(method,url,payload,headers)
        request=Request(url,data=data,headers=headers,method=method)
        try:
            with build_opener(NoRedirect).open(request,timeout=25) as response:raw=response.read(20_000_001)
        except HTTPError as exc:
            try:
                value=json.loads(exc.read(100000));message=value.get('error','Service request failed.')
                if not isinstance(message,str):raise ValueError()
                message=' '.join(message.split())[:2000]
                for private in [self.token]+[payload.get(key,'') for key in ('password','newPassword','code','invitation','challenge','activationCode') if isinstance(payload,dict)]:
                    if isinstance(private,str) and private:message=message.replace(private,'[private]')
            except (ValueError,TypeError):message='Service request failed; review connection status.'
            if exc.code==401:self.forget()
            raise ConnectionError(exc.code,message) from None
        except (URLError,TimeoutError,OSError):raise ConnectionError(503,'The optional service could not be reached securely. Check its address/certificate and try again; local records remain available.') from None
        if len(raw)>20000000:raise ConnectionError(413,'Response is too large; review a smaller snapshot.')
        try:value=json.loads(raw)
        except ValueError:raise ConnectionError(503,'The optional service returned invalid JSON.') from None
        if not isinstance(value,dict):raise ConnectionError(503,'The optional service returned an unsupported response.')
        return value

    def accept_session(self,result):
        if not isinstance(result,dict) or not isinstance(result.get('token'),str) or not 20<=len(result['token'])<=100 or not isinstance(result.get('expires'),(int,float)) or isinstance(result['expires'],bool) or not time.time()<result['expires']<=time.time()+86400 or not isinstance(result.get('user'),dict):raise ConnectionError(503,'The service returned invalid session information.')
        if any(not isinstance(result['user'].get(key),str) or not 1<=len(result['user'][key])<=80 for key in ('id','username')):raise ConnectionError(503,'The service returned invalid identity information.')
        self.token=result['token'];self.expires=result['expires'];self.identity=dict(result['user'])
        return self.identity

    def login(self,name,password,code):return self.accept_session(self.call('POST','/service/v1/auth/login',dict(username=name,password=password,code=code),authenticated=False))

    def begin_enrollment(self,invitation,name,password):
        if self.signed_in:raise ValueError('Sign out before enrolling another identity.')
        result=self.call('POST','/service/v1/auth/enroll',dict(invitation=invitation,username=name,password=password),authenticated=False)
        if not isinstance(result,dict) or not isinstance(result.get('challenge'),str) or not 20<=len(result['challenge'])<=100 or not isinstance(result.get('mfaSecret'),str) or not re.fullmatch('[A-Z2-7]{32}',result['mfaSecret']) or not isinstance(result.get('expires'),(int,float)) or isinstance(result['expires'],bool) or not time.time()<result['expires']<=time.time()+660 or result.get('username')!=name.lower():raise ConnectionError(503,'The service returned invalid enrollment information. No identity is assumed; review the invitation or wait ten minutes before restarting.')
        return result

    def finish_enrollment(self,challenge,code,expected_name=None):
        result=self.call('POST','/service/v1/auth/enroll/confirm',dict(challenge=challenge,code=code),authenticated=False)
        recovery=result.get('recoveryCodes') if isinstance(result,dict) else None
        if not isinstance(recovery,list) or len(recovery)!=10 or any(not isinstance(value,str) or not re.fullmatch('[a-f0-9]{20}',value) for value in recovery) or len(set(recovery))!=10:raise ConnectionError(503,'Enrollment completion returned invalid recovery information. Sign-in may already exist; use the enrolled authenticator and contact the operator if needed.')
        if expected_name is not None and (not isinstance(result.get('user'),dict) or result['user'].get('username')!=expected_name):raise ConnectionError(503,'Enrollment returned a different identity from the reviewed invitation; no session was accepted. Contact the service operator.')
        identity=self.accept_session(result)
        return dict(user=identity,recoveryCodes=recovery)

    def cancel_enrollment(self,challenge):
        result=self.call('POST','/service/v1/auth/enroll/cancel',dict(challenge=challenge),authenticated=False)
        if result.get('canceled') is not True:raise ConnectionError(503,'Pending enrollment cancellation was not confirmed; its ten-minute expiry still applies.')
        return result

    def logout(self,all_sessions=False):
        try:
            if self.signed_in:self.call('POST','/service/v1/auth/logout',dict(allSessions=all_sessions))
        finally:self.forget()

    def forget(self):self.token='';self.expires=0;self.identity=None
