"""Guided start paths and explicitly disposable synthetic finance sessions."""
from datetime import date
from pathlib import Path
from .catalog import default_profile, write_json
from .finance import FinanceStore, shift_month
from .investments import InvestmentStore
from .planning import PlanningStore

PATHS = {
    'beginner': ('I am new to personal finance', ['demo','plan','accounts','goals','terms','backup']),
    'saver': ('I want to budget and save', ['plan','statements','accounts','goals','coverage','runway','backup']),
    'casual': ('I want a simple investing view', ['demo','accounts','trades','terms','allocation','performance','retirement','health','backup']),
    'active': ('I manage my own investments', ['trades','research','health','allocation','performance','notes','reports','backup']),
    'advanced': ('I want detailed records and control', ['accounts','trades','health','allocation','performance','reports','notes','backup']),
}
TASKS = {
    'demo': ('Explore sample finances', 'Open an isolated demo with a budget, accounts, goals and synthetic holdings. Reset or close it without changing your real records.'),
    'plan': ('Make a monthly plan', 'Enter take-home pay and known expenses. Mark money received or paid when it actually moves.'),
    'statements': ('Import and review a statement', 'Map a CSV or supported PDF, check duplicates, and categorize merchants. Saving a statement does not prove a full month is complete.'),
    'accounts': ('Record account balances', 'Add assets and debts with dated balances. These are manual estimates; update them regularly.'),
    'goals': ('Set a savings goal', 'Choose an emergency-fund or other target and record the amount you have set aside.'),
    'terms': ('Learn the investing terms', 'Read plain-language definitions for basis, earnings, analyst opinions and demo data.'),
    'coverage': ('Check what your budget includes', 'Review imported transactions, statement periods and manual payments before relying on totals.'),
    'runway': ('Explore variable-income cash runway', 'Review current cash, take-home income ranges and remaining monthly costs. Scenarios show assumptions and reserve shortfalls, not guarantees.'),
    'retirement': ('Explore retirement what-if scenarios', 'Compare editable annual contributions, spending, inflation, fees and outside income. Constant-return examples are not probabilities or guaranteed outcomes.'),
    'trades': ('Record your holdings', 'Record purchases and sales with dates, quantities and fees. Entries do not submit broker orders.'),
    'research': ('Explore a research dashboard', 'Inspect charts, sources, fundamentals, earnings and news for a ticker.'),
    'health': ('Inspect market-data health', 'Check sources, timestamps and errors; demo and cached quotes do not value personal holdings.'),
    'notes': ('Write a research note', 'Save questions or a thesis beside the information you are reviewing.'),
    'reports': ('Export your records', 'Download dated CSV records for a spreadsheet or your own review.'),
    'allocation': ('Check recorded portfolio exposure', 'Inspect priced/unpriced holdings, concentration and unclassified sectors. Cost basis is a separate offline view.'),
    'performance': ('Separate funding from investment returns', 'Record whole-account values and external flows. Inspect supported TWR/MWR and clearly scoped price benchmarks.'),
    'backup': ('Back up your local data', 'Save a portable archive. Later edits require a new backup; restore replaces the current workspace.'),
}


def seed_demo(directory, theme='light'):
    """Only called for a newly created temporary directory by the host window."""
    directory = Path(directory)
    if any(directory.iterdir()):
        raise ValueError('Demo data requires an empty temporary directory.')
    today, month = date.today().isoformat(), date.today().strftime('%Y-%m')
    finance = FinanceStore(directory)
    for name,kind,category,value in [('Sample take-home pay','income','salary',3000),('Sample rent','expense','rent',1000),('Sample savings contribution','expense','savings',200)]:
        ident = finance.save_entry(name,kind,category,value,month,True)
        finance.post(ident,month,True)
    for category,value in [('groceries',350),('transport',120),('subscriptions',50)]:
        finance.set_recurring_target(month,category,value)
    with finance.connection() as db:
        db.execute('INSERT INTO accounts VALUES (?,?,?,?)', ('demo:0000','Demo','0000','demo-card'))
        statement = db.execute('INSERT INTO statements(source_hash,filename,account_key,closing_date) VALUES (?,?,?,?)', ('synthetic-sample','synthetic-example.csv','demo:0000',today)).lastrowid
        for index,(merchant,value,category) in enumerate([('Example grocery store',8500,'groceries'),('Example bus pass',2500,'transport')]):
            db.execute('''INSERT INTO transactions(statement_id,account_key,fingerprint,occurrence,date,posting_date,description,merchant_key,amount_cents,kind,reference,issuer_category,page,category)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (statement,'demo:0000','synthetic-'+str(index),1,today,today,merchant,merchant.upper(),value,'purchase','','',1,category))
    planning = PlanningStore(directory)
    checking = planning.save_account('Sample checking','checking','USD','Sample household',1200,today)
    savings = planning.save_account('Sample savings','savings','USD','Sample household',5000,today)
    debt = planning.save_account('Sample credit-card debt','credit_card','USD','Sample household',1200,today,apr=20,minimum_payment=50)
    earlier = shift_month(month,-1)+'-01'
    for ident,value in [(checking,1000),(savings,4500),(debt,1400)]:
        planning.record_balance(ident,value,earlier)
    target = date(date.today().year+1,date.today().month,min(28,date.today().day)).isoformat()
    goal = planning.save_goal('Sample emergency fund','emergency','USD',10000,target,saved=4000,account_id=savings)
    planning.save_goal('Sample emergency fund','emergency','USD',10000,target,saved=3500,as_of=earlier,account_id=savings,ident=goal)
    investment = InvestmentStore(directory)
    investment.state['dataMode'] = 'demo'
    investment.state['watchlists'][0]['symbols'] = ['SAMPLE','DEMO']
    investment.state['trades']['SAMPLE'] = [dict(id='sample-buy',side='buy',date=today,quantity=2,price=100,fees=0)]
    from .performance import save_record
    investment.state=save_record(investment.state,'legacy',earlier,1000,'Synthetic example account total')
    investment.state=save_record(investment.state,'legacy',today,1005,'Synthetic example account total')
    investment.save(investment.state)
    profile = default_profile()
    profile.update(theme=theme,setup_complete=True,demo_session=True,wallet=['demo-card'],custom_cards=[dict(id='demo-card',name='Synthetic example card',issuerId='custom',variant='personal',status='custom',annualFee=dict(amount=0),earningRates=[dict(multiplier=1,rewardUnit='cashback_percent',eligibleCategories=['everything_else'])],benefits=[],offers=[])])
    write_json(directory/'profile.json',profile)
