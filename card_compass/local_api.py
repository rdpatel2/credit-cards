"""Opt-in loopback-only, scoped, read-only saved-record API, version 1.

Tokens and immutable pagination snapshots exist only for this app session.
No cookies, CORS, URL credentials, filesystem paths or remote listening.
"""
from collections import deque
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs
from .finance import FinanceStore
from .planning import PlanningStore,valid_day
from .investments import validate_workspace,initial_workspace
from .reports import DATASETS,rows_for

VERSION=1


def read_saved(directory,dataset,start,end):
    """Read an immutable SQLite snapshot and separately atomic workspace file."""
    directory=Path(directory)
    with TemporaryDirectory(prefix='compass-api-snapshot-') as temporary:
        temporary=Path(temporary)
        path=directory/'investments-workspace.json'
        state=validate_workspace(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else initial_workspace()
        with closing(sqlite3.connect((directory/'transactions.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(temporary/'transactions.sqlite3')) as destination:
            source.backup(destination)
        owner=SimpleNamespace(finance_store=FinanceStore(temporary),planning_store=PlanningStore(temporary),investment_store=SimpleNamespace(state=state))
        headers,rows=rows_for(owner,dataset,start,end)
        if len(rows)>100000:raise ValueError('Reduce the date range; a snapshot may contain at most 100,000 rows.')
        return headers,[[str(value) if isinstance(value,Decimal) else value for value in row] for row in rows]


class APIError(Exception):
    def __init__(self,status,message):self.status=status;self.message=message


class LocalAPI:
    def __init__(self,directory):
        self.directory=Path(directory);self.server=None;self.thread=None
        self.lock=threading.RLock();self.tokens={};self.snapshots={};self.audit=deque(maxlen=1000)

    @property
    def running(self):return self.server is not None

    @property
    def url(self):return f'http://127.0.0.1:{self.server.server_port}' if self.server else ''

    def issue(self,name,scopes,hours=24):
        scopes=set(scopes)
        if not isinstance(name,str) or not 1<=len(name.strip())<=120 or not scopes or scopes-set(DATASETS):raise ValueError('Name the token and choose explicit dataset scopes.')
        if type(hours) is not int or not 1<=hours<=168:raise ValueError('Choose a lifetime from 1 to 168 hours.')
        with self.lock:
            if len(self.tokens)>=20:raise ValueError('Remove an old token before issuing another (20 maximum).')
            secret=secrets.token_urlsafe(32);ident=secrets.token_hex(16)
            self.tokens[ident]=dict(id=ident,name=name.strip(),scopes=sorted(scopes),expires=time.time()+hours*3600,digest=hashlib.sha256(secret.encode()).hexdigest(),calls=deque())
        return ident,secret

    def token_info(self):
        with self.lock:return [{k:v for k,v in row.items() if k not in ('digest','calls')} for row in self.tokens.values()]

    def revoke(self,ident):
        with self.lock:
            self.tokens.pop(ident,None)
            self.snapshots={key:row for key,row in self.snapshots.items() if row['tokenId']!=ident}

    def authenticate(self,header):
        if not isinstance(header,str) or not header.startswith('Bearer ') or len(header)>100:raise APIError(401,'A valid Bearer token is required.')
        digest=hashlib.sha256(header[7:].encode()).hexdigest();now=time.time();clock=time.monotonic()
        with self.lock:
            token=next((r for r in self.tokens.values() if secrets.compare_digest(r['digest'],digest) and r['expires']>now),None)
            if token is None:raise APIError(401,'A valid Bearer token is required.')
            calls=token['calls']
            while calls and calls[0]<=clock-60:calls.popleft()
            if len(calls)>=60:raise APIError(429,'Request limit reached; retry after 60 seconds.')
            calls.append(clock);return token['id']

    def allowed(self,ident,dataset=None):
        with self.lock:
            token=self.tokens.get(ident)
            if not token or token['expires']<=time.time() or not self.running:raise APIError(401,'Token expired or revoked.')
            if dataset and dataset not in token['scopes']:raise APIError(403,'This token does not grant that dataset.')
            return list(token['scopes'])

    def records(self,ident,dataset,query):
        self.allowed(ident,dataset)
        if set(query)-{'start','end','limit','offset','snapshot'} or any(len(values)!=1 for values in query.values()):raise APIError(400,'Use only the documented single-valued query parameters.')
        try:
            start=query.get('start',[''])[0];end=query.get('end',[''])[0]
            valid_day(start);valid_day(end)
            if start>end or end>datetime.now().astimezone().date().isoformat():raise ValueError()
            limit=int(query.get('limit',['1000'])[0]);offset=int(query.get('offset',['0'])[0])
            if not 1<=limit<=1000 or not 0<=offset<=100000:raise ValueError()
        except (ValueError,TypeError):raise APIError(400,'Provide ordered nonfuture start/end ISO dates, limit 1–1000 and offset 0–100000.')
        key=query.get('snapshot',[''])[0];now=time.time()
        if not key:
            if offset:raise APIError(400,'Continue pagination with the first response snapshot ID.')
            with self.lock:
                self.snapshots={key:row for key,row in self.snapshots.items() if row['expires']>now}
                if len(self.snapshots)>=20:raise APIError(429,'Snapshot capacity reached; wait five minutes or revoke unused tokens.')
            try:headers,rows=read_saved(self.directory,dataset,start,end)
            except (ValueError,OSError,KeyError,TypeError,sqlite3.Error):raise APIError(503,'Saved data could not be read; review local data health or reduce the range.')
            self.allowed(ident,dataset)
            if len(json.dumps([headers,rows],ensure_ascii=False,allow_nan=False).encode('utf-8'))>8_000_000:raise APIError(413,'Snapshot too large; reduce the date range.')
            key=secrets.token_hex(16)
            snapshot=dict(tokenId=ident,dataset=dataset,start=start,end=end,headers=headers,rows=rows,expires=now+300,created=datetime.now(timezone.utc).isoformat())
            with self.lock:
                self.allowed(ident,dataset)
                if len(self.snapshots)>=20:raise APIError(429,'Snapshot capacity reached; retry later.')
                self.snapshots[key]=snapshot
        with self.lock:
            self.allowed(ident,dataset)
            snapshot=self.snapshots.get(key)
            if not snapshot or snapshot['expires']<=now:raise APIError(410,'Snapshot expired; restart pagination at offset zero.')
            if snapshot['tokenId']!=ident:raise APIError(403,'The snapshot belongs to another token.')
            if (dataset,start,end)!=(snapshot['dataset'],snapshot['start'],snapshot['end']):raise APIError(400,'Keep dataset and dates unchanged when using a snapshot.')
            rows=snapshot['rows'];headers=snapshot['headers'];selected=rows[offset:offset+limit]
            return dict(api_version=VERSION,schema_version=1,dataset=dataset,range_start=start,range_end=end,snapshot=key,snapshot_created=snapshot['created'],snapshot_expires_seconds=max(0,int(snapshot['expires']-now)),total=len(rows),offset=offset,limit=limit,next_offset=offset+len(selected) if offset+len(selected)<len(rows) else None,columns=headers,records=[dict(zip(headers,row)) for row in selected],coverage='Saved records only. SQLite and investment workspace are separately captured. No connected-institution certification, implicit FX or tax filing guarantee.')

    def start(self):
        if self.running:return self.url
        service=self
        class Handler(BaseHTTPRequestHandler):
            server_version='FinanceCompass';sys_version='';protocol_version='HTTP/1.0'
            def log_message(self,*args):pass
            def setup(self):
                super().setup();self.connection.settimeout(5)
            def response(self,status,payload):
                data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
                if len(data)>8_000_000:status=413;data=b'{"error":"Response too large; use a smaller page."}'
                self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Connection','close')
                if status==401:self.send_header('WWW-Authenticate','Bearer')
                if status==429:self.send_header('Retry-After','60')
                self.end_headers()
                try:self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):pass
            def do_GET(self):
                ident=None;status=200;dataset=None
                try:
                    if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}' or self.headers.get('Origin') is not None:raise APIError(403,'Only direct loopback API clients are accepted.')
                    if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length') not in (None,'0'):raise APIError(400,'GET bodies are not supported.')
                    ident=service.authenticate(self.headers.get('Authorization'))
                    parsed=urlsplit(self.path)
                    if parsed.scheme or parsed.netloc or len(self.path)>2000:raise APIError(400,'Use a relative API path.')
                    query=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=10)
                    if parsed.path=='/api/v1/datasets':
                        if query:raise APIError(400,'This endpoint accepts no query parameters.')
                        payload=dict(api_version=VERSION,schema_version=1,datasets=[dict(id=key,name=DATASETS[key]) for key in service.allowed(ident)],decimal_encoding='Exact monetary/share Decimal values are JSON strings; integer IDs/cents remain integers.',read_only=True)
                    elif parsed.path.startswith('/api/v1/records/') and parsed.path[16:] in DATASETS:
                        dataset=parsed.path[16:];payload=service.records(ident,dataset,query)
                    else:raise APIError(404,'Unknown version or endpoint.')
                    service.allowed(ident,dataset)
                except APIError as exc:status=exc.status;payload=dict(error=exc.message)
                except (ValueError,TypeError):status=400;payload=dict(error='Invalid request.')
                with service.lock:service.audit.append(dict(at=datetime.now(timezone.utc).isoformat(),tokenId=ident,status=status,dataset=dataset))
                self.response(status,payload)
            def reject_method(self):self.response(405,dict(error='Read-only API supports GET only.'))
            do_POST=reject_method;do_PUT=reject_method;do_PATCH=reject_method;do_DELETE=reject_method;do_OPTIONS=reject_method;do_HEAD=reject_method
        class Server(ThreadingHTTPServer):
            daemon_threads=True;block_on_close=False;request_queue_size=8
            def __init__(self,*args):self.capacity=threading.BoundedSemaphore(8);super().__init__(*args)
            def process_request(self,request,address):
                if not self.capacity.acquire(False):self.shutdown_request(request);return
                try:super().process_request(request,address)
                except BaseException:self.capacity.release();raise
            def process_request_thread(self,request,address):
                try:super().process_request_thread(request,address)
                finally:self.capacity.release()
        self.server=Server(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs=dict(poll_interval=.1),daemon=True);self.thread.start()
        return self.url

    def stop(self):
        with self.lock:
            server=self.server;self.server=None;self.tokens.clear();self.snapshots.clear()
        if server:server.shutdown();server.server_close()
        if self.thread:self.thread.join(timeout=2);self.thread=None
