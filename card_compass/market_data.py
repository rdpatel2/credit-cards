"""Public Folio data providers in Python; fetches never include personal holdings."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import build_opener, HTTPCookieProcessor, Request
from xml.etree import ElementTree
from PySide6.QtCore import QThread, Signal
from .investments import METRICS, symbol, RANGES
from .market_news import sectors


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def numeric(value):
    if isinstance(value, dict):
        value = value.get('raw')
    return value if isinstance(value, (int, float)) and not isinstance(value,bool) and math.isfinite(value) else None


def parse_chart(raw, previous=None):
    result = raw['chart']['result'][0]
    meta = result['meta']
    price = numeric(meta.get('regularMarketPrice'))
    previous = previous or numeric(meta.get('previousClose')) or numeric(meta.get('chartPreviousClose'))
    if not price or not previous or price <= 0 or previous <= 0:
        raise ValueError('Price or previous close unavailable.')
    times = result.get('timestamp', [])
    closes = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])
    points = [(t, c) for t,c in zip(times,closes) if numeric(t) is not None and numeric(c) is not None]
    return dict(symbol=meta['symbol'], name=meta.get('longName',meta.get('shortName',meta['symbol'])),
                price=price, previousClose=previous, change=price-previous, changePercent=(price/previous-1)*100,
                currency=meta.get('currency',''), volume=numeric(meta.get('regularMarketVolume')),
                high=numeric(meta.get('regularMarketDayHigh')), low=numeric(meta.get('regularMarketDayLow')),
                yearHigh=numeric(meta.get('fiftyTwoWeekHigh')), yearLow=numeric(meta.get('fiftyTwoWeekLow')),
                timezone=meta.get('exchangeTimezoneName','UTC'),
                points=points, asOf=datetime.fromtimestamp(meta['regularMarketTime'],timezone.utc).isoformat(),
                source='Yahoo Finance', demo=False)


def parse_modules(raw):
    result = raw.get('quoteSummary',{}).get('result')
    if not result:
        raise ValueError('Fundamentals unavailable.')
    result = result[0]
    fields = {}
    for module in ('price','summaryDetail','defaultKeyStatistics','financialData'):
        fields.update(result.get(module,{}))
    fields['priceToSales'] = fields.get('priceToSalesTrailing12Months')
    fields['pegRatio'] = fields.get('pegRatio',fields.get('trailingPegRatio'))
    values = {key: numeric(fields.get(key)) for key in METRICS}
    if values['debtToEquity'] is not None:
        values['debtToEquity'] /= 100
    for key, eps in [('trailingPE','trailingEps'),('forwardPE','forwardEps')]:
        if (values[key] is not None and values[key] <= 0) or (values[eps] is not None and values[eps] <= 0):
            values[key] = None
    analyst = []
    for row in result.get('recommendationTrend',{}).get('trend',[]):
        counts = [numeric(row.get(k)) for k in ('strongSell','sell','hold','buy','strongBuy')]
        if row.get('period') in ('0m','-1m','-2m','-3m') and not any(p['period']==row['period'] for p in analyst) and all(v is not None and 0<=v<=10000 and int(v)==v for v in counts):
            analyst.append(dict(period=row['period'], counts=counts))
    def raw_text(value):
        return value.get('raw','') if isinstance(value,dict) else value or ''
    return dict(values=values, periods=analyst, currency=raw_text(fields.get('currency')),
                financialCurrency=raw_text(fields.get('financialCurrency')),
                source='Yahoo Finance', asOf=timestamp(), demo=False)


def parse_news(raw):
    if len(raw)>1_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Invalid news feed.')
    root = ElementTree.fromstring(raw)
    result = []
    for item in root.findall('.//item')[:40]:
        url = item.findtext('link','')
        if url.startswith(('https://','http://')):
            title=item.findtext('title','')
            result.append(dict(title=title, url=url, date=item.findtext('pubDate',''), sectors=sectors(title)))
    return dict(items=result, source='Yahoo Finance RSS', asOf=timestamp(), demo=False)


def parse_earnings(raw, day):
    data=raw.get('data')
    if not isinstance(data,dict):raise ValueError('Earnings calendar unavailable.')
    rows=data.get('rows') or []
    if not isinstance(rows,list):raise ValueError('Invalid earnings calendar response.')
    times={'time-pre-market':'Before market open','time-after-hours':'After market close','time-not-supplied':'Not supplied'}
    items=[dict(row,date=day,reportTime=times.get(row.get('time'),row.get('time') or 'Not supplied')) for row in rows if isinstance(row,dict) and row.get('symbol')]
    return dict(items=items,date=day,source='Nasdaq',asOf=timestamp(),demo=False)


class Provider:
    def __init__(self, interrupted=lambda: False):
        self.interrupted = interrupted
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def fetch(self, url, raw=False):
        if self.interrupted():
            raise InterruptedError()
        # Yahoo's crumb endpoint serves plain text and rejects JSON/RSS-only negotiation.
        request = Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; FinanceCompass/1.0)', 'Accept':'*/*'})
        with self.opener.open(request, timeout=7) as response:
            content = response.read(4_000_001)
        if self.interrupted():
            raise InterruptedError()
        if len(content)>4_000_000:
            raise ValueError('Provider response too large.')
        return content if raw else json.loads(content)

    def get(self, kind, key):
        if kind == 'quote':
            ticker, period = key.split('|')
            symbol(ticker)
            if period not in RANGES:
                raise ValueError('Invalid chart range.')
            url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + quote(ticker)
            daily = parse_chart(self.fetch(url+'?range=1d&interval=5m'))
            if period == '1d':
                return daily
            interval = '1wk' if period=='5y' else '1d'
            return parse_chart(self.fetch(url+f'?range={period}&interval={interval}'),daily['previousClose'])
        if kind == 'modules':
            symbol(key)
            try:
                self.fetch('https://fc.yahoo.com',raw=True)
            except HTTPError:
                pass  # Cookie may still be set on a 404 response.
            crumb = self.fetch('https://query1.finance.yahoo.com/v1/test/getcrumb',raw=True).decode().strip()
            if not crumb or len(crumb)>128 or any(c in crumb for c in '<>\r\n'):
                raise ValueError('Public fundamentals session unavailable.')
            params = urlencode({'modules':'price,summaryDetail,defaultKeyStatistics,financialData,recommendationTrend','crumb':crumb})
            return parse_modules(self.fetch(f'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{quote(key)}?{params}'))
        if kind == 'news':
            if key == 'market':
                articles, failures = [], []
                for feed in ('https://finance.yahoo.com/news/rssindex', 'https://www.cnbc.com/id/100003114/device/rss/rss.html'):
                    try:
                        articles.extend(parse_news(self.fetch(feed, raw=True))['items'])
                    except InterruptedError:
                        raise
                    except Exception:
                        failures.append(feed)
                if not articles:
                    raise ValueError('Market news feeds unavailable.')
                seen, items = set(), []
                for item in articles:
                    key_title = item['title'].lower().strip()
                    if key_title not in seen:
                        seen.add(key_title)
                        items.append(dict(item, sectors=sectors(item['title'])))
                return dict(items=items, source='Yahoo Finance / CNBC RSS', asOf=timestamp(), demo=False, unavailableFeeds=failures)
            return parse_news(self.fetch('https://finance.yahoo.com/rss/headline?s='+quote(key),raw=True))
        if kind == 'earnings':
            from datetime import date
            date.fromisoformat(key)
            raw = self.fetch('https://api.nasdaq.com/api/calendar/earnings?'+urlencode({'date':key}))
            return parse_earnings(raw,key)
        raise ValueError('Unknown data request.')


def demo(kind, key):
    """Explicit preview data. Never used to value personal positions."""
    source = dict(source='Demo · synthetic preview',asOf=timestamp(),demo=True)
    seed = int(hashlib.sha256(key.split('|')[0].encode()).hexdigest()[:8],16)
    if kind=='quote':
        ticker, period = key.split('|')
        price = 40 + seed%400
        now = datetime.now(timezone.utc).timestamp()
        intervals = {'1d':86400,'1mo':30*86400,'3mo':90*86400,'1y':365*86400,'5y':5*365*86400}
        points = [(now-intervals[period]*(60-i)/60,price*(.95+.001*i+.02*math.sin(i/5))) for i in range(61)]
        return dict(source, symbol=ticker,name=ticker,price=price,currency='USD',previousClose=price*.99,
                    change=price*.01,changePercent=1.01,volume=seed%10_000_000,high=price*1.03,low=price*.97,
                    yearHigh=price*1.3,yearLow=price*.7,points=points,timezone='America/New_York')
    if kind=='modules':
        values=dict(trailingEps=4+seed%800/100,forwardEps=5+seed%900/100,trailingPE=15+seed%30,
                    forwardPE=14+seed%25,pegRatio=1+seed%200/100,priceToBook=2+seed%10,
                    priceToSales=2+seed%800/100,marketCap=(10+seed%900)*1e9,enterpriseValue=(15+seed%900)*1e9,
                    enterpriseToEbitda=8+seed%20,dividendYield=(seed%300)/10000,beta=.8+seed%80/100,
                    profitMargins=.1+seed%20/100,operatingMargins=.15+seed%20/100,returnOnEquity=.12+seed%20/100,
                    revenueGrowth=.04+seed%15/100,earningsGrowth=.03+seed%20/100,totalRevenue=(5+seed%200)*1e9,
                    freeCashflow=(1+seed%30)*1e9,debtToEquity=.3+seed%200/100)
        periods=[dict(period=period,counts=[1,2+index,8+index,15+seed%10-index,10+seed%8]) for index,period in enumerate(('0m','-1m','-2m','-3m'))]
        return dict(source,values=values,periods=periods,currency='USD',financialCurrency='USD')
    if kind=='news':
        titles=['Nvidia and Microsoft discuss AI investment', 'Banks weigh the interest-rate outlook', 'Oil producers assess supply and demand', 'Healthcare companies discuss drug research', 'Wall Street awaits inflation data']
        return dict(source,items=[dict(title=title,url='',date=timestamp(),sectors=sectors(title)) for title in titles])
    if kind=='earnings':
        return dict(source,date=key,items=[dict(symbol=ticker,name=name,epsForecast=eps,date=key,reportTime='Before market open') for ticker,name,eps in
                    [('SAMPLE','Example technology company','1.20'),('DEMO','Example financial company','2.45')]])
    raise ValueError('Unknown demo data request.')


class MarketWorker(QThread):
    result = Signal(str, str, object)
    failed = Signal(str, str, str)

    def __init__(self, tasks, mode, parent=None):
        super().__init__(parent)
        self.tasks, self.mode = tasks, mode

    def run(self):
        def task(kind,key):
            if self.isInterruptionRequested():
                raise InterruptedError()
            return demo(kind,key) if self.mode=='demo' else Provider(self.isInterruptionRequested).get(kind,key)
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(task,*args):args for args in self.tasks}
            for future in as_completed(pending):
                kind,key = pending[future]
                if self.isInterruptionRequested():
                    for queued in pending:
                        queued.cancel()
                    return
                try:
                    self.result.emit(kind,key,future.result())
                except InterruptedError:
                    return
                except Exception as exc:
                    self.failed.emit(kind,key,f'{type(exc).__name__}: {exc}')
