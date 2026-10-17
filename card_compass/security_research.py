"""Saved selected-security comparisons and covered-field screening, without signals."""
from decimal import Decimal,InvalidOperation
from .investments import symbol,METRICS

PERCENT_FIELDS={'dividendYield','profitMargins','operatingMargins','returnOnEquity','revenueGrowth','earningsGrowth','debtToEquity'}
MONEY_FIELDS={'trailingEps','forwardEps','marketCap','enterpriseValue','totalRevenue','freeCashflow'}
FINANCIAL_CURRENCY_FIELDS={'totalRevenue','freeCashflow'}
RESEARCH_CURRENCIES=('USD','CAD','EUR','GBP','AUD','CHF','JPY','CNY','HKD')


def threshold(value):
    if value is None or value=='':return None
    try:
        number=Decimal(str(value))
        if not number.is_finite() or abs(number)>Decimal('1e18'):raise ValueError('Use a finite filter bound of at most 1 quintillion.')
        return number
    except InvalidOperation:raise ValueError('Use a numeric filter bound.') from None


def validate_config(config):
    if not isinstance(config,dict):raise ValueError('Invalid research view.')
    tickers=config.get('symbols')
    if not isinstance(tickers,list) or not 1<=len(tickers)<=30 or len(set(tickers))!=len(tickers):raise ValueError('Compare between 1 and 30 distinct tickers.')
    for ticker in tickers:
        if symbol(ticker)!=ticker:raise ValueError('Save uppercase tickers.')
    fields=config.get('fields')
    if not isinstance(fields,list) or not fields or len(set(fields))!=len(fields) or any(key not in METRICS for key in fields):raise ValueError('Select distinct supported fundamental fields.')
    field=config.get('filterField')
    if field is not None and field not in METRICS:raise ValueError('Choose a supported filter field.')
    low,high=threshold(config.get('minimum')),threshold(config.get('maximum'))
    if low is not None and high is not None and low>high:raise ValueError('Minimum cannot exceed maximum.')
    if field is None and (low is not None or high is not None):raise ValueError('Choose a filter field before entering bounds.')
    for key in ('requireComplete','includeMissing','includeCachedDemo'):
        if type(config.get(key)) is not bool:raise ValueError('Invalid coverage filter.')
    if config.get('currency') not in RESEARCH_CURRENCIES:raise ValueError('Choose a filter currency.')
    return config


def validate_views(views):
    if not isinstance(views,list) or len(views)>50:raise ValueError('Keep at most 50 saved research views.')
    ids=set()
    for view in views:
        if not isinstance(view,dict):raise ValueError('Invalid saved comparison.')
        ident=view.get('id')
        if not isinstance(ident,str) or not 1<=len(ident)<=80 or ident in ids:raise ValueError('Saved views need distinct IDs.')
        ids.add(ident)
        if not isinstance(view.get('name'),str) or not 1<=len(view['name'].strip())<=80:raise ValueError('Name the research view (up to 80 characters).')
        validate_config(view['config'])


def field_value(data,key):
    values=data.get('values',{})
    raw=values.get(key) if isinstance(values,dict) else None
    value=None
    if not isinstance(raw,bool) and raw is not None:
        try:value=threshold(raw)
        except ValueError:pass
    currency=data.get('financialCurrency' if key in FINANCIAL_CURRENCY_FIELDS else 'currency','') if key in MONEY_FIELDS else None
    return value,currency


def display_field(value,currency,key):
    if value is None:return 'Unavailable'
    if key in MONEY_FIELDS:return (currency or 'Currency unavailable')+f' {value:,.2f}'
    if key in PERCENT_FIELDS:return f'{value*100:.2f}%'
    return f'{value:,.3f}'.rstrip('0').rstrip('.')


def compare(config,lookup):
    validate_config(config)
    rows=[];excluded=[]
    low,high=threshold(config.get('minimum')),threshold(config.get('maximum'))
    for ticker in config['symbols']:
        data=lookup(ticker) or {}
        if not isinstance(data,dict):data={}
        mode='DEMO' if data.get('demo') else 'CACHED' if data.get('stale') else 'PUBLIC' if data else 'NOT LOADED'
        values={key:field_value(data,key) for key in config['fields']}
        comparable={key:value if key not in MONEY_FIELDS or currency==config['currency'] else None for key,(value,currency) in values.items()}
        available=sum(value is not None for value,currency in values.values())
        covered=sum(value is not None for value in comparable.values())
        reason=''
        if mode in ('DEMO','CACHED') and not config['includeCachedDemo']:reason=mode+' excluded by source filter'
        elif config['requireComplete'] and covered<len(config['fields']):reason='Missing fields or incompatible currency'
        field=config['filterField']
        if not reason and field is not None:
            value,currency=field_value(data,field)
            if field in MONEY_FIELDS and currency!=config['currency']:value=None
            if value is None:
                if not config['includeMissing']:reason='Filter field unavailable / currency mismatch'
            elif (low is not None and value<low) or (high is not None and value>high):reason='Outside filter bounds'
        row=dict(ticker=ticker,mode=mode,source=data.get('source','Not supplied'),asOf=data.get('asOf','Not supplied'),
                 values=values,available=available,comparable=covered,fieldCount=len(config['fields']),
                 formatted={key:display_field(value,currency,key) for key,(value,currency) in values.items()})
        if reason:excluded.append(dict(ticker=ticker,reason=reason,mode=mode))
        else:rows.append(row)
    return dict(rows=rows,excluded=excluded)
