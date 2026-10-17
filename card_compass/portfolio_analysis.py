"""Descriptive portfolio allocation; incomplete pricing never produces full weights."""
from datetime import date
from decimal import Decimal
from .investments import position, symbol, number
from .market_news import SECTORS
from .planning import valid_day

ASSET_TYPES={'unclassified':'Unclassified','equity':'Individual equity','etf':'ETF','fund':'Mutual fund',
             'bond':'Bond','cash':'Cash security','other':'Other security'}
PORTFOLIO_SECTORS={'unclassified':'Unclassified',**{k:v for k,v in SECTORS.items() if k not in ('all','macro')}}


def validate_classifications(values):
    if not isinstance(values,dict) or len(values)>1000:
        raise ValueError('Keep at most 1,000 security classifications.')
    for ticker,row in values.items():
        symbol(ticker)
        if not isinstance(row,dict) or row.get('assetType') not in ASSET_TYPES or row.get('sector') not in PORTFOLIO_SECTORS:
            raise ValueError('Choose a supported asset type and sector.')
        valid_day(row['asOf'],future=False)
        if row.get('source')!='manual':raise ValueError('Security classifications must identify their manual source.')


def allocation(state, quote_lookup, mode='market', account_id=None):
    if mode not in ('market','basis'):raise ValueError('Choose market value or remaining cost basis.')
    if account_id is not None and account_id not in {a['id'] for a in state['investmentAccounts']}:
        raise ValueError('Choose an existing investment account.')
    rows=[]
    for ticker,trades in sorted(state['trades'].items()):
        holding=position([row for row in trades if account_id is None or row.get('accountId','legacy')==account_id])
        if not holding['quantity']:continue
        quote=quote_lookup(ticker) or {}
        reason='Demo quote' if quote.get('demo') else 'Cached / expired quote' if quote.get('stale') else 'Unsupported quote currency' if quote and quote.get('currency')!='USD' else 'Quote unavailable' if not quote else ''
        try:
            price=number(quote.get('price'),True) if not reason else None
        except ValueError:
            price=None;reason='Invalid quote price'
        classification=state.get('securityClassifications',{}).get(ticker)
        value=holding['basis'] if mode=='basis' else holding['quantity']*price if price is not None else None
        rows.append(dict(ticker=ticker,quantity=holding['quantity'],basis=holding['basis'],value=value,weight=None,
                         assetType=classification['assetType'] if classification else 'unclassified',
                         sector=classification['sector'] if classification else 'unclassified',
                         classificationAsOf=classification['asOf'] if classification else '',
                         classificationStale=bool(classification and (date.today()-date.fromisoformat(classification['asOf'])).days>=365),
                         source='Recorded execution prices and fees' if mode=='basis' else quote.get('source','Not supplied'),
                         asOf='Recorded trade history' if mode=='basis' else quote.get('asOf','Not supplied'),
                         reason=reason if mode=='market' else ''))
    total=sum((row['value'] for row in rows if row['value'] is not None),Decimal(0))
    missing=[row for row in rows if row['value'] is None]
    complete=not missing
    weights_available=bool(rows and complete and total>0)
    groups={'security':{},'assetType':{},'sector':{}}
    if weights_available:
        for row in rows:row['weight']=row['value']/total
    for row in rows:
        if row['value'] is None:continue
        for name,key in [('security',row['ticker']),('assetType',row['assetType']),('sector',row['sector'])]:
            groups[name][key]=groups[name].get(key,Decimal(0))+row['value']
    sorted_weights=sorted((row['weight'] for row in rows if row['weight'] is not None),reverse=True)
    return dict(mode=mode,rows=rows,total=total,missing=missing,complete=complete,weightsAvailable=weights_available,
                groups=groups,largest=sorted_weights[0] if sorted_weights else None,
                topFive=sum(sorted_weights[:5],Decimal(0)) if sorted_weights else None,
                hhi=sum((weight*weight for weight in sorted_weights),Decimal(0)) if sorted_weights else None,
                unclassifiedAsset=groups['assetType'].get('unclassified',Decimal(0)),
                unclassifiedSector=groups['sector'].get('unclassified',Decimal(0)))
