"""Monthly account risk from funding-aware returns; no inferred price portfolio."""
import calendar
import csv
from datetime import date,datetime,timezone
from decimal import Decimal,InvalidOperation
import math
from pathlib import Path
import re
from statistics import mean,stdev
import uuid
from .finance import valid_month,shift_month
from .performance import calculate,calculate_all,validate_records

FACTOR_COLUMNS={'market_excess':'market_excess_percent','rf':'rf_percent','smb':'smb_percent','hml':'hml_percent'}


def month_end(month):
    valid_month(month);year,number=map(int,month.split('-'))
    return date(year,number,calendar.monthrange(year,number)[1]).isoformat()


def finite(value):
    try:
        if type(value) is bool:raise ValueError()
        result=Decimal(str(value))
        if not result.is_finite() or not math.isfinite(float(result)):raise ValueError()
        return result
    except (ValueError,InvalidOperation,OverflowError) as exc:raise ValueError('Use finite numeric returns.') from exc


def monthly_series(state,account_id,start_month,end_month):
    valid_month(start_month);valid_month(end_month)
    if start_month>end_month or end_month>=date.today().strftime('%Y-%m'):
        raise ValueError('Choose ordered, completed calendar months; partial/future months are excluded.')
    accounts={r['id'] for r in state['investmentAccounts']}
    if account_id is not None and account_id not in accounts:raise ValueError('Choose an existing investment account.')
    records=state['performanceRecords'];validate_records(records,accounts)
    periods=[];month=start_month
    while month<=end_month:
        if len(periods)>=120:raise ValueError('Analyze at most 120 completed months.')
        start,end=month_end(shift_month(month,-1)),month_end(month)
        result=calculate_all(state,start,end) if account_id is None else calculate(records,account_id,start,end)
        if result['twr'] is None:raise ValueError(month+': '+result['twrError'])
        periods.append(dict(period=month,returnRate=result['twr'],start=start,end=end))
        month=shift_month(month,1)
    relevant={account_id} if account_id is not None else accounts
    start=periods[0]['start'];end=periods[-1]['end']
    sources=sorted({r['source'] for key in ('valuations','flows') for r in records[key] if r['accountId'] in relevant and start<=r['date']<=end})
    return dict(accountId=account_id,start=start,end=end,periods=periods,sources=sources,
        method='Whole-account monthly TWR with end-of-day external flows; exact calendar month-end and every flow-date valuation required. No interpolation or missing-account carry-forward.')


def validate_factor_set(dataset):
    if not isinstance(dataset,dict) or dataset.get('version')!=1 or dataset.get('frequency')!='monthly' or dataset.get('currency')!='USD':raise ValueError('Use a version 1 monthly USD factor dataset.')
    if not isinstance(dataset.get('id'),str) or not re.fullmatch('[a-f0-9]{32}',dataset['id']):raise ValueError('Invalid factor dataset identity.')
    for key,limit in (('name',120),('source',250),('file',250)):
        if not isinstance(dataset.get(key),str) or not 1<=len(dataset[key].strip())<=limit:raise ValueError('Identify factor name/source/file within supported limits.')
    try:
        timestamp=datetime.fromisoformat(dataset.get('importedAt',''))
        if timestamp.tzinfo is None:raise ValueError()
    except (ValueError,TypeError) as exc:raise ValueError('Factor import timestamp must include a timezone.') from exc
    rows=dataset.get('rows')
    if not isinstance(rows,list) or not 1<=len(rows)<=1200:raise ValueError('Use 1–1200 monthly factor rows.')
    seen=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Invalid factor row.')
        month=valid_month(row.get('period'))
        if month>=date.today().strftime('%Y-%m') or month in seen:raise ValueError('Use unique, completed factor months.')
        seen.add(month)
        values={key:finite(row.get(key)) for key in FACTOR_COLUMNS}
        if any(abs(v)>10 for v in values.values()):raise ValueError('Factor return exceeds the supported magnitude; check decimal/percent units.')
        if values['rf']<=-1 or values['rf']+values['market_excess']<-1:raise ValueError('Risk-free/market total return is outside supported bounds.')
    return dataset


def validate_factor_sets(datasets):
    if not isinstance(datasets,list) or len(datasets)>10:raise ValueError('Keep at most ten saved factor datasets.')
    ids=set()
    for dataset in datasets:
        validate_factor_set(dataset)
        if dataset['id'] in ids:raise ValueError('Duplicate factor dataset identity.')
        ids.add(dataset['id'])


def parse_factors(path,name,source):
    path=Path(path)
    if path.stat().st_size>10_000_000:raise ValueError('Factor CSV exceeds 10 MB.')
    with path.open(encoding='utf-8-sig',newline='') as handle:
        reader=csv.DictReader(handle)
        fields=reader.fieldnames or []
        if len(fields)!=len(set(fields)) or not {'period',*FACTOR_COLUMNS.values()}<=set(fields):
            raise ValueError('Use period, market_excess_percent, rf_percent, smb_percent, hml_percent headers. Normalize vendor files before importing.')
        rows=[]
        for line,raw in enumerate(reader,2):
            if len(rows)>=1200:raise ValueError('Factor CSV exceeds 1200 monthly rows.')
            if None in raw or any(raw.get(key) is None for key in ('period',*FACTOR_COLUMNS.values())):raise ValueError(f'Invalid CSV column count on line {line}.')
            values={key:finite(raw[column].strip()) for key,column in FACTOR_COLUMNS.items()}
            if any(v in (Decimal('-99.99'),Decimal('-999')) for v in values.values()):
                raise ValueError(f'Known missing-data sentinel on line {line}; do not interpret it as a return.')
            rows.append(dict(period=raw['period'].strip(),**{key:str(value/100) for key,value in values.items()}))
    dataset=dict(version=1,id=uuid.uuid4().hex,frequency='monthly',currency='USD',name=name.strip(),source=source.strip(),
        file=path.name,importedAt=datetime.now(timezone.utc).isoformat(),rows=sorted(rows,key=lambda row:row['period']))
    return validate_factor_set(dataset)


def risk_summary(series,annual_rf=None,factors=None):
    periods=series['periods']
    if not periods:raise ValueError('No return periods to analyze.')
    returns=[finite(row['returnRate']) for row in periods]
    if any(v<-1 for v in returns):raise ValueError('Return below -100% is unsupported; inspect valuations/flows.')
    for prior,current in zip(periods,periods[1:]):
        if current['period']!=shift_month(prior['period'],1):raise ValueError('Risk periods must be adjacent calendar months.')
    wealth=peak=Decimal(1);drawdown=Decimal(0);curve=[]
    for row,rate in zip(periods,returns):
        wealth*=1+rate;peak=max(peak,wealth);loss=wealth/peak-1
        drawdown=min(drawdown,loss)
        curve.append(dict(period=row['period'],returnRate=rate,index=wealth*100,drawdown=loss))
    result=dict(count=len(returns),start=series['start'],end=series['end'],cumulative=wealth-1,maxDrawdown=drawdown,
        worstMonth=min(returns),annualVolatility=None,sharpe=None,sharpeSource='',factorModel=None,
        factorError='',warnings=[],curve=curve)
    numeric=[float(v) for v in returns]
    if len(returns)<12:result['warnings'].append('At least 12 completed monthly observations are required for annualized volatility/Sharpe.')
    else:result['annualVolatility']=stdev(numeric)*math.sqrt(12)
    rf_rates=None
    if factors is not None:
        validate_factor_set(factors)
        lookup={row['period']:row for row in factors['rows']}
        missing=[row['period'] for row in periods if row['period'] not in lookup]
        if missing:result['factorError']='Missing factor periods: '+', '.join(missing[:12])+('…' if len(missing)>12 else '')
        else:
            rf_rates=[float(finite(lookup[row['period']]['rf'])) for row in periods]
            result['sharpeSource']='Imported monthly RF · '+factors['source']
            if len(returns)<36:result['factorError']='Three-factor regression requires at least 36 aligned completed months.'
            else:
                from numpy import asarray
                from scipy.linalg import lstsq,LinAlgError
                matrix=[[1.0]+[float(finite(lookup[row['period']][key])) for key in ('market_excess','smb','hml')] for row in periods]
                target=[value-rf for value,rf in zip(numeric,rf_rates)]
                try:
                    coefficients,residual,rank,singular=lstsq(asarray(matrix,dtype=float),asarray(target,dtype=float),check_finite=True,lapack_driver='gelsd')
                except (LinAlgError,ValueError):
                    rank=0;singular=[0.0]
                    result['factorError']='Factor fit did not converge; inspect input data before retrying.'
                condition=float(singular[0]/singular[-1]) if singular[-1]>0 else float('inf')
                if rank!=4 or not math.isfinite(condition) or condition>1e8:
                    result['factorError']=result['factorError'] or 'Factors are constant/collinear or poorly conditioned; no reliable coefficient fit is reported.'
                else:
                    fitted=[sum(float(c)*x for c,x in zip(coefficients,row)) for row in matrix]
                    average=mean(target)
                    squared=sum((a-b)**2 for a,b in zip(target,fitted));total=sum((a-average)**2 for a in target)
                    result['factorModel']=dict(interceptMonthly=float(coefficients[0]),marketBeta=float(coefficients[1]),
                        smb=float(coefficients[2]),hml=float(coefficients[3]),rSquared=None if total==0 else max(0,min(1,1-squared/total)),
                        condition=condition,count=len(target),source=factors['source'],file=factors['file'],importedAt=factors['importedAt'])
    if rf_rates is None and annual_rf is not None:
        assumption=finite(annual_rf)
        if not Decimal('-1')<assumption<=Decimal(1):raise ValueError('Use an effective annual risk-free assumption above -100% and at most 100%.')
        rf_rates=[float((1+float(assumption))**(1/12)-1)]*len(numeric)
        result['sharpeSource']='Explicit user-entered effective annual risk-free assumption'
    if rf_rates is not None and len(returns)>=12:
        excess=[value-rf for value,rf in zip(numeric,rf_rates)];deviation=stdev(excess)
        if deviation>1e-15:result['sharpe']=mean(excess)/deviation*math.sqrt(12)
        else:result['warnings'].append('Sharpe is undefined for effectively zero excess-return variability.')
    elif rf_rates is None:result['warnings'].append('Sharpe needs an explicit risk-free assumption or fully aligned imported RF observations; zero is never silently assumed.')
    result['warnings'].append('Monthly observations omit intramonth drawdowns. Annualization uses the square-root-of-12 convention, not a risk forecast. Recorded TWR depends on complete valuations/flows. Factor fits are sample sensitivities, not causal attribution, future alpha or trading guidance.')
    return result


def correlations(series_by_account):
    if not 2<=len(series_by_account)<=20:raise ValueError('Compare between two and twenty recorded accounts.')
    labels=list(series_by_account);periods=[row['period'] for row in series_by_account[labels[0]]['periods']]
    if len(periods)<12:raise ValueError('Account correlations require at least twelve aligned completed months.')
    values={}
    for label,series in series_by_account.items():
        if [row['period'] for row in series['periods']]!=periods:raise ValueError('Correlation periods must match exactly; missing months are not dropped.')
        values[label]=[float(finite(row['returnRate'])) for row in series['periods']]
    averages={key:mean(value) for key,value in values.items()}
    deviations={key:stdev(value) for key,value in values.items()}
    result={}
    for left in labels:
        result[left]={}
        for right in labels:
            x,y=values[left],values[right];sx,sy=deviations[left],deviations[right]
            result[left][right]=None if sx<=1e-15 or sy<=1e-15 else max(-1,min(1,sum((a-averages[left])*(b-averages[right]) for a,b in zip(x,y))/((len(x)-1)*sx*sy)))
    return dict(periods=periods,count=len(periods),matrix=result)
