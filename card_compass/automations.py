"""Local, opt-in report schedules and reviewed finance conditions.

No OS service or network delivery. Each UTC schedule slot is claimed once in
SQLite before side effects. Interrupted runs are visible and never auto-retried.
"""
import calendar
from datetime import date,datetime,time,timedelta,timezone
import json
from pathlib import Path
import re
import uuid
from .ledger import Ledger
from .reports import DATASETS,rows_for,write_csv
from .finance import shift_month

KINDS={'report':'Scheduled CSV report','budget':'Budget threshold','goal':'Goal progress threshold','stale_balance':'Balance review reminder'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS automation_jobs(id TEXT PRIMARY KEY,settings TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS automation_runs(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,
 slot TEXT NOT NULL,started TEXT NOT NULL,finished TEXT NOT NULL,status TEXT NOT NULL,
 message TEXT NOT NULL,path TEXT NOT NULL,details TEXT NOT NULL,UNIQUE(job_id,slot));
'''


def utc(value):
    if not isinstance(value,datetime) or value.tzinfo is None:raise ValueError('Use a timezone-aware check time.')
    return value.astimezone(timezone.utc)


def stamp(value):return utc(value).isoformat(timespec='seconds')


def validate_job(job):
    if not isinstance(job,dict) or job.get('version')!=1:raise ValueError('Invalid automation version.')
    if not isinstance(job.get('id'),str) or not re.fullmatch(r'[a-f0-9]{32}',job['id']):raise ValueError('Invalid automation ID.')
    if job.get('kind') not in KINDS or type(job.get('enabled')) is not bool:raise ValueError('Choose a valid automation and enabled state.')
    if not isinstance(job.get('name'),str) or not 1<=len(job['name'].strip())<=120:raise ValueError('Name the automation (up to 120 characters).')
    activated=datetime.fromisoformat(job['activatedAt']);utc(activated)
    if job.get('frequency') not in ('daily','weekly','monthly'):raise ValueError('Choose a daily, weekly or monthly schedule.')
    for key,low,high in [('hour',0,23),('minute',0,59),('weekday',0,6),('monthDay',1,28),('threshold',1,1000),('maxAgeDays',1,3650)]:
        if type(job.get(key)) is not int or not low<=job[key]<=high:raise ValueError('Invalid schedule or rule threshold.')
    if job.get('dataset') not in DATASETS or job.get('range') not in ('previous_month','trailing_30','year_to_date'):raise ValueError('Choose a recorded-data dataset and range.')
    if not isinstance(job.get('directory'),str) or len(job['directory'])>4096:raise ValueError('Invalid export folder.')
    if job['kind']=='report' and not Path(job['directory']).is_absolute():raise ValueError('Choose an absolute export folder.')
    if not isinstance(job.get('target'),str) or len(job['target'])>120:raise ValueError('Invalid rule target.')
    if job['kind'] in ('goal','stale_balance') and (not job['target'].isdigit() or int(job['target'])<=0):raise ValueError('Choose an existing goal or account.')
    if job['kind']=='budget' and not job['target'].strip():raise ValueError('Choose a budget category.')
    return job


def latest_slot(job,now):
    """Most recent due slot after explicit activation; one catch-up, no backlog."""
    validate_job(job);now=utc(now)
    due=datetime.combine(now.date(),time(job['hour'],job['minute']),timezone.utc)
    if due>now:due-=timedelta(days=1)
    if job['frequency']=='weekly':due-=timedelta(days=(due.weekday()-job['weekday'])%7)
    elif job['frequency']=='monthly':
        day=date(now.year,now.month,job['monthDay'])
        due=datetime.combine(day,time(job['hour'],job['minute']),timezone.utc)
        if due>now:
            month=shift_month(now.strftime('%Y-%m'),-1)
            due=datetime.fromisoformat(month+f"-{job['monthDay']:02d}T{job['hour']:02d}:{job['minute']:02d}:00+00:00")
    return due if due>=utc(datetime.fromisoformat(job['activatedAt'])) else None


def report_range(kind,today):
    if kind=='previous_month':
        month=shift_month(today.strftime('%Y-%m'),-1);year,number=map(int,month.split('-'))
        return month+'-01',month+f'-{calendar.monthrange(year,number)[1]:02d}'
    if kind=='trailing_30':return (today-timedelta(days=29)).isoformat(),today.isoformat()
    if kind=='year_to_date':return date(today.year,1,1).isoformat(),today.isoformat()
    raise ValueError('Invalid report range.')


def reviewed_directory(directory,profile):
    folder=Path(directory).resolve(strict=True);profile=Path(profile).resolve()
    if not folder.is_dir():raise ValueError('The selected export folder is unavailable.')
    if folder==profile or profile in folder.parents:raise ValueError('Choose an export folder outside the active profile.')
    return folder


def evaluate_rule(owner,job,today):
    """An observed record condition, not a forecast or inferred bank balance."""
    if job['kind']=='budget':
        category=owner.finance_store.summary(today.strftime('%Y-%m'))['categories'].get(job['target'])
        if not category or category['planned']<=0:raise ValueError('This category has no positive monthly plan; review its budget.')
        triggered=category['actual']*100>=category['planned']*job['threshold']
        details=dict(month=today.strftime('%Y-%m'),category=job['target'],actualCents=category['actual'],plannedCents=category['planned'],thresholdPercent=job['threshold'])
        return triggered,f"Recorded {job['target']} spending reached {job['threshold']}% of its monthly plan." if triggered else 'Recorded spending is below the selected threshold.',details
    if job['kind']=='goal':
        goal=next((r for r in owner.planning_store.goals(today.isoformat()) if str(r['id'])==job['target']),None)
        if not goal or not goal['as_of']:raise ValueError('Goal or its dated progress is missing; review the goal.')
        if (today-date.fromisoformat(goal['as_of'])).days>job['maxAgeDays']:raise ValueError('Goal progress is older than the reviewed age limit; record a fresh value.')
        triggered=goal['saved_cents']*100>=goal['target_cents']*job['threshold']
        details=dict(goalId=goal['id'],asOf=goal['as_of'],currency=goal['currency'],savedCents=goal['saved_cents'],targetCents=goal['target_cents'],thresholdPercent=job['threshold'])
        return triggered,f"Recorded {goal['name']} savings reached {job['threshold']}% of its target." if triggered else 'Recorded goal progress is below the selected threshold.',details
    account=next((r for r in owner.planning_store.accounts(today.isoformat()) if str(r['id'])==job['target']),None)
    if not account:raise ValueError('The selected account was removed; review this reminder.')
    age=(today-date.fromisoformat(account['as_of'])).days if account['as_of'] else None
    triggered=age is None or age>job['maxAgeDays']
    return triggered,('Review this account balance: no dated value or the review age limit was exceeded.' if triggered else 'Recorded balance is within the selected review age.'),dict(accountId=account['id'],asOf=account['as_of'],ageDays=age,maxAgeDays=job['maxAgeDays'])


class AutomationStore(Ledger):
    def __init__(self,directory):
        super().__init__(directory)
        with self.connection() as db:db.executescript(SCHEMA)

    def jobs(self):
        with self.connection() as db:
            rows=[validate_job(json.loads(r[0])) for r in db.execute('SELECT settings FROM automation_jobs ORDER BY id')]
        if len(rows)>100:raise ValueError('Keep at most 100 automations.')
        return rows

    def save(self,job):
        validate_job(job)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM automation_jobs WHERE id=?',(job['id'],)).fetchone() and db.execute('SELECT COUNT(*) FROM automation_jobs').fetchone()[0]>=100:raise ValueError('Automation capacity reached; remove an unused job first.')
            db.execute('INSERT OR REPLACE INTO automation_jobs VALUES (?,?)',(job['id'],json.dumps(job)))

    def remove(self,ident):
        with self.connection() as db:db.execute('DELETE FROM automation_jobs WHERE id=?',(ident,))

    def history(self):
        with self.connection() as db:return [dict(r) for r in db.execute('SELECT * FROM automation_runs ORDER BY rowid DESC')]

    def clear_finished(self):
        with self.connection() as db:db.execute("DELETE FROM automation_runs WHERE status!='running'")

    def claim(self,job,slot,now,manual=False):
        ident=uuid.uuid4().hex
        key='manual:'+ident if manual else stamp(slot)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM automation_runs WHERE job_id=? AND slot=?',(job['id'],key)).fetchone():return None
            if db.execute('SELECT COUNT(*) FROM automation_runs').fetchone()[0]>=1000:raise ValueError('Run history is full; explicitly clear finished history before running more jobs.')
            db.execute('INSERT INTO automation_runs VALUES (?,?,?,?,?,?,?,?,?)',(ident,job['id'],key,stamp(now),'','running','Claimed; result not yet confirmed.','','{}'))
        return ident

    def finish(self,ident,status,message,now,path='',details=None):
        if status not in ('completed','triggered','no_match','failed','interrupted'):raise ValueError('Invalid automation result.')
        with self.connection() as db:db.execute("UPDATE automation_runs SET status=?,message=?,finished=?,path=?,details=? WHERE id=? AND status='running'",(status,message[:2000],stamp(now),path,json.dumps(details or {}),ident))

    def recover_interrupted(self,now):
        with self.connection() as db:db.execute("UPDATE automation_runs SET status='interrupted',finished=?,message='The previous app session ended before completion was confirmed. Inspect any output before using Run now; no automatic retry.' WHERE status='running'",(stamp(now),))


def execute(owner,store,job,now,manual=False):
    validate_job(job);now=utc(now)
    slot=now if manual else latest_slot(job,now)
    if slot is None or (not manual and not job['enabled']):return None
    ident=store.claim(job,slot,now,manual)
    if ident is None:return None
    try:
        # Finance reporting uses the device's calendar date, explicitly shown in
        # run details; schedule due times are always UTC, unaffected by DST.
        today=now.astimezone().date()
        if job['kind']=='report':
            folder=reviewed_directory(job['directory'],owner.directory)
            start,end=report_range(job['range'],today)
            headers,rows=rows_for(owner,job['dataset'],start,end)
            path=folder/f"finance-compass-{job['dataset']}-{ident}.csv"
            if path.exists():raise ValueError('The output path already exists; no file was replaced.')
            count=write_csv(path,headers,rows)
            store.finish(ident,'completed',f'Exported {count} recorded rows.',now,str(path),dict(dataset=job['dataset'],start=start,end=end,rows=count,jobName=job['name'],schemaVersion=1))
        else:
            matched,message,details=evaluate_rule(owner,job,today)
            # Persist continuous-condition latch in history: a no-match rearms;
            # an unavailable/failed check does not claim recovery.
            previous=next((r for r in store.history() if r['job_id']==job['id'] and r['id']!=ident and r['status'] in ('triggered','no_match','completed') and json.loads(r['details']).get('activation')==job['activatedAt']),None)
            status='triggered' if matched and (not previous or previous['status']=='no_match') else 'completed' if matched else 'no_match'
            if status=='completed':message='Condition remains active; no repeated announcement. '+message
            details.update(jobName=job['name'],activation=job['activatedAt']);store.finish(ident,status,message,now,details=details)
    except (ValueError,OSError,KeyError,TypeError) as exc:store.finish(ident,'failed',str(exc),now,details=dict(jobName=job['name']))
    return ident


def validate_table(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    present={'automation_jobs','automation_runs'}&tables
    if not present:return
    if len(present)!=2:raise ValueError('Incomplete automation tables.')
    jobs=list(db.execute('SELECT id,settings FROM automation_jobs'))
    if len(jobs)>100:raise ValueError('Too many automations.')
    for row in jobs:
        job=validate_job(json.loads(row[1]))
        if row[0]!=job['id']:raise ValueError('Automation identity mismatch.')
    cursor=db.execute('SELECT * FROM automation_runs');columns=[r[0] for r in cursor.description];runs=list(cursor)
    if len(runs)>1000:raise ValueError('Too many automation run records.')
    for row in runs:
        record=dict(zip(columns,row))
        for key in ('id','job_id'):
            if not re.fullmatch(r'[a-f0-9]{32}',record[key]):raise ValueError('Invalid run identity.')
        if record['status'] not in ('running','completed','triggered','no_match','failed','interrupted'):raise ValueError('Invalid run status.')
        utc(datetime.fromisoformat(record['started']))
        if record['finished']:utc(datetime.fromisoformat(record['finished']))
        for key,maximum in [('slot',100),('message',2000),('path',4096),('details',20000)]:
            if not isinstance(record[key],str) or len(record[key])>maximum:raise ValueError('Invalid run metadata.')
        if not isinstance(json.loads(record['details']),dict):raise ValueError('Invalid run details.')


def pause_restored_jobs(db):
    """A restored backup never renews filesystem consent automatically."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='automation_jobs'").fetchone():return
    for row in list(db.execute('SELECT id,settings FROM automation_jobs')):
        job=validate_job(json.loads(row[1]));job['enabled']=False
        db.execute('UPDATE automation_jobs SET settings=? WHERE id=?',(json.dumps(job),row[0]))
