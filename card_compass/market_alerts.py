"""User-defined, descriptive in-app market conditions; never trading instructions."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import re
import uuid
from zoneinfo import ZoneInfo
from .investments import symbol,number
from .trading_calendar import calendar


def initial_alerts():return dict(rules=[],events=[],quietHours=None,capacityWarning=False)


def aware_time(value):
    if not isinstance(value,str):raise ValueError('Alert timestamps must be ISO text.')
    moment=datetime.fromisoformat(value)
    if moment.tzinfo is None:raise ValueError('Alert timestamps need a time zone.')
    return moment


def validate_alerts(state):
    if not isinstance(state,dict):raise ValueError('Invalid market alerts.')
    rules,events=state.get('rules'),state.get('events')
    if not isinstance(rules,list) or len(rules)>100 or not isinstance(events,list) or len(events)>1000:
        raise ValueError('Keep at most 100 market rules and 1,000 notifications.')
    if type(state.get('capacityWarning')) is not bool:raise ValueError('Invalid alert capacity state.')
    quiet=state.get('quietHours')
    if quiet is not None:
        if not isinstance(quiet,dict) or any(type(quiet.get(key)) is not int or not 0<=quiet[key]<=23 for key in ('start','end')) or quiet['start']==quiet['end']:
            raise ValueError('Quiet hours need different whole-hour start/end times (0–23).')
    ids=set()
    for rule in rules:
        ident=rule.get('id') if isinstance(rule,dict) else None
        if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{32}',ident) or ident in ids:raise ValueError('Alert rules need unique IDs.')
        ids.add(ident)
        if not isinstance(rule.get('name'),str) or not 1<=len(rule['name'].strip())<=120:raise ValueError('Name the alert (up to 120 characters).')
        if type(rule.get('enabled')) is not bool or rule.get('kind') not in ('price','earnings','stale'):raise ValueError('Invalid alert type or enabled state.')
        if rule['kind']=='price':
            symbol(rule['symbol'])
            if rule.get('direction') not in ('above','below') or number(rule['threshold'],True)>Decimal('1e9'):raise ValueError('Choose above/below and a positive USD price threshold.')
        elif rule['kind']=='earnings':
            symbol(rule['symbol'])
            if type(rule.get('lookahead')) is not int or not 0<=rule['lookahead']<=7:raise ValueError('Use an earnings window of 0–7 calendar days.')
        elif type(rule.get('delayMinutes')) is not int or not 1<=rule['delayMinutes']<=240:
            raise ValueError('Use a data-problem delay of 1–240 minutes.')
        runtime=rule.get('runtime')
        if not isinstance(runtime,dict) or type(runtime.get('active')) is not bool or type(runtime.get('sequence')) is not int or not 0<=runtime['sequence']<=1_000_000_000:
            raise ValueError('Invalid alert condition state.')
        if runtime.get('since') is not None:aware_time(runtime['since'])
        seen=runtime.get('seen')
        if not isinstance(seen,list) or len(seen)>100 or any(not isinstance(key,str) or len(key)>120 for key in seen):raise ValueError('Invalid earnings alert history.')
    ids=set()
    for event in events:
        if not isinstance(event,dict) or not isinstance(event.get('id'),str) or not re.fullmatch(r'[a-f0-9]{32}',event['id']) or event['id'] in ids:
            raise ValueError('Invalid alert notification identity.')
        ids.add(event['id']);aware_time(event['created'])
        if event.get('kind') not in ('price','earnings','stale') or type(event.get('read')) is not bool:raise ValueError('Invalid notification state.')
        for key,limit in [('ruleId',80),('title',300),('details',3000),('source',300),('asOf',120)]:
            if not isinstance(event.get(key),str) or not 1<=len(event[key])<=limit:raise ValueError('Invalid notification text.')


def rule_values(name,kind,enabled=True,**config):
    return dict(id=uuid.uuid4().hex,name=name.strip(),kind=kind,enabled=bool(enabled),
                runtime=dict(active=False,sequence=0,since=None,seen=[]),**config)


def session_dates(today,lookahead):
    days=[]
    for offset in range(lookahead+1):
        day=today+timedelta(days=offset)
        if calendar(day.year).is_session(day):days.append(day.isoformat())
    return days


def requirements(alerts,now=None):
    now=now or datetime.now(timezone.utc)
    today=now.astimezone(ZoneInfo('America/New_York')).date()
    keys=set()
    for rule in alerts['rules']:
        if not rule['enabled']:continue
        if rule['kind']=='price':keys.add(('quote',rule['symbol']+'|1d'))
        elif rule['kind']=='earnings':keys.update(('earnings',day) for day in session_dates(today,rule['lookahead']))
    return keys


def is_quiet(alerts,now=None):
    quiet=alerts['quietHours']
    if quiet is None:return False
    hour=(now or datetime.now(timezone.utc)).astimezone().hour
    return quiet['start']<=hour<quiet['end'] if quiet['start']<quiet['end'] else hour>=quiet['start'] or hour<quiet['end']


def evaluate(alerts,lookup,feeds,now=None):
    """Pure evaluation; latches survive restarts and unhealthy data cannot rearm price alerts."""
    validate_alerts(alerts)
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Use a timezone-aware alert clock.')
    moment=now.astimezone(timezone.utc).isoformat()
    today=now.astimezone(ZoneInfo('America/New_York')).date()
    candidate=deepcopy(alerts);new=[]
    def emit(rule,title,details,source,as_of):
        if len(candidate['events'])>=1000:
            candidate['capacityWarning']=True;return False
        event=dict(id=uuid.uuid4().hex,ruleId=rule['id'],kind=rule['kind'],created=moment,read=False,
                   title=title[:300],details=details[:3000],source=str(source or 'Not supplied')[:300],asOf=str(as_of or 'Not supplied')[:120])
        candidate['events'].append(event);new.append(event)
        return True
    for rule in candidate['rules']:
        if not rule['enabled']:continue
        runtime=rule['runtime']
        if rule['kind']=='price':
            data=lookup('quote',rule['symbol']+'|1d') or {}
            if not isinstance(data,dict) or data.get('demo') or data.get('stale') or data.get('currency')!='USD':continue
            try:price=number(data.get('price'),True)
            except ValueError:continue
            threshold=number(rule['threshold'],True)
            condition=price>=threshold if rule['direction']=='above' else price<=threshold
            if not condition:runtime['active']=False
            elif not runtime['active']:
                title=f"{rule['symbol']} meets your at-or-{rule['direction']} USD {threshold} threshold"
                if emit(rule,title,f'Observed public quote: USD {price}. Quotes may be delayed; this condition is not an instruction to trade.',data.get('source'),data.get('asOf')):
                    runtime['active']=True;runtime['sequence']+=1
        elif rule['kind']=='earnings':
            # Past events are no longer queried; retain only the current/recent deduplication window.
            runtime['seen']=[key for key in runtime['seen'] if key[:10]>=(today-timedelta(days=30)).isoformat()]
            for day in session_dates(today,rule['lookahead']):
                data=lookup('earnings',day) or {}
                if not isinstance(data,dict) or data.get('demo') or data.get('stale'):continue
                items=data.get('items',[])
                if not isinstance(items,list):continue
                for item in items:
                    if not isinstance(item,dict) or not isinstance(item.get('symbol'),str) or item['symbol'].upper()!=rule['symbol']:continue
                    key=day+':'+rule['symbol']
                    if key in runtime['seen']:continue
                    if emit(rule,f"{rule['symbol']} appears on the {day} earnings calendar",f"{item.get('reportTime','Time not supplied')}. Provider dates can change; check the company's investor-relations calendar. This is an event reminder, not an investment recommendation.",data.get('source'),data.get('asOf')):
                        runtime['seen'].append(key)
        else:
            bad=[row for row in feeds if row['status'] in ('empty','error','stale')]
            if not bad:
                if all(row['status']=='ready' for row in feeds):runtime.update(active=False,since=None)
                continue
            if runtime['since'] is None:runtime['since']=moment
            if (now-aware_time(runtime['since'])).total_seconds()<rule['delayMinutes']*60 or runtime['active']:continue
            details='\n'.join(str(row['kind'])+' '+str(row['key'])+': '+str(row['status'])+(' · '+str(row.get('error','')) if row.get('error') else '') for row in bad[:15])
            if emit(rule,f'{len(bad)} watched feeds are unavailable or stale',details+'\nOpen Market data health to inspect all feeds and retry.','Current subscribed feed health',moment):runtime['active']=True
    if len(candidate['events'])<1000:candidate['capacityWarning']=False
    validate_alerts(candidate)
    return candidate,new
