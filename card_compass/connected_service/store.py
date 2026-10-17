"""Encrypted optional-service records and explicit household authorization.

Separate from the desktop profile. A server-provided Fernet key is required;
losing it loses encrypted data, and it must be protected outside this database.
"""
import base64
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import struct
import time
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError,InvalidHashError
from cryptography.fernet import Fernet
from ..sharing import validate_snapshot

HASHER=PasswordHasher()
ROLES={'owner','editor','read_only'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS service_meta(id INTEGER PRIMARY KEY,key_check TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,
 password_hash TEXT NOT NULL,mfa TEXT NOT NULL,last_step INTEGER NOT NULL,recovery TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS session_details(digest TEXT PRIMARY KEY REFERENCES sessions(digest) ON DELETE CASCADE,
 id TEXT UNIQUE NOT NULL,created TEXT NOT NULL,label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS households(id TEXT PRIMARY KEY,name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members(household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,role TEXT NOT NULL,PRIMARY KEY(household_id,user_id));
CREATE TABLE IF NOT EXISTS invites(digest TEXT PRIMARY KEY,household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
 username TEXT NOT NULL,role TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS enrollments(digest TEXT PRIMARY KEY,invite_digest TEXT UNIQUE NOT NULL,
 username TEXT NOT NULL,password_hash TEXT NOT NULL,mfa TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY,creator TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 household_id TEXT REFERENCES households(id) ON DELETE CASCADE,kind TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS service_audit(id INTEGER PRIMARY KEY,at TEXT NOT NULL,user_id TEXT NOT NULL,action TEXT NOT NULL,entity_id TEXT NOT NULL);
'''


class ServiceError(ValueError):
    def __init__(self,status,message):self.status=status;super().__init__(message)


def username(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,80}',value):raise ServiceError(400,'Use a username of 3–80 letters, digits, dots, underscores or hyphens.')
    return value.lower()


def password(value):
    if not isinstance(value,str) or not 14<=len(value)<=256:raise ServiceError(400,'Use a password/passphrase of 14–256 characters.')
    return value


def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def now_iso():return datetime.now(timezone.utc).isoformat()
def new_mfa():return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def totp(secret,step):
    key=base64.b32decode(secret+'='*((-len(secret))%8),casefold=True)
    result=hmac.new(key,struct.pack('>Q',step),hashlib.sha1).digest();offset=result[-1]&15
    return f'{(struct.unpack(">I",result[offset:offset+4])[0]&0x7fffffff)%1000000:06d}'


def matched_step(secret,code,last_step,now=None):
    if not isinstance(code,str) or not re.fullmatch(r'\d{6}',code):return None
    step=int((time.time() if now is None else now)//30)
    for candidate in (step,step-1,step+1):
        if candidate>last_step and hmac.compare_digest(totp(secret,candidate),code):return candidate
    return None


class ServiceStore:
    def __init__(self,directory,key):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'service.sqlite3';self.cipher=Fernet(key)
        with self.connection() as db:
            db.executescript(SCHEMA)
            row=db.execute('SELECT key_check FROM service_meta WHERE id=1').fetchone()
            if row:
                if self.decrypt(row[0])!=dict(check='Finance Compass service v1'):raise ValueError('Wrong service encryption key.')
            else:db.execute('INSERT INTO service_meta VALUES (1,?)',(self.encrypt(dict(check='Finance Compass service v1')),))
            for row in db.execute('SELECT digest FROM sessions WHERE digest NOT IN (SELECT digest FROM session_details)').fetchall():
                db.execute('INSERT INTO session_details VALUES (?,?,?,?)',(row['digest'],secrets.token_hex(16),'','Legacy service session'))
        try:self.directory.chmod(0o700);self.path.chmod(0o600)
        except OSError:pass # Deployment must also enforce host/volume ACLs.
        self.dummy_hash=HASHER.hash(secrets.token_urlsafe(32))

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:yield db
        finally:db.close()

    def encrypt(self,value):return self.cipher.encrypt(json.dumps(value,allow_nan=False,separators=(',',':')).encode()).decode()
    def decrypt(self,value):return json.loads(self.cipher.decrypt(value.encode()))

    @staticmethod
    def audit(db,user,action,entity=''):db.execute('INSERT INTO service_audit(at,user_id,action,entity_id) VALUES (?,?,?,?)',(now_iso(),user,action,entity))

    def create_user(self,name,secret,mfa_secret=None):
        """Operator bootstrap or verified invite only; never public signup."""
        name=username(name);secret=password(secret);ident=secrets.token_hex(16);mfa_secret=mfa_secret or new_mfa()
        # Validate supplied enrollment material before creating the identity.
        totp(mfa_secret,int(time.time()//30))
        recovery=[secrets.token_hex(10) for _ in range(10)]
        with self.connection() as db:
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]>=1000:raise ServiceError(409,'Service identity capacity reached.')
            try:db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',(ident,name,HASHER.hash(secret),self.encrypt(mfa_secret),-1,json.dumps([digest(r) for r in recovery])))
            except sqlite3.IntegrityError:raise ServiceError(409,'That username is unavailable.') from None
            self.audit(db,ident,'identity_created',ident)
        return dict(id=ident,username=name,mfaSecret=mfa_secret,recoveryCodes=recovery)

    def login(self,name,secret,code,label='Finance Compass desktop'):
        name=username(name)
        if not isinstance(secret,str) or len(secret)>256:raise ServiceError(401,'Invalid credentials or second factor.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');user=db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
            try:valid=HASHER.verify(user['password_hash'] if user else self.dummy_hash,secret)
            except (VerificationError,InvalidHashError):valid=False
            if not user or not valid:raise ServiceError(401,'Invalid credentials or second factor.')
            step=matched_step(self.decrypt(user['mfa']),code,user['last_step']);recovery=json.loads(user['recovery'])
            recovery_key=digest(code) if isinstance(code,str) and len(code)==20 else ''
            if step is None and recovery_key not in recovery:raise ServiceError(401,'Invalid credentials or second factor.')
            if step is not None:db.execute('UPDATE users SET last_step=? WHERE id=?',(step,user['id']))
            else:
                recovery.remove(recovery_key);db.execute('UPDATE users SET recovery=? WHERE id=?',(json.dumps(recovery),user['id']))
            if HASHER.check_needs_rehash(user['password_hash']):db.execute('UPDATE users SET password_hash=? WHERE id=?',(HASHER.hash(secret),user['id']))
            db.execute('DELETE FROM sessions WHERE expires<=?',(time.time(),))
            if db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(user['id'],)).fetchone()[0]>=20:raise ServiceError(409,'Revoke old sessions before opening another.')
            token=secrets.token_urlsafe(32);expires=time.time()+43200
            db.execute('INSERT INTO sessions VALUES (?,?,?)',(digest(token),user['id'],expires));self.audit(db,user['id'],'login',user['id'])
            self.session_detail(db,token,label)
            return dict(token=token,expires=expires,user=dict(id=user['id'],username=user['username']))

    @staticmethod
    def session_detail(db,token,label='Finance Compass desktop'):
        if not isinstance(label,str) or not 1<=len(label.strip())<=80:raise ServiceError(400,'Use a session/device label of 1–80 characters.')
        db.execute('INSERT INTO session_details VALUES (?,?,?,?)',(digest(token),secrets.token_hex(16),now_iso(),label.strip()))

    def authenticate(self,token):
        if not isinstance(token,str) or not 20<=len(token)<=100:raise ServiceError(401,'Authentication required.')
        with self.connection() as db:
            user=db.execute('SELECT u.id,u.username FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.digest=? AND s.expires>?',(digest(token),time.time())).fetchone()
        if not user:raise ServiceError(401,'Session expired or revoked.')
        return dict(user)

    def logout(self,user,token,all_sessions=False):
        with self.connection() as db:
            if all_sessions:db.execute('DELETE FROM sessions WHERE user_id=?',(user,))
            else:db.execute('DELETE FROM sessions WHERE digest=? AND user_id=?',(digest(token),user))
            self.audit(db,user,'logout_all' if all_sessions else 'logout',user)

    @staticmethod
    def require_member(db,user,household,roles=None):
        row=db.execute('SELECT role FROM members WHERE household_id=? AND user_id=?',(household,user)).fetchone()
        if not row or (roles and row['role'] not in roles):raise ServiceError(403,'Household permission denied.')
        return row['role']

    def create_household(self,user,name):
        if not isinstance(name,str) or not 1<=len(name.strip())<=120:raise ServiceError(400,'Name the household (up to 120 characters).')
        ident=secrets.token_hex(16)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM members WHERE user_id=?',(user,)).fetchone()[0]>=5:raise ServiceError(409,'Join at most five households.')
            db.execute('INSERT INTO households VALUES (?,?)',(ident,self.encrypt(name.strip())))
            db.execute('INSERT INTO members VALUES (?,?,?)',(ident,user,'owner'));self.audit(db,user,'household_created',ident)
        return dict(id=ident,name=name.strip(),role='owner')

    def households(self,user):
        with self.connection() as db:return [dict(id=r['id'],name=self.decrypt(r['name']),role=r['role']) for r in db.execute('SELECT h.*,m.role FROM households h JOIN members m ON m.household_id=h.id WHERE m.user_id=?',(user,))]

    def invite(self,user,household,name,role):
        name=username(name)
        if role not in ROLES:raise ServiceError(400,'Choose owner, editor or read_only.')
        token=secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.require_member(db,user,household,{'owner'})
            db.execute('DELETE FROM invites WHERE expires<=?',(time.time(),))
            if db.execute('SELECT COUNT(*) FROM invites WHERE household_id=?',(household,)).fetchone()[0]>=20:raise ServiceError(409,'Revoke an unused invitation first.')
            db.execute('INSERT INTO invites VALUES (?,?,?,?,?)',(digest(token),household,name,role,time.time()+86400));self.audit(db,user,'invitation_created',household)
        return dict(invite=token,expires=time.time()+86400,username=name,role=role)

    def accept_invite(self,user,token):
        if not isinstance(token,str) or not 20<=len(token)<=100:raise ServiceError(400,'Invalid invitation.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');invite=db.execute('SELECT * FROM invites WHERE digest=? AND expires>?',(digest(token),time.time())).fetchone()
            identity=db.execute('SELECT username FROM users WHERE id=?',(user,)).fetchone()
            if not invite or not identity or identity['username']!=invite['username']:raise ServiceError(403,'Invitation is invalid, expired or intended for another identity.')
            if db.execute('SELECT COUNT(*) FROM members WHERE household_id=?',(invite['household_id'],)).fetchone()[0]>=20:raise ServiceError(409,'Household membership capacity reached.')
            if db.execute('SELECT COUNT(*) FROM members WHERE user_id=?',(user,)).fetchone()[0]>=5:raise ServiceError(409,'Join at most five households.')
            if db.execute('SELECT 1 FROM members WHERE household_id=? AND user_id=?',(invite['household_id'],user)).fetchone():raise ServiceError(409,'Already a household member; the owner must review role changes.')
            db.execute('INSERT INTO members VALUES (?,?,?)',(invite['household_id'],user,invite['role']));db.execute('DELETE FROM invites WHERE digest=?',(digest(token),));self.audit(db,user,'invitation_accepted',invite['household_id'])

    def begin_enrollment(self,invitation,name,secret):
        name=username(name);secret=password(secret)
        if not isinstance(invitation,str) or not 20<=len(invitation)<=100:raise ServiceError(403,'Invalid enrollment invitation.')
        challenge=secrets.token_urlsafe(32);mfa_secret=new_mfa()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');db.execute('DELETE FROM enrollments WHERE expires<=?',(time.time(),))
            row=db.execute('SELECT * FROM invites WHERE digest=? AND expires>?',(digest(invitation),time.time())).fetchone()
            if not row or row['username']!=name:raise ServiceError(403,'Invalid enrollment invitation or intended username.')
            if db.execute('SELECT 1 FROM users WHERE username=?',(name,)).fetchone():raise ServiceError(409,'This identity already exists; sign in and accept the invitation.')
            if db.execute('SELECT 1 FROM enrollments WHERE invite_digest=?',(digest(invitation),)).fetchone():raise ServiceError(409,'Enrollment already started; complete it or wait ten minutes to restart.')
            db.execute('INSERT INTO enrollments VALUES (?,?,?,?,?,?)',(digest(challenge),digest(invitation),name,HASHER.hash(secret),self.encrypt(mfa_secret),time.time()+600))
        return dict(challenge=challenge,mfaSecret=mfa_secret,expires=time.time()+600,username=name)

    def finish_enrollment(self,challenge,code):
        if not isinstance(challenge,str) or not 20<=len(challenge)<=100:raise ServiceError(403,'Invalid enrollment challenge.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            pending=db.execute('SELECT * FROM enrollments WHERE digest=? AND expires>?',(digest(challenge),time.time())).fetchone()
            invite=db.execute('SELECT * FROM invites WHERE digest=? AND expires>?',(pending['invite_digest'],time.time())).fetchone() if pending else None
            if not pending or not invite:raise ServiceError(403,'Enrollment expired or invitation was revoked; restart with a valid invitation.')
            step=matched_step(self.decrypt(pending['mfa']),code,-1)
            if step is None:raise ServiceError(401,'Confirm the current authenticator code.')
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]>=1000 or db.execute('SELECT COUNT(*) FROM members WHERE household_id=?',(invite['household_id'],)).fetchone()[0]>=20:raise ServiceError(409,'Identity or household capacity reached.')
            ident=secrets.token_hex(16);recovery=[secrets.token_hex(10) for _ in range(10)]
            try:db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)',(ident,pending['username'],pending['password_hash'],pending['mfa'],step,json.dumps([digest(r) for r in recovery])))
            except sqlite3.IntegrityError:raise ServiceError(409,'This identity already exists; sign in instead.') from None
            db.execute('INSERT INTO members VALUES (?,?,?)',(invite['household_id'],ident,invite['role']))
            db.execute('DELETE FROM invites WHERE digest=?',(pending['invite_digest'],));db.execute('DELETE FROM enrollments WHERE digest=?',(digest(challenge),))
            token=secrets.token_urlsafe(32);expires=time.time()+43200
            db.execute('INSERT INTO sessions VALUES (?,?,?)',(digest(token),ident,expires));self.audit(db,ident,'enrollment_completed',invite['household_id'])
            self.session_detail(db,token)
        return dict(token=token,expires=expires,user=dict(id=ident,username=pending['username']),recoveryCodes=recovery)

    def cancel_enrollment(self,challenge):
        """The enrollment challenge permits canceling only its pending setup.

        The invitation remains usable. No created identity, membership or
        session is deleted by this unauthenticated capability.
        """
        if not isinstance(challenge,str) or not 20<=len(challenge)<=100:raise ServiceError(403,'Invalid enrollment challenge.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            pending=db.execute('SELECT * FROM enrollments WHERE digest=?',(digest(challenge),)).fetchone()
            if not pending:raise ServiceError(404,'Pending enrollment was not found; it may have expired or already completed.')
            db.execute('DELETE FROM enrollments WHERE digest=?',(digest(challenge),))
            self.audit(db,'','enrollment_canceled','')
        return dict(canceled=True)

    def revoke_invite(self,user,household,invite_digest):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_member(db,user,household,{'owner'})
            if not db.execute('SELECT 1 FROM invites WHERE digest=? AND household_id=?',(invite_digest,household)).fetchone():raise ServiceError(404,'The invitation was not found; refresh its current state.')
            db.execute('DELETE FROM enrollments WHERE invite_digest=?',(invite_digest,))
            db.execute('DELETE FROM invites WHERE digest=? AND household_id=?',(invite_digest,household))
            self.audit(db,user,'invitation_revoked',household)

    def invitations(self,user,household):
        with self.connection() as db:
            self.require_member(db,user,household,{'owner'})
            return [dict(r) for r in db.execute('SELECT digest,username,role,expires FROM invites WHERE household_id=?',(household,))]

    def members(self,user,household):
        with self.connection() as db:
            self.require_member(db,user,household)
            return [dict(r) for r in db.execute('SELECT u.id,u.username,m.role FROM members m JOIN users u ON u.id=m.user_id WHERE m.household_id=?',(household,))]

    def set_role(self,user,household,target,role):
        if role is not None and role not in ROLES:raise ServiceError(400,'Choose a valid household role.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.require_member(db,user,household,{'owner'})
            existing=db.execute('SELECT role FROM members WHERE household_id=? AND user_id=?',(household,target)).fetchone()
            if not existing:raise ServiceError(404,'Member not found.')
            if existing[0]=='owner' and role!='owner' and db.execute("SELECT COUNT(*) FROM members WHERE household_id=? AND role='owner'",(household,)).fetchone()[0]<=1:raise ServiceError(409,'Keep at least one household owner.')
            if role:db.execute('UPDATE members SET role=? WHERE household_id=? AND user_id=?',(role,household,target))
            else:
                db.execute('DELETE FROM members WHERE household_id=? AND user_id=?',(household,target))
                # A removed member's publications become private to that member.
                db.execute('UPDATE resources SET household_id=NULL,version=version+1,updated=? WHERE household_id=? AND creator=?',(now_iso(),household,target))
                db.execute('DELETE FROM invites WHERE household_id=? AND username=(SELECT username FROM users WHERE id=?)',(household,target))
            self.audit(db,user,'role_changed' if role else 'member_removed',target)

    @staticmethod
    def validate_payload(kind,payload):
        try:validate_snapshot(kind,payload)
        except (ValueError,TypeError,KeyError):raise ServiceError(400,'Invalid selected-record snapshot; check schema, dates, amounts and excluded private fields.') from None

    @staticmethod
    def may_read(db,user,row):
        return row['creator']==user or (row['household_id'] is not None and db.execute('SELECT 1 FROM members WHERE household_id=? AND user_id=?',(row['household_id'],user)).fetchone() is not None)

    def resources(self,user,household=None):
        with self.connection() as db:
            if household:self.require_member(db,user,household)
            rows=db.execute('SELECT * FROM resources WHERE household_id=?' if household else 'SELECT * FROM resources WHERE creator=?',(household or user,)).fetchall()
            return [dict(id=r['id'],creator=r['creator'],householdId=r['household_id'],kind=r['kind'],version=r['version'],updated=r['updated'],payload=self.decrypt(r['payload'])) for r in rows if self.may_read(db,user,r)]

    def resource(self,user,ident):
        with self.connection() as db:
            row=db.execute('SELECT * FROM resources WHERE id=?',(ident,)).fetchone()
            if not row or not self.may_read(db,user,row):raise ServiceError(404,'Snapshot not found.')
            return dict(id=row['id'],creator=row['creator'],householdId=row['household_id'],kind=row['kind'],version=row['version'],updated=row['updated'],payload=self.decrypt(row['payload']))

    def save_resource(self,user,kind,payload,household=None,ident=None,expected_version=None):
        self.validate_payload(kind,payload);updated=now_iso()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if household:self.require_member(db,user,household,{'owner','editor'})
            if ident:
                row=db.execute('SELECT * FROM resources WHERE id=?',(ident,)).fetchone()
                if not row or not self.may_read(db,user,row):raise ServiceError(404,'Snapshot not found.')
                withdrawing=row['creator']==user and household is None and row['household_id'] is not None
                if row['household_id'] and not withdrawing:self.require_member(db,user,row['household_id'],{'owner','editor'})
                if withdrawing and payload!=self.decrypt(row['payload']):raise ServiceError(403,'Withdraw the existing snapshot first; review edits separately after it is private.')
                if row['creator']!=user and row['household_id']!=household:raise ServiceError(403,'Only the creator can change sharing scope.')
                if expected_version!=row['version']:raise ServiceError(409,'Snapshot changed on another device; reload and review before saving.')
                if kind!=row['kind']:raise ServiceError(400,'Snapshot type cannot be changed.')
                version=row['version']+1
                db.execute('UPDATE resources SET household_id=?,version=?,payload=?,updated=? WHERE id=?',(household,version,self.encrypt(payload),updated,ident))
            else:
                if db.execute('SELECT COUNT(*) FROM resources WHERE creator=?',(user,)).fetchone()[0]>=500:raise ServiceError(409,'Snapshot capacity reached; remove an unused snapshot first.')
                ident=secrets.token_hex(16);version=1
                db.execute('INSERT INTO resources VALUES (?,?,?,?,?,?,?)',(ident,user,household,kind,version,self.encrypt(payload),updated))
            self.audit(db,user,'snapshot_shared' if household else 'snapshot_private_saved',ident)
        return self.resource(user,ident)

    def delete_resource(self,user,ident,expected_version):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM resources WHERE id=?',(ident,)).fetchone()
            if not row or not self.may_read(db,user,row):raise ServiceError(404,'Snapshot not found.')
            if row['creator']!=user:raise ServiceError(403,'Only the creator may delete a snapshot.')
            if expected_version!=row['version']:raise ServiceError(409,'Snapshot changed; reload before deleting.')
            db.execute('DELETE FROM resources WHERE id=?',(ident,));self.audit(db,user,'snapshot_deleted',ident)
