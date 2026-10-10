"""US equity session dates, including exchange holidays and special closures."""
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars


def market_today():
    return datetime.now(ZoneInfo('America/New_York')).date()


@lru_cache(maxsize=8)
def calendar(year):
    return exchange_calendars.get_calendar('XNYS', start=f'{year}-01-01', end=f'{year+1}-01-15')


def next_trading_day(today=None):
    """The first session strictly after today's date in New York."""
    today=today or market_today()
    return calendar(today.year).date_to_session(today+timedelta(days=1),direction='next').date()


def earnings_date(config):
    return config.get('earningsDate') or next_trading_day().isoformat()
