"""Explicit selected-record snapshot schema; never serialize the whole profile."""
from copy import deepcopy
import json
from .planning import ACCOUNT_TYPES,CURRENCIES,valid_day
from .investments import initial_workspace,validate_workspace,legacy_account

ACCOUNT_FIELDS={'id','name','kind','currency','owner'}
TRADE_FIELDS={'id','date','side','quantity','price','fees','accountId','externalId'}
VALUE_FIELDS={'accountId','date','amount','source'}
FLOW_FIELDS=VALUE_FIELDS|{'id','kind'}
DIVIDEND_FIELDS={'id','accountId','symbol','paid','exDate','currency','grossCents','qualifiedCents','withheldCents','classification','source','notes'}


def selected_fields(row,allowed):return {key:deepcopy(value) for key,value in row.items() if key in allowed}


def validate_snapshot(kind,payload):
    if not isinstance(payload,dict) or type(payload.get('version')) is not int or payload['version']!=1:raise ValueError('Snapshot schema version 1 is required.')
    if len(json.dumps(payload,allow_nan=False).encode())>500000:raise ValueError('Share at most 500 KB of selected records per snapshot.')
    if not isinstance(payload.get('name'),str) or not 1<=len(payload['name'].strip())<=120:raise ValueError('Name the shared account or goal (up to 120 characters).')
    common={'version','name'}
    if kind=='investment_account':
        if set(payload)!=common|{'workspace'}:raise ValueError('Share only the selected investment account records.')
        workspace=payload['workspace']
        if not isinstance(workspace,dict) or set(workspace)!={'investmentAccounts','trades','performanceRecords','dividendEvents'}:raise ValueError('Only selected account metadata, trades, valuations/flows and dividends can be shared.')
        if not isinstance(workspace['performanceRecords'],dict) or set(workspace['performanceRecords'])!={'valuations','flows'}:raise ValueError('Exclude undocumented/private performance metadata from selected records.')
        candidate=initial_workspace();candidate.update(deepcopy(workspace));validate_workspace(candidate)
        accounts=workspace['investmentAccounts']
        for rows,allowed in ((accounts,ACCOUNT_FIELDS),([r for values in workspace['trades'].values() for r in values],TRADE_FIELDS),(workspace['performanceRecords']['valuations'],VALUE_FIELDS),(workspace['performanceRecords']['flows'],FLOW_FIELDS),(workspace['dividendEvents'],DIVIDEND_FIELDS)):
            if any(set(row)-allowed for row in rows):raise ValueError('Exclude undocumented/private fields from selected investment records.')
        if {r['id'] for r in accounts}!={'legacy','shared'} or len(accounts)!=2 or next(r for r in accounts if r['id']=='legacy')!=legacy_account():raise ValueError('A snapshot contains exactly one selected account; exclude other private accounts.')
        if next(row for row in accounts if row['id']=='shared')['name']!=payload['name']:raise ValueError('The snapshot name must match its selected investment account.')
        rows=[r for values in workspace['trades'].values() for r in values]+workspace['performanceRecords']['valuations']+workspace['performanceRecords']['flows']+workspace['dividendEvents']
        if any(r.get('accountId')!='shared' for r in rows):raise ValueError('Exclude other accounts from the selected snapshot.')
        for row in [r for values in workspace['trades'].values() for r in values]:valid_day(row['date'],future=False)
        return payload
    if kind not in ('manual_account','goal'):raise ValueError('Choose a manual account, investment account or savings goal snapshot.')
    allowed=common|({'currency','accountType','owner','asOf','amountCents','aprBps','minimumPaymentCents','source'} if kind=='manual_account' else {'currency','goalType','asOf','amountCents','targetCents','targetDate','source'})
    if set(payload)!=allowed:raise ValueError('Use only the documented selected account/goal fields; private links and unrelated records are excluded.')
    if payload['currency'] not in CURRENCIES:raise ValueError('Unsupported snapshot currency; currencies are never implicitly combined.')
    valid_day(payload['asOf'],future=False)
    if not isinstance(payload['source'],str) or not 1<=len(payload['source'].strip())<=120:raise ValueError('Name the recorded balance/progress source.')
    for field in ('amountCents',)+(('minimumPaymentCents',) if kind=='manual_account' else ('targetCents',)):
        if type(payload[field]) is not int or not 0<=payload[field]<=100000000000000:raise ValueError('Use nonnegative integer-cent values within the supported range.')
    if kind=='manual_account':
        if payload['accountType'] not in ACCOUNT_TYPES or not isinstance(payload['owner'],str) or not 1<=len(payload['owner'].strip())<=120:raise ValueError('Choose an account type and recorded owner label.')
        if type(payload['aprBps']) is not int or not 0<=payload['aprBps']<=10000:raise ValueError('Use an APR from 0% to 100%.')
        if ACCOUNT_TYPES[payload['accountType']][1]=='asset' and (payload['aprBps'] or payload['minimumPaymentCents']):raise ValueError('Debt assumptions belong to liability accounts.')
    else:
        if payload['goalType'] not in ('emergency','savings') or payload['targetCents']<=0:raise ValueError('Choose a valid savings goal and positive target.')
        valid_day(payload['targetDate'])
    return payload


def manual_snapshot(row):
    if not row['as_of'] or row['amount_cents'] is None:raise ValueError('Record a dated balance before sharing this account.')
    result=dict(version=1,name=row['name'],currency=row['currency'],accountType=row['kind'],owner=row['owner'],asOf=row['as_of'],amountCents=row['amount_cents'],aprBps=row['apr_bps'],minimumPaymentCents=row['minimum_payment_cents'],source=row.get('source','Recorded manual balance'))
    return validate_snapshot('manual_account',result)


def goal_snapshot(row):
    if not row['as_of']:raise ValueError('Record dated progress before sharing this goal.')
    result=dict(version=1,name=row['name'],currency=row['currency'],goalType=row['kind'],asOf=row['as_of'],amountCents=row['saved_cents'],targetCents=row['target_cents'],targetDate=row['target_date'],source=row.get('source','Recorded manual goal allocation'))
    return validate_snapshot('goal',result)


def investment_snapshot(state,account_id):
    account=next((r for r in state['investmentAccounts'] if r['id']==account_id),None)
    if not account:raise ValueError('Select an existing investment account.')
    selected=selected_fields(account,ACCOUNT_FIELDS);selected['id']='shared'
    def selected_rows(rows,allowed):return [dict(selected_fields(r,allowed),accountId='shared') for r in rows if r.get('accountId','legacy')==account_id]
    trades={symbol:values for symbol,rows in state['trades'].items() if (values:=selected_rows(rows,TRADE_FIELDS))}
    performance=state.get('performanceRecords',dict(valuations=[],flows=[]))
    workspace=dict(investmentAccounts=[legacy_account(),selected],trades=trades,performanceRecords={key:selected_rows(performance[key],VALUE_FIELDS if key=='valuations' else FLOW_FIELDS) for key in ('valuations','flows')},dividendEvents=selected_rows(state.get('dividendEvents',[]),DIVIDEND_FIELDS))
    result=dict(version=1,name=account['name'],workspace=workspace)
    return validate_snapshot('investment_account',result)
