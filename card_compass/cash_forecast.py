"""Monthly runway scenarios from current cash, remaining plans and stated income ranges."""
import calendar
from datetime import date
from decimal import Decimal,ROUND_HALF_UP
import json
from .ledger import Ledger
from .finance import shift_month,valid_month
from .planning import valid_day


def defaults(today=None):
    today=today or date.today()
    return dict(version=1,cashAsOf=today.isoformat(),openingCash=0,reserve=0,months=12,
                incomeMode='scheduled',incomeLow=0,incomeTypical=0,incomeHigh=0,
                expenseBuffer=0,savingsConsumeCash=True,source='Manual current cash',overrides={})


def validate_settings(settings):
    if not isinstance(settings,dict) or settings.get('version')!=1:raise ValueError('Invalid cash forecast settings version.')
    valid_day(settings.get('cashAsOf'),future=False)
    for key in ('openingCash','reserve','incomeLow','incomeTypical','incomeHigh','expenseBuffer'):
        if type(settings.get(key)) is not int or not 0<=settings[key]<=100_000_000_000_000:raise ValueError('Use non-negative cash assumptions within the supported limit.')
    if not settings['incomeLow']<=settings['incomeTypical']<=settings['incomeHigh']:raise ValueError('Income ranges must be low ≤ typical ≤ high.')
    if type(settings.get('months')) is not int or not 1<=settings['months']<=24:raise ValueError('Forecast between 1 and 24 months.')
    if settings.get('incomeMode') not in ('scheduled','range') or type(settings.get('savingsConsumeCash')) is not bool:raise ValueError('Choose valid income and savings treatment.')
    if not isinstance(settings.get('source'),str) or not 1<=len(settings['source'].strip())<=120:raise ValueError('Identify the current-cash source (up to 120 characters).')
    overrides=settings.get('overrides')
    if not isinstance(overrides,dict) or len(overrides)>24:raise ValueError('Use at most 24 monthly income overrides.')
    for month,values in overrides.items():
        valid_month(month)
        if not isinstance(values,list) or len(values)!=3 or any(type(v) is not int or not 0<=v<=100_000_000_000_000 for v in values) or not values[0]<=values[1]<=values[2]:
            raise ValueError('Monthly overrides need ordered low/typical/high income amounts.')
    return settings


class CashForecastStore(Ledger):
    def __init__(self,directory):
        super().__init__(directory)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cash_forecast_settings (id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL)')

    def load(self):
        with self.connection() as db:row=db.execute('SELECT settings FROM cash_forecast_settings WHERE id=1').fetchone()
        return validate_settings(json.loads(row['settings'])) if row else defaults()

    def save(self,settings):
        validate_settings(settings)
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO cash_forecast_settings VALUES (1,?)',(json.dumps(settings),))


def validate_table(db):
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'cash_forecast_settings' in tables:
        rows=list(db.execute('SELECT id,settings FROM cash_forecast_settings'))
        if len(rows)>1 or any(row[0]!=1 for row in rows):raise ValueError('Invalid cash forecast records.')
        for row in rows:validate_settings(json.loads(row[1]))


def forecast(finance,settings,today=None):
    today=today or date.today()
    validate_settings(settings)
    if settings['cashAsOf']!=today.isoformat():
        raise ValueError('Update current cash and its as-of date before forecasting. Monthly posting records cannot reconcile changes since an older cash balance.')
    current=today.strftime('%Y-%m')
    scenarios=[]
    for scenario,income_key,index in [('Low income','incomeLow',0),('Typical income','incomeTypical',1),('High income','incomeHigh',2)]:
        cash=settings['openingCash'];rows=[];first_shortfall=None;first_below_reserve=current if cash<settings['reserve'] else None;warnings=[]
        for offset in range(settings['months']):
            month=shift_month(current,offset);summary=finance.summary(month)
            received=summary['received']
            override=settings['overrides'].get(month)
            full_income=override[index] if override else settings[income_key] if settings['incomeMode']=='range' else summary['income']
            remaining_income=max(0,full_income-received)
            pending={}
            for entry in summary['entries']:
                if entry['kind']=='expense' and not entry['posted']:
                    pending[entry['category']]=pending.get(entry['category'],0)+entry['amount_cents']
            outflow=sum(max(0,bucket['planned']-bucket['actual'],pending.get(category,0)) for category,bucket in summary['categories'].items() if settings['savingsConsumeCash'] or category!='savings')
            buffer=settings['expenseBuffer']
            if offset==0:
                days=calendar.monthrange(today.year,today.month)[1]
                buffer=int((Decimal(buffer)*(days-today.day+1)/days).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            outflow+=buffer
            opening=cash;cash+=remaining_income-outflow
            if cash<0 and first_shortfall is None:first_shortfall=month
            if cash<settings['reserve'] and first_below_reserve is None:first_below_reserve=month
            if full_income<received:warnings.append(month+': scenario income is below recorded receipts; remaining receipts are clamped to zero.')
            if offset and received:warnings.append(month+': future-month received amounts are treated as already included in current cash; verify prepayments.')
            if offset and summary['actual']:warnings.append(month+': future-month recorded spending is treated as already reflected in current cash; verify prepayments and transaction dates.')
            if not summary['categories']:warnings.append(month+': no budget/bill categories are recorded; unrecorded costs remain outside the forecast.')
            if not summary['income'] and settings['incomeMode']=='scheduled' and not override:warnings.append(month+': no scheduled income is recorded.')
            rows.append(dict(month=month,opening=opening,incomeAssumption=full_income,recordedReceipts=received,
                             remainingReceipts=remaining_income,remainingOutflow=outflow,expenseBuffer=buffer,
                             closing=cash,aboveReserve=cash-settings['reserve'],recordedSpending=summary['actual'],
                             categoryCount=len(summary['categories']),source='Income override' if override else 'Entered income range' if settings['incomeMode']=='range' else 'Scheduled income'))
        scenarios.append(dict(name=scenario,rows=rows,firstShortfall=first_shortfall,firstBelowReserve=first_below_reserve,warnings=warnings))
    return dict(asOf=settings['cashAsOf'],source=settings['source'],reserve=settings['reserve'],openingBelowReserve=settings['openingCash']<settings['reserve'],scenarios=scenarios,
                assumptions='Monthly settlement of recorded plans; no intramonth bill timing, interest, inflation, FX, tax withholding or unrecorded transactions. Current-month actual receipts/spending are assumed reflected in opening cash. Savings treatment and spending buffer are user choices. Scenarios are not probabilities or guarantees.')
