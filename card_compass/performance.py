"""Contribution-aware account returns under a declared end-of-day flow convention.

Valuations include cash and distributions retained in the account. Purchases and
sales are internal transactions, never inferred as external funding movements.
"""
from copy import deepcopy
from datetime import date,datetime
from decimal import Decimal
import math
import uuid
from zoneinfo import ZoneInfo
from scipy.optimize import brentq
from .investments import number
from .planning import valid_day,money_value


def validate_records(records,accounts):
    if not isinstance(records,dict):raise ValueError('Invalid performance records.')
    for kind in ('valuations','flows'):
        rows=records.get(kind)
        if not isinstance(rows,list) or len(rows)>10000:raise ValueError('Keep at most 10,000 records per performance dataset.')
        seen=set()
        for row in rows:
            if not isinstance(row,dict) or row.get('accountId') not in accounts:raise ValueError('Performance records need a valid investment account.')
            valid_day(row['date'],future=False)
            amount=number(row['amount'])
            if amount!=amount.quantize(Decimal('.01')):raise ValueError('Use USD amounts with at most two decimal places.')
            if not isinstance(row.get('source'),str) or not 1<=len(row['source'].strip())<=120:raise ValueError('Identify the valuation or flow source (up to 120 characters).')
            if kind=='valuations':key=row['accountId'],row['date']
            else:
                if row.get('kind') not in ('contribution','withdrawal') or amount<=0:raise ValueError('Record a positive contribution or withdrawal amount.')
                ident=row.get('id')
                if not isinstance(ident,str) or not 1<=len(ident)<=80:raise ValueError('Performance flows need a stable ID.')
                key=row['accountId'],ident
            if key in seen:raise ValueError('Duplicate performance record identity.')
            seen.add(key)


def empty_records():return dict(valuations=[],flows=[])


def save_record(state,account_id,day,amount,source,kind='valuation',ident=None):
    entered=number(amount)
    if entered!=entered.quantize(Decimal('.01')):raise ValueError('Use USD amounts with at most two decimal places.')
    candidate=deepcopy(state)
    records=candidate.setdefault('performanceRecords',empty_records())
    value=str(Decimal(money_value(amount))/100)
    if kind=='valuation':
        rows=records['valuations']
        index=next((i for i,row in enumerate(rows) if row['accountId']==account_id and row['date']==day),None)
        row=dict(accountId=account_id,date=day,amount=value,source=source.strip())
    else:
        if ident is None:ident=uuid.uuid4().hex
        rows=records['flows']
        index=next((i for i,row in enumerate(rows) if row['accountId']==account_id and row['id']==ident),None)
        row=dict(accountId=account_id,date=day,amount=value,source=source.strip(),kind=kind,id=ident)
    if index is None:rows.append(row)
    else:rows[index]=row
    validate_records(records,{a['id'] for a in candidate['investmentAccounts']})
    return candidate


def delete_record(state,account_id,kind,key):
    if kind not in ('valuations','flows'):raise ValueError('Choose valuations or flows.')
    candidate=deepcopy(state)
    records=candidate.setdefault('performanceRecords',empty_records())
    identity='date' if kind=='valuations' else 'id'
    records[kind]=[row for row in records[kind] if not (row['accountId']==account_id and row[identity]==key)]
    return candidate


def money_weighted(initial,terminal,start,end,flows):
    """XIRR with a conservative uniqueness guard for non-conventional cash flows."""
    start_date=date.fromisoformat(start)
    dates={start:-initial,end:terminal}
    for row in flows:
        amount=number(row['amount'])
        dates[row['date']]=dates.get(row['date'],Decimal(0))+(-amount if row['kind']=='contribution' else amount)
    items=[(date.fromisoformat(day),amount) for day,amount in sorted(dates.items()) if amount]
    signs=[1 if amount>0 else -1 for day,amount in items]
    if not signs or not (min(signs)<0<max(signs)):
        return None,'Money-weighted return requires both invested capital and a positive recovery value.'
    changes=sum(a!=b for a,b in zip(signs,signs[1:]))
    if changes!=1:
        return None,'Mixed cash-flow signs may have multiple internal rates of return; a unique MWR is not assured. Use the stated TWR and dollar gain.'
    coefficients=[float(amount) for day,amount in items]
    scale=max(abs(value) for value in coefficients)
    coefficients=[value/scale for value in coefficients]
    times=[(day-start_date).days/365 for day,amount in items]
    def residual(log_rate):
        powers=[-log_rate*year for year in times]
        shift=max(powers)
        return math.fsum(value*math.exp(power-shift) for value,power in zip(coefficients,powers))
    if residual(-20)*residual(20)>0:
        return None,'No supported money-weighted rate could be bracketed.'
    try:
        solved=brentq(residual,-20,20,xtol=1e-13,maxiter=200)
        rate=math.expm1(solved)
    except (ValueError,RuntimeError,OverflowError):
        return None,'Money-weighted rate did not converge; inspect values and flows.'
    return rate,''


def calculate(records,account_id,start,end):
    valid_day(start,future=False);valid_day(end,future=False)
    if start>=end:raise ValueError('Choose a start date before the end date.')
    valuations=sorted([row for row in records['valuations'] if row['accountId']==account_id and start<=row['date']<=end],key=lambda row:row['date'])
    if not valuations or valuations[0]['date']!=start or valuations[-1]['date']!=end:
        raise ValueError('Record exact opening and closing valuations on the selected dates. Values are not interpolated.')
    opening,closing=number(valuations[0]['amount']),number(valuations[-1]['amount'])
    if opening<=0:raise ValueError('Opening invested value must be positive.')
    flows=[row for row in records['flows'] if row['accountId']==account_id and start<row['date']<=end]
    net={}
    for row in flows:
        amount=number(row['amount'])*(1 if row['kind']=='contribution' else -1)
        net[row['date']]=net.get(row['date'],Decimal(0))+amount
    result=dict(start=start,end=end,opening=opening,closing=closing,netFlows=sum(net.values(),Decimal(0)),
                gain=closing-opening-sum(net.values(),Decimal(0)),twr=None,twrAnnualized=None,twrError='',
                mwr=None,mwrError='',intervals=[],flows=flows,valuations=valuations)
    valuation_dates={row['date'] for row in valuations}
    missing=sorted(day for day in net if day not in valuation_dates)
    if missing:
        result['twrError']='TWR needs an end-of-day valuation on every external-flow date. Missing: '+', '.join(missing)
    else:
        factor=Decimal(1)
        for prior,current in zip(valuations,valuations[1:]):
            before,after=number(prior['amount']),number(current['amount'])
            flow=net.get(current['date'],Decimal(0))
            if before<=0 or after-flow<0:
                result['twrError']='A return interval has zero starting capital or a negative pre-flow closing value. Inspect the records.'
                break
            rate=(after-flow)/before-1
            factor*=1+rate
            result['intervals'].append(dict(start=prior['date'],end=current['date'],opening=before,closing=after,flow=flow,returnRate=rate))
        if not result['twrError']:
            result['twr']=factor-1
            days=(date.fromisoformat(end)-date.fromisoformat(start)).days
            if days>=365:
                try:
                    annualized=float(factor)**(365/days)-1
                    if math.isfinite(annualized):result['twrAnnualized']=annualized
                except OverflowError:pass
    result['mwr'],result['mwrError']=money_weighted(opening,closing,start,end,flows)
    return result


def calculate_all(state,start,end):
    """Aligned whole-account totals; never carry missing account values forward."""
    valid_day(start,future=False);valid_day(end,future=False)
    if start>=end:raise ValueError('Choose a start date before the end date.')
    records=state['performanceRecords']
    legacy_used=any(row.get('accountId','legacy')=='legacy' for trades in state['trades'].values() for row in trades) or any(row['accountId']=='legacy' for kind in ('valuations','flows') for row in records[kind])
    accounts=[row for row in state['investmentAccounts'] if row['id']!='legacy' or legacy_used]
    if not accounts:raise ValueError('No accounts with recorded activity. Record account valuations first.')
    ids={row['id'] for row in accounts}
    dates={start,end}
    dates.update(row['date'] for kind in ('valuations','flows') for row in records[kind] if row['accountId'] in ids and start<row['date']<=end)
    lookup={(row['accountId'],row['date']):row for row in records['valuations']}
    missing=[(account['name'],day) for day in sorted(dates) for account in accounts if (account['id'],day) not in lookup]
    if missing:
        raise ValueError('Combined returns require aligned valuations for every included account on each valuation/flow date. Missing: '+', '.join(name+' '+day for name,day in missing[:8])+('…' if len(missing)>8 else ''))
    combined=dict(valuations=[dict(accountId='_combined',date=day,amount=str(sum((number(lookup[account['id'],day]['amount']) for account in accounts),Decimal(0))),source='Aligned recorded account totals') for day in sorted(dates)],
                  flows=[dict(row,accountId='_combined') for row in records['flows'] if row['accountId'] in ids])
    result=calculate(combined,'_combined',start,end)
    result['accountNames']=[row['name'] for row in accounts]
    result['sourceReferences']=sorted({row['source'] for row in records['valuations'] if row['accountId'] in ids and start<=row['date']<=end})
    return result


def benchmark_return(value,start,end):
    """Price-only comparison; exact endpoint dates required, no synthetic filling."""
    if not value or value.get('demo'):raise ValueError('A public historical benchmark series is required; demo values cannot compare personal returns.')
    if value.get('currency')!='USD':raise ValueError('Choose a USD benchmark; no FX conversion is performed.')
    try:zone=ZoneInfo(value.get('timezone','UTC'))
    except (ValueError,KeyError):raise ValueError('Benchmark time zone is unavailable.') from None
    points={}
    for stamp,price in value.get('points',[]):
        try:
            day=datetime.fromtimestamp(stamp,zone).date().isoformat()
            points[day]=number(price,True)
        except (ValueError,TypeError,OverflowError,OSError):continue
    if start not in points or end not in points:
        raise ValueError('Benchmark history must contain both selected dates. Select matching trading dates or load a longer range.')
    return dict(returnRate=points[end]/points[start]-1,startPrice=points[start],endPrice=points[end],
                source=value.get('source','Not supplied'),asOf=value.get('asOf','Not supplied'),cached=bool(value.get('stale')),
                definition='Provider chart price return; distributions, fees and personal cash-flow timing are excluded. It is not a total-return index.')
