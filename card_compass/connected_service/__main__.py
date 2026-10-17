"""Explicit operator setup; neither install nor desktop launch starts a service."""
import argparse
import getpass
import ipaddress
import os
from pathlib import Path
from urllib.parse import quote


def main():
    parser=argparse.ArgumentParser(description='Optional Finance Compass identity/sharing service')
    parser.add_argument('--data-dir',type=Path,required=True,help='Separate service directory, never the desktop profile')
    parser.add_argument('--bootstrap-user',help='Create an operator-provisioned identity and exit; password is prompted securely')
    parser.add_argument('--resolve-consent',help='Resolve unexchanged/canceled/uncertain consent only after external Plaid/bank revocation review; service must be stopped')
    parser.add_argument('--consent-user',help='Username that owns the uncertain consent session')
    parser.add_argument('--revocation-evidence',help='Private external revocation case/reference, never a credential')
    parser.add_argument('--confirm-external-revocation',action='store_true',help='Attest that external provider/bank consent was reviewed and revoked; no API confirmation is claimed')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--allowed-host',action='append',default=[])
    parser.add_argument('--tls-cert',type=Path)
    parser.add_argument('--tls-key',type=Path)
    parser.add_argument('--trusted-proxy',action='append',default=[],help='Explicit proxy IP; wildcards are rejected')
    parser.add_argument('--local-http',action='store_true',help='Development only: permit HTTP from a loopback client while bound to 127.0.0.1')
    args=parser.parse_args()
    if (args.data_dir/'transactions.sqlite3').exists() or (args.data_dir/'profile.json').exists():parser.error('Choose a separate service directory, not the desktop profile.')
    key=os.environ.get('FINANCE_SERVICE_KEY')
    if not key:parser.error('Configure FINANCE_SERVICE_KEY outside the service database before startup. Keep a protected recovery copy.')
    if not 1<=args.port<=65535:parser.error('Use a port from 1 to 65535.')
    if args.local_http and args.host!='127.0.0.1':parser.error('Development HTTP may only bind 127.0.0.1.')
    if bool(args.tls_cert)!=bool(args.tls_key):parser.error('Supply both a TLS certificate and its private key.')
    if args.host!='127.0.0.1' and not args.tls_cert:parser.error('Public binding requires direct TLS. Alternatively bind loopback behind an explicitly trusted HTTPS proxy.')
    for proxy in args.trusted_proxy:
        try:ipaddress.ip_address(proxy)
        except ValueError:parser.error('Trusted proxies must be explicit IP addresses, never wildcards.')
    if args.resolve_consent:
        if args.bootstrap_user or not args.consent_user or not args.revocation_evidence or not args.confirm_external_revocation:parser.error('Consent resolution requires --consent-user, --revocation-evidence and --confirm-external-revocation; it cannot also bootstrap an identity.')
    elif args.consent_user or args.revocation_evidence or args.confirm_external_revocation:parser.error('Consent-resolution arguments require --resolve-consent.')
    if not args.bootstrap_user and not args.resolve_consent and not args.tls_cert and not args.local_http and not args.trusted_proxy:parser.error('Configure direct TLS or an explicit trusted HTTPS proxy before serving. For loopback development only, use --local-http.')
    from .lease import DirectoryLease
    try:
        with DirectoryLease(args.data_dir):run(args,parser,key)
    except ValueError as exc:parser.error(str(exc))


def run(args,parser,key):
    from .store import ServiceStore,ServiceError
    from cryptography.fernet import InvalidToken
    try:store=ServiceStore(args.data_dir,key.encode())
    except (ValueError,TypeError,InvalidToken):parser.error('The configured service key is invalid or does not match this database.')
    if args.resolve_consent:
        from .connections import Connections
        try:Connections(store,None).resolve_uncertain(args.consent_user,args.resolve_consent,args.revocation_evidence)
        except ServiceError as exc:parser.error(str(exc))
        print('Recorded external revocation attestation. No provider API confirmation is claimed. The unresolved session is closed; start new consent only after external review.')
        return
    if args.bootstrap_user:
        secret=getpass.getpass('New passphrase (14–256 characters): ')
        if getpass.getpass('Confirm passphrase: ')!=secret:parser.error('Passphrases do not match.')
        try:result=store.create_user(args.bootstrap_user,secret)
        except ServiceError as exc:parser.error(str(exc))
        label=quote('Finance Compass:'+result['username'],safe='')
        print('Created identity:',result['username'])
        print('Enroll this private authenticator URI:',f"otpauth://totp/{label}?secret={result['mfaSecret']}&issuer=Finance%20Compass&algorithm=SHA1&digits=6&period=30")
        print('Store these one-use recovery codes privately:')
        for code in result['recoveryCodes']:print(code)
        return
    from .api import create_app
    from ..plaid_provider import PlaidProvider
    provider=None
    if os.environ.get('PLAID_CLIENT_ID') or os.environ.get('PLAID_SECRET'):
        try:provider=PlaidProvider(os.environ.get('PLAID_CLIENT_ID'),os.environ.get('PLAID_SECRET'),os.environ.get('PLAID_ENV','sandbox'))
        except ValueError as exc:parser.error(str(exc))
    import uvicorn
    hosts=args.allowed_host or ['127.0.0.1','localhost']
    app=create_app(store,allowed_hosts=hosts,allow_local_http=args.local_http,provider=provider,redirect_uri=os.environ.get('PLAID_REDIRECT_URI'))
    uvicorn.run(app,host=args.host,port=args.port,workers=1,access_log=False,proxy_headers=bool(args.trusted_proxy),forwarded_allow_ips=','.join(args.trusted_proxy),ssl_certfile=str(args.tls_cert) if args.tls_cert else None,ssl_keyfile=str(args.tls_key) if args.tls_key else None,limit_concurrency=32,timeout_keep_alive=5)


if __name__=='__main__':main()
