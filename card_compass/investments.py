"""Native investment workspace and Decimal FIFO accounting, ported from Folio."""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import math
import re
import uuid
from .catalog import read_json, write_json

WIDGETS = {'watchlist': 'Watchlist', 'trades': 'Trade history', 'chart': 'Price chart',
           'quote': 'Key statistics', 'metrics': 'Advanced metrics', 'analyst': 'Analyst trend',
           'movers': 'Daily movers', 'market': 'Market overview', 'news': 'News',
           'earnings': 'Earnings calendar', 'notes': 'Research notes',
           'positions': 'Watchlists and positions', 'ledger': 'Trade history', 'headlines': 'Market headlines'}
METRICS = {'trailingEps': 'EPS (TTM)', 'forwardEps': 'Forward EPS', 'trailingPE': 'P/E (TTM)',
           'forwardPE': 'Forward P/E', 'pegRatio': 'PEG', 'priceToBook': 'Price / book',
           'priceToSales': 'Price / sales', 'marketCap': 'Market cap', 'enterpriseValue': 'Enterprise value',
           'enterpriseToEbitda': 'EV / EBITDA', 'dividendYield': 'Dividend yield', 'beta': 'Beta',
           'profitMargins': 'Profit margin', 'operatingMargins': 'Operating margin',
           'returnOnEquity': 'Return on equity', 'revenueGrowth': 'Revenue growth',
           'earningsGrowth': 'Earnings growth', 'totalRevenue': 'Revenue (TTM)',
           'freeCashflow': 'Free cash flow', 'debtToEquity': 'Debt / equity'}
RANGES = ('1d', '1mo', '3mo', '1y', '5y')
VIEW_COLUMNS = {
    'watchlist': ['Ticker', 'Company', 'Price', 'Day %', 'Shares', 'Average cost', 'Remaining basis'],
    'trades': ['Date', 'Ticker', 'Side', 'Shares', 'Price', 'Fees', 'Account'],
    'news': ['Headline', 'Published', 'Sector tags'],
}

INVESTMENT_ACCOUNT_TYPES = {'unknown': 'Unassigned / tax treatment unknown', 'brokerage': 'Taxable brokerage',
                            'ira': 'Traditional IRA', 'roth': 'Roth IRA', '401k': '401(k)',
                            'hsa': 'HSA', 'other': 'Other / review tax treatment'}


def legacy_account():
    return dict(id='legacy', name='Unassigned historical trades', kind='unknown', currency='USD', owner='')


def validate_accounts(accounts):
    if not isinstance(accounts, list) or not 1 <= len(accounts) <= 100:
        raise ValueError('Keep between 1 and 100 investment accounts.')
    ids = set()
    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError('Invalid investment account.')
        ident = account.get('id')
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ident) or ident in ids:
            raise ValueError('Investment accounts need unique IDs.')
        ids.add(ident)
        if account.get('kind') not in INVESTMENT_ACCOUNT_TYPES or account.get('currency') != 'USD':
            raise ValueError('Investment trades currently support USD accounts only.')
        for key in ('name', 'owner'):
            value = account.get(key)
            if not isinstance(value, str) or len(value) > 120 or (key == 'name' and not value.strip()):
                raise ValueError('Use an account name and owner of at most 120 characters.')
    if 'legacy' not in ids:
        raise ValueError('Preserve the unassigned historical-trades account.')
    return ids


def validate_broker_history(history, accounts):
    if not isinstance(history,list) or len(history)>200:
        raise ValueError('Invalid broker import history.')
    ids=set()
    for record in history:
        ident=record.get('id') if isinstance(record,dict) else None
        if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{32}',ident) or ident in ids:
            raise ValueError('Invalid broker import record.')
        ids.add(ident)
        moment=datetime.fromisoformat(record['importedAt'])
        if moment.tzinfo is None:raise ValueError('Import timestamps need a time zone.')
        if not isinstance(record['files'],list) or len(record['files'])>2 or any(not isinstance(name,str) or len(name)>256 for name in record['files']):
            raise ValueError('Invalid import source metadata.')
        for key in ('added','skipped','possibleDuplicates'):
            if type(record[key]) is not int or not 0<=record[key]<=10000:raise ValueError('Invalid import counts.')
        for key in ('duplicatesAcknowledged','variancesAcknowledged'):
            if type(record[key]) is not bool:raise ValueError('Invalid import review state.')
        rows=record['reconciliation']
        if not isinstance(rows,list) or len(rows)>10000:raise ValueError('Invalid position reconciliation.')
        for row in rows:
            if row['accountId'] not in accounts:raise ValueError('Reconciliation refers to a missing account.')
            symbol(row['ticker'])
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',row['as_of']):raise ValueError('Invalid snapshot date.')
            date.fromisoformat(row['as_of'])
            actual,expected=number(row['recorded']),number(row['broker'])
            if Decimal(str(row['difference']))!=actual-expected:raise ValueError('Invalid reconciliation difference.')


def validate_view_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError('Invalid view settings.')
    metrics = settings.get('metrics', [True, True, True])
    if not isinstance(metrics, list) or len(metrics) != 3 or any(type(v) is not bool for v in metrics):
        raise ValueError('Invalid summary tile selection.')
    height = settings.get('rowHeight', 58)
    if type(height) is not int or not 24 <= height <= 96:
        raise ValueError('Row height must be between 24 and 96.')
    for group in ('columns', 'order'):
        values = settings.get(group, {})
        if not isinstance(values, dict):
            raise ValueError('Invalid table settings.')
        for name, selected in values.items():
            if name not in VIEW_COLUMNS or not isinstance(selected, list) or not selected or any(type(v) is not int or not 0 <= v < len(VIEW_COLUMNS[name]) for v in selected) or len(set(selected)) != len(selected):
                raise ValueError('Select at least one valid column for each table.')
            if group == 'order' and len(selected) != len(VIEW_COLUMNS[name]):
                raise ValueError('Column order must contain every column exactly once.')
    return settings


def symbol(value):
    value = str(value).strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}', value):
        raise ValueError('Enter a US ticker such as AAPL or BRK-B.')
    return value


def number(value, positive=False):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or (positive and n == 0) or n > Decimal('1e12'):
            raise ValueError('Enter a finite positive quantity and non-negative price / fees.')
        return n
    except InvalidOperation:
        raise ValueError('Invalid numeric value.') from None


def long_term(acquired, sold):
    acquired = date.fromisoformat(acquired)
    try:
        anniversary = acquired.replace(year=acquired.year + 1)
    except ValueError:
        anniversary = acquired.replace(year=acquired.year + 1, day=28)
    return date.fromisoformat(sold) > anniversary


def position(trades):
    """Stable same-day order; reject edits/deletions which break subsequent sales."""
    lots, disposals, ids = [], [], set()
    buy_basis = buy_quantity = Decimal(0)
    for trade in sorted(trades, key=lambda row: row['date']):
        account_id = trade.get('accountId', 'legacy')
        if not isinstance(account_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', account_id):
            raise ValueError('Invalid trade account.')
        if not isinstance(trade['id'], str) or not 1 <= len(trade['id']) <= 80:
            raise ValueError('A valid trade ID is required.')
        if trade['id'] in ids:
            raise ValueError('Duplicate trade IDs.')
        ids.add(trade['id'])
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', trade['date']):
            raise ValueError('Use YYYY-MM-DD for trade dates.')
        date.fromisoformat(trade['date'])
        qty, price, fees = number(trade['quantity'], True), number(trade['price']), number(trade['fees'])
        if any(v > Decimal('1e9') for v in (qty, price, fees)):
            raise ValueError('Trade values must be at most 1 billion.')
        if trade['side'] == 'buy':
            basis = qty * price + fees
            lots.append(dict(id=trade['id'], date=trade['date'], quantity=qty, basis=basis, accountId=account_id))
            buy_quantity += qty
            buy_basis += basis
        elif trade['side'] == 'sell':
            if fees > qty * price:
                raise ValueError('Sale fees cannot exceed proceeds.')
            remaining = qty
            for lot in lots:
                if not remaining:
                    break
                if not lot['quantity'] or lot['accountId'] != account_id:
                    continue
                used = min(remaining, lot['quantity'])
                basis = lot['basis'] * used / lot['quantity']
                proceeds = used * price - fees * used / qty
                disposals.append(dict(tradeId=trade['id'], lotId=lot['id'], acquired=lot['date'], sold=trade['date'],
                                      quantity=used, basis=basis, proceeds=proceeds, gain=proceeds-basis,
                                      longTerm=long_term(lot['date'], trade['date']), accountId=account_id))
                lot['quantity'] -= used
                lot['basis'] -= basis
                remaining -= used
            if remaining:
                raise ValueError(f"Sale on {trade['date']} exceeds shares held in account {account_id} on that date.")
        else:
            raise ValueError('Choose buy or sell.')
    quantity = sum((lot['quantity'] for lot in lots), Decimal(0))
    basis = sum((lot['basis'] for lot in lots), Decimal(0))
    return dict(quantity=quantity, basis=basis, averageCost=basis/quantity if quantity else None,
                purchaseAverage=buy_basis/buy_quantity if buy_quantity else None,
                realizedGain=sum((d['gain'] for d in disposals), Decimal(0)),
                lots=[lot for lot in lots if lot['quantity']], disposals=disposals)


def simulate_sale(trades, sale):
    if any(row['date'] > sale['date'] for row in trades):
        raise ValueError('The hypothetical sale must follow all recorded trades.')
    ident = uuid.uuid4().hex
    result = position([*trades, dict(sale, id=ident, side='sell')])
    lots = [d for d in result['disposals'] if d['tradeId'] == ident]
    return dict(lots=lots, shortGain=sum((d['gain'] for d in lots if not d['longTerm']), Decimal(0)),
                longGain=sum((d['gain'] for d in lots if d['longTerm']), Decimal(0)),
                hasLoss=any(d['gain'] < 0 for d in lots))


def widget(kind, ident=None):
    config = dict(symbol='AAPL', followSymbol=True, range='1mo', watchlistId='core', limit=8,
                  metrics=list(METRICS)[:4] + ['marketCap', 'priceToBook', 'dividendYield', 'profitMargins'],
                  analystScope='stock', earningsDate='', newsScope='company', sector='all')
    return dict(id=ident or uuid.uuid4().hex, type=kind, title=WIDGETS.get(kind, kind), config=config)


def page(ident, name, kinds):
    from .dashboard.layout_engine import ENGINE, BREAKPOINTS
    widgets = [widget(kind) for kind in kinds]
    layout = ENGINE.repair([dict(i=w['id'], x=(i % 2)*6, y=(i//2)*30, w=12 if w['type'] in ('positions', 'ledger', 'headlines') else 6, h=32,minH=24 if w['type'] in ('positions','ledger','headlines') else 18) for i,w in enumerate(widgets)])
    return dict(id=ident, name=name, widgets=widgets, settings=dict(compact=True, density='comfortable', gap=12, reducedMotion=False),
                layouts={key: ENGINE.derive(layout, key) for key in BREAKPOINTS})


def initial_workspace():
    return dict(version=2, theme='light', dataMode='auto',
                pages=[page('home', 'Investment overview', ['watchlist', 'chart', 'market', 'movers', 'news']),
                       page('positions', 'Watchlists and positions', ['positions']),
                       page('trade-history', 'Trade history', ['ledger']),
                       page('market-news', 'Market news', ['headlines', 'notes'])],
                stockTemplate=page('stock-template', 'Stock research', ['chart', 'quote', 'metrics', 'analyst', 'news', 'notes']),
                stockOverrides={}, newsPages={}, templates={}, notes={}, trades={}, taxProfile=None,
                watchlists=[dict(id='core', name='Core watchlist', symbols=['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN'])],
                investmentAccounts=[legacy_account()], brokerImports=[], securityClassifications={},
                performanceRecords=dict(valuations=[],flows=[]))


def include_purchased_symbols(state, tickers=None, watchlist_id=None):
    """Track purchased tickers without duplicating them across watchlists."""
    purchased=tickers if tickers is not None else [s for s,rows in state['trades'].items() if any(row['side']=='buy' for row in rows)]
    watched={s for listing in state['watchlists'] for s in listing['symbols']}
    preferred=next((w for w in state['watchlists'] if w['id']==watchlist_id),state['watchlists'][0])
    for ticker in purchased:
        ticker=symbol(ticker)
        if ticker in watched:continue
        target=preferred if len(preferred['symbols'])<30 else next((w for w in state['watchlists'] if len(w['symbols'])<30),None)
        if target is None:
            if len(state['watchlists'])>=20:raise ValueError('Your watchlists are full. Free a ticker slot to track this purchase.')
            target=dict(id=uuid.uuid4().hex,name='Purchased investments',symbols=[])
            state['watchlists'].append(target)
        target['symbols'].append(ticker);watched.add(ticker)


def validate_workspace(raw):
    """Accept Folio v1 exports without dropping their layouts or unknown settings."""
    try:
        value = deepcopy(raw)
        if value.get('version') not in (1, 2) or not isinstance(value['notes'], dict):
            raise ValueError('Use a version 1 or 2 investment workspace export.')
        from .dashboard.schema import migrate_workspace
        value = migrate_workspace(value)
        value.setdefault('trades', {})
        value.setdefault('stockOverrides', {})
        value.setdefault('newsPages', {})
        value.setdefault('taxProfile', None)
        value.setdefault('investmentAccounts', [legacy_account()])
        accounts = validate_accounts(value['investmentAccounts'])
        if 'serviceCopies' in value:
            from .shared_sync import validate_investment_copies
            validate_investment_copies(value['serviceCopies'],accounts)
        value.setdefault('brokerImports', [])
        validate_broker_history(value['brokerImports'], accounts)
        from .portfolio_analysis import validate_classifications
        value.setdefault('securityClassifications', {})
        validate_classifications(value['securityClassifications'])
        from .performance import validate_records
        value.setdefault('performanceRecords',dict(valuations=[],flows=[]))
        validate_records(value['performanceRecords'],accounts)
        if 'dividendEvents' in value:
            from .dividends import validate_events
            validate_events(value['dividendEvents'],accounts)
        if 'riskFactorSets' in value:
            from .risk_analysis import validate_factor_sets
            validate_factor_sets(value['riskFactorSets'])
        from .security_research import validate_views
        if 'researchViews' in value:validate_views(value['researchViews'])
        if 'marketAlerts' in value:
            from .market_alerts import validate_alerts
            validate_alerts(value['marketAlerts'])
        if 'performanceBenchmark' in value:
            settings=value['performanceBenchmark']
            if not isinstance(settings,dict) or settings.get('range') not in ('1y','5y'):
                raise ValueError('Choose a one-year or five-year benchmark range.')
            symbol(settings['symbol'])
        if 'viewSettings' in value:
            settings=value['viewSettings']
            if isinstance(settings,dict) and isinstance(settings.get('order'),dict):
                order=settings['order'].get('trades')
                if isinstance(order,list) and len(order)==6 and set(order)==set(range(6)):
                    settings['order']['trades']=[*order,6]
            if isinstance(settings,dict) and isinstance(settings.get('columns'),dict):
                columns=settings['columns'].get('trades')
                if isinstance(columns,list) and columns==list(range(6)):
                    settings['columns']['trades']=[*columns,6]
            validate_view_settings(value['viewSettings'])
        if 'newsPanelSizes' in value:
            sizes = value['newsPanelSizes']
            if not isinstance(sizes, list) or len(sizes) != 2 or any(type(n) is not int or not 0 <= n <= 100000 for n in sizes) or not sum(sizes):
                raise ValueError('Invalid news panel sizes.')
        if value.get('theme', 'light') not in ('dark', 'light'):
            raise ValueError('Invalid theme.')
        if value['taxProfile'] is not None:
            from .investment_tax import validate_profile
            validate_profile(value['taxProfile'])
        if value.get('dataMode', 'auto') not in ('auto', 'demo'):
            raise ValueError('Invalid market data mode.')
        for key,limit in (('pages',24),('watchlists',20)):
            if not 1 <= len(value[key]) <= limit or len({p['id'] for p in value[key]}) != len(value[key]):
                raise ValueError(f'Each dashboard and watchlist needs a unique ID; limit {limit}.')
        if not any(p['id'] == 'home' for p in value['pages']):
            raise ValueError('The home dashboard is required.')
        existing={p['id'] for p in value['pages']}
        for ident,name,kinds in [('positions','Watchlists and positions',['positions']),('trade-history','Trade history',['ledger']),('market-news','Market news',['headlines','notes'])]:
            if ident not in existing:value['pages'].append(page(ident,name,kinds))
        if len(value['pages'])>24:raise ValueError('Limit: 20 custom dashboards and four primary pages.')
        watchlists = {w['id'] for w in value['watchlists']}
        for listing in value['watchlists']:
            if not isinstance(listing['symbols'], list) or not listing['name'].strip() or len(listing['symbols']) > 30:
                raise ValueError('Watchlists need a name and at most 30 tickers.')
            listing['symbols'] = [symbol(s) for s in listing['symbols']]
            if len(set(listing['symbols'])) != len(listing['symbols']):
                raise ValueError('Duplicate ticker in watchlist.')
        for p in [*value['pages'], value['stockTemplate'], *value['stockOverrides'].values(), *value['newsPages'].values()]:
            if not isinstance(p['name'], str) or not p['name'].strip() or len(p['widgets']) > 30:
                raise ValueError('Each dashboard needs a name and at most 30 widgets.')
            for w in p['widgets']:
                if w['type'] in WIDGETS:
                    defaults = widget(w['type'])['config']
                    defaults.update(w['config'])
                    defaults['watchlistId'] = defaults.get('watchlistId') if defaults.get('watchlistId') in watchlists else value['watchlists'][0]['id']
                    w['config'] = defaults
                if not isinstance(w['config'], dict):
                    raise ValueError('Invalid widget configuration.')
        broker_ids=set()
        for key, trades in value['trades'].items():
            symbol(key)
            if len(trades)>5000:
                raise ValueError('Too many trades.')
            for row in trades:
                if row.get('accountId', 'legacy') not in accounts:
                    raise ValueError('A trade refers to a missing investment account.')
                if 'externalId' in row and (not isinstance(row['externalId'], str) or not 1 <= len(row['externalId']) <= 120):
                    raise ValueError('Invalid broker transaction ID.')
                if row.get('externalId'):
                    identity=row.get('accountId','legacy'),row['externalId']
                    if identity in broker_ids:raise ValueError('Duplicate broker transaction ID in the same account.')
                    broker_ids.add(identity)
            position(trades)
        include_purchased_symbols(value)
        for key, note in value['notes'].items():
            if not isinstance(key, str) or not isinstance(note,str) or len(note)>20000:
                raise ValueError('Invalid research notes.')
        for key in value['stockOverrides']:
            symbol(key)
        return value
    except (KeyError, TypeError, AttributeError, InvalidOperation, OverflowError) as exc:
        raise ValueError('Invalid workspace file.') from exc


class InvestmentStore:
    def __init__(self, directory):
        self.path = directory / 'investments-workspace.json'
        self.error = ''
        self.state = initial_workspace()
        if self.path.exists():
            try:
                self.state = validate_workspace(read_json(self.path))
            except ValueError as exc:
                self.error = f'Saved investment workspace could not be read: {exc} The file has been preserved.'

    def save(self, state):
        candidate = validate_workspace(state)
        if self.error and self.path.exists():
            backup = self.path.with_name('investments-unreadable-' + uuid.uuid4().hex + '.json')
            backup.write_bytes(self.path.read_bytes())
        write_json(self.path, candidate)
        self.state = candidate
        self.error = ''

    def trade(self, ticker, row, delete=False, watchlist_id=None):
        ticker = symbol(ticker)
        state = deepcopy(self.state)
        trades = state['trades'].setdefault(ticker, [])
        index = next((i for i,r in enumerate(trades) if r['id']==row['id']), None)
        if delete:
            if index is not None:
                trades.pop(index)
        elif index is None:
            trades.append(row)
        else:
            trades[index] = row
        if not delete and row['side']=='buy':include_purchased_symbols(state,[ticker],watchlist_id)
        self.save(state)

    def portfolio(self):
        holdings = {s: position(rows) for s,rows in self.state['trades'].items()}
        return dict(holdings=holdings, basis=sum((p['basis'] for p in holdings.values()), Decimal(0)),
                    realized=sum((p['realizedGain'] for p in holdings.values()), Decimal(0)))
