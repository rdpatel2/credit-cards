"""Deterministic annual accumulation/drawdown, with explicit user assumptions."""
from datetime import date
from decimal import Decimal,ROUND_HALF_UP,localcontext
import json
from .ledger import Ledger
from .planning import valid_day


def defaults():
    return dict(version=1,asOf=date.today().isoformat(),source='Manual reviewed retirement funds',
        currentAge=35,retireAge=65,endAge=95,openingCents=0,annualContributionCents=0,
        annualSpendingCents=0,annualIncomeCents=0,incomeStartAge=65,taxBufferCents=0,reserveCents=0,
        lowReturnBps=-200,typicalReturnBps=0,highReturnBps=200,inflationBps=200,feeBps=0)


def validate(settings):
    if not isinstance(settings,dict) or settings.get('version')!=1:raise ValueError('Invalid retirement assumptions version.')
    valid_day(settings.get('asOf'),future=False)
    for key in ('currentAge','retireAge','endAge','incomeStartAge'):
        if type(settings.get(key)) is not int or not 18<=settings[key]<=120:raise ValueError('Use whole ages from 18 through 120.')
    if max(settings['currentAge'],settings['retireAge'])>=settings['endAge']:
        raise ValueError('Final modeled age must follow current age and retirement age. Already-retired users may enter an earlier retirement age.')
    for key in ('openingCents','annualContributionCents','annualSpendingCents','annualIncomeCents','taxBufferCents','reserveCents'):
        if type(settings.get(key)) is not int or not 0<=settings[key]<=100_000_000_000_000:raise ValueError('Use non-negative retirement amounts within the supported limit.')
    for key in ('lowReturnBps','typicalReturnBps','highReturnBps'):
        if type(settings.get(key)) is not int or not -9900<=settings[key]<=3000:raise ValueError('Use annual nominal return assumptions from -99% through 30%.')
    if not settings['lowReturnBps']<=settings['typicalReturnBps']<=settings['highReturnBps']:
        raise ValueError('Return assumptions must be low ≤ typical ≤ high.')
    for key,maximum in (('inflationBps',2000),('feeBps',1000)):
        if type(settings.get(key)) is not int or not 0<=settings[key]<=maximum:raise ValueError('Use supported non-negative inflation (≤20%) and fees (≤10%).')
    if not isinstance(settings.get('source'),str) or not 1<=len(settings['source'].strip())<=120:raise ValueError('Identify the starting funds/source (up to 120 characters).')
    return settings


class RetirementStore(Ledger):
    def __init__(self,directory):
        super().__init__(directory)
        with self.connection() as db:db.execute('CREATE TABLE IF NOT EXISTS retirement_settings (id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL)')

    def load(self):
        with self.connection() as db:row=db.execute('SELECT settings FROM retirement_settings WHERE id=1').fetchone()
        return validate(json.loads(row['settings'])) if row else defaults()

    def save(self,settings):
        validate(settings)
        with self.connection() as db:db.execute('INSERT OR REPLACE INTO retirement_settings VALUES (1,?)',(json.dumps(settings),))


def validate_table(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'retirement_settings' in tables:
        rows=list(db.execute('SELECT id,settings FROM retirement_settings'))
        if len(rows)>1 or any(row[0]!=1 for row in rows):raise ValueError('Invalid retirement settings records.')
        for row in rows:validate(json.loads(row[1]))


def project(settings,today=None):
    settings=validate(settings);today=today or date.today()
    if settings['asOf']!=today.isoformat():raise ValueError('Review starting funds and update the as-of date to today before projecting.')
    scenarios=[]
    with localcontext() as context:
        context.prec=40
        inflation=Decimal(1)+Decimal(settings['inflationBps'])/10000
        fee=Decimal(settings['feeBps'])/10000
        def cents(value):return int(value.quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        for title,return_key in [('Low return','lowReturnBps'),('Typical return','typicalReturnBps'),('High return','highReturnBps')]:
            balance=settings['openingCents'];rows=[];first_shortfall=None;first_reserve=settings['currentAge'] if balance<settings['reserveCents'] else None;total_unfunded=0
            gross=Decimal(settings[return_key])/10000
            for year_index,age in enumerate(range(settings['currentAge'],settings['endAge'])):
                factor=inflation**year_index;retired=age>=settings['retireAge']
                contribution=0 if retired else cents(Decimal(settings['annualContributionCents'])*factor)
                spending=cents(Decimal(settings['annualSpendingCents'])*factor) if retired else 0
                taxes=cents(Decimal(settings['taxBufferCents'])*factor) if retired else 0
                income=cents(Decimal(settings['annualIncomeCents'])*factor) if retired and age>=settings['incomeStartAge'] else 0
                required=max(0,spending+taxes-income)
                income_surplus=max(0,income-spending-taxes)
                opening=balance
                available=balance+contribution+income_surplus
                funded=min(required,available);unfunded=required-funded
                if unfunded and first_shortfall is None:first_shortfall=age
                total_unfunded+=unfunded
                after_flow=available-funded
                gain=cents(Decimal(after_flow)*gross)
                after_return=after_flow+gain
                fees=cents(Decimal(after_return)*fee)
                balance=max(0,after_return-fees)
                reserve=cents(Decimal(settings['reserveCents'])*inflation**(year_index+1))
                if balance<reserve and first_reserve is None:first_reserve=age+1
                real=cents(Decimal(balance)/inflation**(year_index+1))
                rows.append(dict(startAge=age,endAge=age+1,year=today.year+year_index,opening=opening,
                    contribution=contribution,spending=spending,taxBuffer=taxes,income=income,incomeSurplus=income_surplus,
                    requiredWithdrawal=required,fundedWithdrawal=funded,unfunded=unfunded,gain=gain,fees=fees,
                    closing=balance,realClosing=real,reserve=reserve,aboveReserve=balance-reserve))
            scenarios.append(dict(name=title,grossReturnBps=settings[return_key],rows=rows,
                firstUnfundedAge=first_shortfall,firstBelowReserveAge=first_reserve,totalUnfunded=total_unfunded,
                finalRealCents=rows[-1]['realClosing']))
    warnings=[]
    if settings['annualSpendingCents']==0:warnings.append('No retirement spending assumption is entered; a zero modeled funding gap does not establish retirement readiness.')
    if settings['openingCents']==0:warnings.append('Starting funds are zero. Only entered future contributions and outside income support this scenario.')
    return dict(asOf=settings['asOf'],source=settings['source'],scenarios=scenarios,warnings=warnings,
        assumptions='Full-year intervals from reviewed starting funds, not partial calendar years or birthdays. Contributions before retirement and net retirement withdrawals/income surplus occur at the start of each interval. All entered annual cash amounts and the protected reserve grow with the entered inflation assumption. Gross nominal returns are constant in each scenario; proportional fees apply after returns. Real closing funds use end-interval inflation. Shortfalls are unfunded spending, not negative assets. Reserve is a warning threshold, not a withdrawal restriction. No probabilistic success rate, sequence-of-return path, mortality, eligibility, RMD, account tax, benefits, FX or medical-cost model is inferred. Tax buffer is a user-entered assumption; results are not guarantees or retirement advice.')
