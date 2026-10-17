"""Versioned, portable CSV datasets with safe spreadsheet text cells."""
import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
from .finance import valid_month, shift_month
from .planning import valid_day
from .investments import position

DATASETS = {
    'cash_flow': 'Monthly income and bills', 'budgets': 'Monthly category budgets',
    'card_transactions': 'Imported card transactions', 'trades': 'Investment trades',
    'lots': 'Remaining FIFO lots as of end date', 'gains': 'Realized FIFO disposals',
    'balances': 'Account balance snapshots', 'goals': 'Goal progress snapshots',
    'valuations': 'Investment account valuations', 'funding_flows': 'Investment external cash flows',
    'dividends': 'Recorded dividend cash and classification',
}


def safe_cell(value):
    if isinstance(value, str) and value.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')):
        return "'"+value
    return value


def write_csv(path, headers, rows):
    """Atomic write: a failure never truncates an existing user export."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8-sig', newline='', dir=path.parent, suffix='.csv', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            writer = csv.writer(handle)
            writer.writerow(headers)
            for row in rows:
                writer.writerow([safe_cell(value) for value in row])
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return len(rows)


def rows_for(owner, dataset, start, end):
    valid_day(start)
    valid_day(end)
    if start > end or dataset not in DATASETS:
        raise ValueError('Choose a dataset and an ordered date range.')
    headers, rows = [], []
    finance = owner.finance_store
    if dataset in ('cash_flow', 'budgets'):
        month = valid_month(start[:7])
        months = []
        while month <= end[:7]:
            if len(months) >= 600:
                raise ValueError('Export at most 50 years at once.')
            months.append(month)
            month = shift_month(month, 1)
        if dataset == 'cash_flow':
            headers = ['period','name','kind','category','scheduled_usd','status','actual_usd']
            for month in months:
                for row in finance.entries(month):
                    rows.append([month,row['name'],row['kind'],row['category'],Decimal(row['amount_cents'])/100,
                                 'received' if row['posted'] and row['kind']=='income' else 'paid' if row['posted'] else 'scheduled',
                                 Decimal(row['amount_cents'])/100 if row['posted'] else 0])
        else:
            headers = ['period','category','target_usd','scheduled_usd','planned_usd','actual_usd','available_usd']
            for month in months:
                for category,row in finance.summary(month)['categories'].items():
                    rows.append([month,category,*[Decimal(row[key])/100 for key in ('target','scheduled','planned','actual')],
                                 Decimal(row['planned']-row['actual'])/100])
    elif dataset == 'card_transactions':
        headers = ['id','date','posting_date','description','category','kind','amount_usd','account','card_id','source_file','manual_override']
        rows = [[row['id'],row['date'],row['posting_date'],row['description'],row['category'] or '',row['kind'],
                 Decimal(row['amount_cents'])/100,row['account_key'],row['card_id'],row['filename'],row['manual_override']]
                for row in finance.transactions() if start<=row['date']<=end]
    elif dataset in ('trades','lots','gains'):
        trades = owner.investment_store.state['trades']
        if dataset == 'trades':
            headers = ['ticker','trade_id','date','side','shares','execution_price_usd','fees_usd','account_id','broker_transaction_id']
            rows = [[ticker,row['id'],row['date'],row['side'],row['quantity'],row['price'],row['fees'],row.get('accountId','legacy'),row.get('externalId','')]
                    for ticker,items in sorted(trades.items()) for row in items if start<=row['date']<=end]
        else:
            headers = ['ticker','lot_id','acquired','remaining_shares','remaining_basis_usd','account_id'] if dataset=='lots' else ['ticker','trade_id','lot_id','acquired','sold','shares','basis_usd','proceeds_usd','gain_usd','holding_period','account_id']
            for ticker,items in sorted(trades.items()):
                result = position([row for row in items if row['date']<=end])
                if dataset=='lots':
                    rows.extend([[ticker,row['id'],row['date'],row['quantity'],row['basis'],row['accountId']] for row in result['lots']])
                else:
                    rows.extend([[ticker,row['tradeId'],row['lotId'],row['acquired'],row['sold'],row['quantity'],row['basis'],row['proceeds'],row['gain'],'long' if row['longTerm'] else 'short',row['accountId']] for row in result['disposals'] if start<=row['sold']<=end])
    elif dataset=='dividends':
        headers=['event_id','account_id','ticker','paid','ex_dividend_date','currency','gross_ordinary','qualified_portion','withheld','classification','source','notes']
        rows=[[row['id'],row['accountId'],row['symbol'],row['paid'],row.get('exDate',''),row['currency'],Decimal(row['grossCents'])/100,Decimal(row['qualifiedCents'])/100,Decimal(row['withheldCents'])/100,row['classification'],row['source'],row['notes']]
              for row in owner.investment_store.state.get('dividendEvents',[]) if start<=row['paid']<=end]
    elif dataset in ('valuations','funding_flows'):
        kind='valuations' if dataset=='valuations' else 'flows'
        headers=['account_id','date','amount_usd','source'] if kind=='valuations' else ['account_id','flow_id','date','kind','amount_usd','source']
        records=owner.investment_store.state.get('performanceRecords',{'valuations':[],'flows':[]})
        rows=[[row['accountId'],row['date'],Decimal(row['amount']),row['source']] if kind=='valuations' else
              [row['accountId'],row['id'],row['date'],row['kind'],Decimal(row['amount']),row['source']]
              for row in records[kind] if start<=row['date']<=end]
    else:
        with owner.planning_store.connection() as db:
            from .shared_sync import source_for,source_label
            if dataset=='balances':
                headers = ['account_id','name','kind','owner','currency','as_of','amount','source']
                rows = [[row['id'],row['name'],row['kind'],row['owner'],row['currency'],row['date'],Decimal(row['amount_cents'])/100,source_label(source) if (source:=source_for(db,'manual_account',row['id'],row['date'],row['amount_cents'])) else 'manual']
                        for row in db.execute('SELECT a.*,s.date,s.amount_cents FROM financial_accounts a JOIN balance_snapshots s ON a.id=s.account_id WHERE s.date BETWEEN ? AND ? ORDER BY s.date,a.id',(start,end))]
            else:
                headers = ['goal_id','name','currency','target','target_date','progress_as_of','amount_set_aside','linked_account_id','category']
                rows = [[row['id'],row['name'],row['currency'],Decimal(row['target_cents'])/100,row['target_date'],row['date'],Decimal(row['saved_cents'])/100,row['account_id'] or '',row['category']]
                        for row in db.execute('SELECT g.*,s.date,s.saved_cents FROM savings_goals g JOIN goal_snapshots s ON g.id=s.goal_id WHERE s.date BETWEEN ? AND ? ORDER BY s.date,g.id',(start,end))]
    prefix = ['schema_version','dataset','range_start','range_end']
    return prefix+headers, [[1,dataset,start,end]+row for row in rows]
