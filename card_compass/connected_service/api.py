"""Optional authenticated household service; no listener starts on import."""
from collections import defaultdict,deque
import threading
import time
from typing import Literal
from fastapi import FastAPI,Depends,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials
from pydantic import BaseModel,ConfigDict,Field
from cryptography.fernet import InvalidToken
from .store import ServiceError
from .connections import Connections
from .privacy import Privacy
from ..plaid_provider import ProviderError


class Model(BaseModel):model_config=ConfigDict(extra='forbid')
class Login(Model):
    username:str=Field(min_length=3,max_length=80)
    password:str=Field(min_length=1,max_length=256)
    code:str=Field(min_length=6,max_length=20)
    label:str=Field(default='Finance Compass desktop',min_length=1,max_length=80)
class Enrollment(Model):
    invitation:str=Field(min_length=20,max_length=100)
    username:str=Field(min_length=3,max_length=80)
    password:str=Field(min_length=14,max_length=256)
class EnrollmentConfirm(Model):
    challenge:str=Field(min_length=20,max_length=100)
    code:str=Field(min_length=6,max_length=6)
class EnrollmentCancel(Model):challenge:str=Field(min_length=20,max_length=100)
class Household(Model):name:str=Field(min_length=1,max_length=120)
class Invitation(Model):
    username:str=Field(min_length=3,max_length=80)
    role:Literal['owner','editor','read_only']
class Accept(Model):invitation:str=Field(min_length=20,max_length=100)
class Role(Model):role:Literal['owner','editor','read_only']|None
class Snapshot(Model):
    kind:Literal['manual_account','investment_account','goal']
    payload:dict
    householdId:str|None=Field(default=None,max_length=32)
    expectedVersion:int|None=Field(default=None,ge=1)
class Deletion(Model):expectedVersion:int=Field(ge=1)
class Logout(Model):allSessions:bool=False
class Link(Model):products:list[Literal['transactions','investments']]=Field(min_length=1,max_length=2)
class Refresh(Model):freshBalances:bool=False
class Proof(Model):
    password:str=Field(min_length=1,max_length=256)
    code:str=Field(min_length=6,max_length=20)
class PasswordChange(Proof):newPassword:str=Field(min_length=14,max_length=256)
class SecurityConfirmation(Model):code:str=Field(min_length=6,max_length=20)
class IdentityDeletion(Proof):
    planHash:str=Field(pattern='^[a-f0-9]{64}$')
    confirmation:str=Field(min_length=3,max_length=80)
    closeSoleHouseholds:bool=False


class LimitsMiddleware:
    """Bound bodies before parsing and reject insecure or browser-origin use."""
    def __init__(self,app,hosts,allow_local_http):
        self.app=app;self.hosts=set(hosts);self.allow_local_http=allow_local_http
        self.lock=threading.Lock();self.calls=defaultdict(deque)

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':await self.app(scope,receive,send);return
        headers=dict(scope['headers']);host=headers.get(b'host',b'').decode('latin1').split(':')[0].lower()
        peer=(scope.get('client') or ('unknown',))[0]
        async def reject(status,message):await JSONResponse(dict(error=message),status_code=status,headers={'Cache-Control':'no-store','Connection':'close'})(scope,receive,send)
        for key in (b'host',b'authorization',b'content-length'):
            if sum(name==key for name,value in scope['headers'])>1:await reject(400,'Duplicate security-sensitive header.');return
        if host not in self.hosts or b'origin' in headers:await reject(403,'Untrusted host or browser origin.');return
        if scope.get('scheme')!='https' and not (self.allow_local_http and peer in ('127.0.0.1','::1')):await reject(403,'HTTPS is required for this optional service.');return
        sensitive=scope['path'].startswith(('/service/v1/auth/','/service/v1/privacy/')) and scope['method'] not in ('GET','DELETE')
        clock=time.monotonic();maximum=10 if sensitive else 120
        key=(peer,'auth' if maximum==10 else 'api')
        with self.lock:
            for stale in [key for key,values in self.calls.items() if not values or values[-1]<clock-60]:self.calls.pop(stale,None)
            values=self.calls[key]
            while values and values[0]<clock-60:values.popleft()
            limited=len(values)>=maximum or len(self.calls)>10000
            if not limited:values.append(clock)
        if limited:await reject(429,'Request limit reached; retry after a minute.');return
        try:length=int(headers.get(b'content-length',b'0'))
        except ValueError:await reject(400,'Invalid request length.');return
        if length<0 or length>600000:await reject(413,'Request body exceeds 600 KB.');return
        data=bytearray()
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            data.extend(message.get('body',b''))
            if len(data)>600000:await reject(413,'Request body exceeds 600 KB.');return
            if not message.get('more_body'):break
        delivered=False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:delivered=True;return {'type':'http.request','body':bytes(data),'more_body':False}
            return await receive()
        async def protected_send(message):
            if message['type']=='http.response.start':
                message['headers']=list(message['headers'])+[(b'cache-control',b'no-store'),(b'x-content-type-options',b'nosniff'),(b'referrer-policy',b'no-referrer')]
                if scope.get('scheme')=='https':message['headers'].append((b'strict-transport-security',b'max-age=31536000'))
            await send(message)
        await self.app(scope,bounded_receive,protected_send)


def create_app(store,allowed_hosts=('127.0.0.1','localhost'),allow_local_http=False,provider=None,redirect_uri=None):
    app=FastAPI(title='Finance Compass optional service',docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(LimitsMiddleware,hosts=allowed_hosts,allow_local_http=allow_local_http)
    connections=Connections(store,provider,redirect_uri)
    privacy=Privacy(store,connections)
    bearer=HTTPBearer(auto_error=False)
    def user(credentials:HTTPAuthorizationCredentials|None=Depends(bearer)):
        if not credentials or credentials.scheme.lower()!='bearer':raise ServiceError(401,'Authentication required.')
        result=store.authenticate(credentials.credentials);result['token']=credentials.credentials;return result
    @app.exception_handler(ServiceError)
    async def service_error(request,exc):return JSONResponse(dict(error=str(exc)),status_code=exc.status)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):return JSONResponse(dict(error='Invalid request fields; check the documented schema.'),status_code=422)
    @app.exception_handler(InvalidToken)
    async def encrypted_record_error(request,exc):return JSONResponse(dict(error='Encrypted service data could not be read; contact the service operator.'),status_code=503)
    @app.exception_handler(ProviderError)
    async def provider_error(request,exc):return JSONResponse(dict(error=str(exc),code=exc.code),status_code=503)

    @app.post('/service/v1/auth/login')
    def login(data:Login):return store.login(data.username,data.password,data.code,data.label)
    @app.post('/service/v1/auth/enroll')
    def enroll(data:Enrollment):return store.begin_enrollment(data.invitation,data.username,data.password)
    @app.post('/service/v1/auth/enroll/confirm')
    def confirm(data:EnrollmentConfirm):return store.finish_enrollment(data.challenge,data.code)
    @app.post('/service/v1/auth/enroll/cancel')
    def cancel_enrollment(data:EnrollmentCancel):return store.cancel_enrollment(data.challenge)
    @app.post('/service/v1/auth/logout')
    def logout(data:Logout,identity=Depends(user)):
        store.logout(identity['id'],identity['token'],data.allSessions);return dict(revoked=True)
    @app.get('/service/v1/auth/sessions')
    def sessions(identity=Depends(user)):return dict(sessions=privacy.sessions(identity['id'],identity['token']))
    @app.delete('/service/v1/auth/sessions/{ident}')
    def revoke_session(ident:str,identity=Depends(user)):return privacy.revoke_session(identity['id'],identity['token'],ident)
    @app.post('/service/v1/auth/password')
    def change_password(data:PasswordChange,identity=Depends(user)):return privacy.change_password(identity['id'],identity['token'],data.password,data.code,data.newPassword)
    @app.get('/service/v1/auth/security')
    def security_setups(identity=Depends(user)):return dict(pending=privacy.changes(identity['id'],identity['token']))
    @app.post('/service/v1/auth/security/{kind}/begin')
    def begin_security(kind:Literal['mfa','recovery'],data:Proof,identity=Depends(user)):return privacy.begin_change(identity['id'],identity['token'],data.password,data.code,kind)
    @app.post('/service/v1/auth/security/{ident}/confirm')
    def confirm_security(ident:str,data:SecurityConfirmation,identity=Depends(user)):return privacy.confirm_change(identity['id'],identity['token'],ident,data.code)
    @app.delete('/service/v1/auth/security/{ident}')
    def cancel_security(ident:str,identity=Depends(user)):return privacy.cancel_change(identity['id'],identity['token'],ident)
    @app.post('/service/v1/privacy/exports')
    def prepare_export(data:Proof,identity=Depends(user)):return privacy.prepare_export(identity['id'],identity['token'],data.password,data.code)
    @app.get('/service/v1/privacy/exports')
    def list_exports(identity=Depends(user)):return dict(exports=privacy.exports(identity['id'],identity['token']))
    @app.get('/service/v1/privacy/exports/{ident}/chunks/{sequence}')
    def export_chunk(ident:str,sequence:int,identity=Depends(user)):return privacy.export_chunk(identity['id'],identity['token'],ident,sequence)
    @app.post('/service/v1/privacy/exports/{ident}/claim')
    def claim_export(ident:str,data:Proof,identity=Depends(user)):return privacy.claim_export(identity['id'],identity['token'],ident,data.password,data.code)
    @app.delete('/service/v1/privacy/exports/{ident}')
    def cancel_export(ident:str,identity=Depends(user)):return privacy.cancel_export(identity['id'],identity['token'],ident)
    @app.get('/service/v1/privacy/deletion-plan')
    def deletion_plan(identity=Depends(user)):return privacy.deletion_plan(identity['id'],identity['token'])
    @app.post('/service/v1/privacy/delete-identity')
    def delete_identity(data:IdentityDeletion,identity=Depends(user)):return privacy.delete_identity(identity['id'],identity['token'],data.password,data.code,data.planHash,data.confirmation,data.closeSoleHouseholds)
    @app.get('/service/v1/me')
    def me(identity=Depends(user)):return dict(id=identity['id'],username=identity['username'])
    @app.get('/service/v1/capabilities')
    def capabilities(identity=Depends(user)):return dict(version=1,sharing=True,provider=provider.environment if provider else None,upload='Explicit selected snapshots only; no automatic local profile upload.')
    @app.get('/service/v1/households')
    def households(identity=Depends(user)):return dict(households=store.households(identity['id']))
    @app.post('/service/v1/households')
    def household(data:Household,identity=Depends(user)):return store.create_household(identity['id'],data.name)
    @app.get('/service/v1/households/{household}/members')
    def members(household:str,identity=Depends(user)):return dict(members=store.members(identity['id'],household))
    @app.patch('/service/v1/households/{household}/members/{member}')
    def role(household:str,member:str,data:Role,identity=Depends(user)):
        store.set_role(identity['id'],household,member,data.role);return dict(updated=True)
    @app.get('/service/v1/households/{household}/invitations')
    def invitations(household:str,identity=Depends(user)):return dict(invitations=store.invitations(identity['id'],household))
    @app.post('/service/v1/households/{household}/invitations')
    def invite(household:str,data:Invitation,identity=Depends(user)):return store.invite(identity['id'],household,data.username,data.role)
    @app.delete('/service/v1/households/{household}/invitations/{invite_digest}')
    def revoke(household:str,invite_digest:str,identity=Depends(user)):
        store.revoke_invite(identity['id'],household,invite_digest);return dict(revoked=True)
    @app.post('/service/v1/invitations/accept')
    def accept(data:Accept,identity=Depends(user)):
        store.accept_invite(identity['id'],data.invitation);return dict(accepted=True)
    @app.get('/service/v1/snapshots')
    def snapshots(household:str|None=None,identity=Depends(user)):return dict(snapshots=store.resources(identity['id'],household))
    @app.get('/service/v1/snapshots/{ident}')
    def snapshot(ident:str,identity=Depends(user)):return store.resource(identity['id'],ident)
    @app.post('/service/v1/snapshots')
    def create_snapshot(data:Snapshot,identity=Depends(user)):
        if data.expectedVersion is not None:raise ServiceError(400,'New snapshots have no existing version.')
        return store.save_resource(identity['id'],data.kind,data.payload,data.householdId)
    @app.put('/service/v1/snapshots/{ident}')
    def save_snapshot(ident:str,data:Snapshot,identity=Depends(user)):
        if data.expectedVersion is None:raise ServiceError(400,'Supply the version reviewed before editing.')
        return store.save_resource(identity['id'],data.kind,data.payload,data.householdId,ident,data.expectedVersion)
    @app.delete('/service/v1/snapshots/{ident}')
    def delete_snapshot(ident:str,data:Deletion,identity=Depends(user)):
        store.delete_resource(identity['id'],ident,data.expectedVersion);return dict(deleted=True)
    @app.post('/service/v1/connections/link')
    def link(data:Link,identity=Depends(user)):return connections.begin(identity['id'],data.products)
    @app.get('/service/v1/connections/link')
    def pending_links(identity=Depends(user)):return dict(pending=connections.pending(identity['id']))
    @app.post('/service/v1/connections/link/{ident}/complete')
    def complete_link(ident:str,identity=Depends(user)):return connections.complete(identity['id'],ident)
    @app.delete('/service/v1/connections/link/{ident}')
    def cancel_link(ident:str,identity=Depends(user)):
        connections.cancel(identity['id'],ident);return dict(canceled=True)
    @app.get('/service/v1/connections')
    def connection_list(identity=Depends(user)):return dict(connections=connections.items(identity['id']))
    @app.post('/service/v1/connections/{ident}/refresh')
    def refresh(ident:str,data:Refresh,identity=Depends(user)):return connections.refresh(identity['id'],ident,data.freshBalances)
    @app.get('/service/v1/connections/{ident}/snapshot')
    def provider_snapshot(ident:str,identity=Depends(user)):return connections.snapshot(identity['id'],ident)
    @app.get('/service/v1/connections/{ident}/balances')
    def provider_balances(ident:str,identity=Depends(user)):return connections.balances(identity['id'],ident)
    @app.delete('/service/v1/connections/{ident}')
    def disconnect(ident:str,identity=Depends(user)):return connections.disconnect(identity['id'],ident)
    return app
