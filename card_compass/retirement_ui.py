"""Local deterministic retirement what-if scenarios with visible cash-flow timing."""
from copy import deepcopy
from datetime import date
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
import sqlite3
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,
    QPushButton,QSpinBox,QTabWidget,QScrollArea,QTextEdit,QTableWidgetItem,
    QDialogButtonBox,QFileDialog,QHeaderView)
from .ui import FitDialog,text,ScrollSafeDoubleSpinBox
from .planning_ui import dated,choices,money
from .cash_forecast_ui import cash_field,cash_value
from .retirement import defaults,project,RetirementStore,validate
from .import_ui import grid
from .reports import write_csv


class RetirementDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner;self.store=RetirementStore(owner.directory);self.result=None
        self.setWindowTitle('Retirement accumulation / drawdown scenarios');self.resize(1000,720)
        error=''
        try:self.settings=deepcopy(self.store.load())
        except (ValueError,TypeError,sqlite3.Error,OSError) as exc:
            self.settings=defaults();error='Saved assumptions could not be read; original record is preserved. Review defaults before replacing it. '+str(exc)
        layout=QVBoxLayout(self)
        layout.addWidget(text('Explore annual retirement cash needs with assumptions you can change.','sectionTitle'))
        layout.addWidget(text('Three constant-return illustrations, not probabilities or a forecast of markets. Starting funds are a reviewed USD total; no account is automatically included and no retirement age or withdrawal rate is recommended.','muted',True))
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        content=QWidget();controls=QVBoxLayout(content);form=QFormLayout()
        self.as_of=dated(self.settings['asOf']);self.as_of.setMaximumDate(QDate.currentDate())
        self.source=QLineEdit(self.settings['source']);self.source.setMaxLength(120)
        self.ages={}
        for key,title in [('currentAge','Current age'),('retireAge','Retirement age'),('endAge','Final modeled age'),('incomeStartAge','Outside retirement income starts at age')]:
            field=QSpinBox();field.setRange(18,120);field.setValue(self.settings[key]);field.setAccessibleName(title)
            self.ages[key]=field;form.addRow(title,field)
        self.amounts={}
        for key,title in [('openingCents','Starting retirement funds (USD)'),('annualContributionCents','Annual contributions before retirement'),('annualSpendingCents','Annual retirement spending'),('annualIncomeCents','Annual outside retirement take-home income'),('taxBufferCents','Additional annual tax/cost buffer'),('reserveCents','Protected reserve warning threshold')]:
            field=cash_field(self.settings[key]);self.amounts[key]=field;form.addRow(title,field)
        form.addRow('Starting funds as of (today)',self.as_of);form.addRow('Funds source / counted accounts',self.source)
        controls.addLayout(form)
        controls.addWidget(text('Annual cash inputs are in today’s dollars and increase with entered inflation. Contributions stop at retirement. Outside income is counted only after retirement and its chosen start age; surplus is added to funds. The buffer is additional: use zero if spending already includes these costs. Reserve flags depletion; it does not stop withdrawals.','muted',True))
        rates=QFormLayout();self.rates={}
        for key,title,minimum,maximum in [('lowReturnBps','Low gross nominal return',-99,30),('typicalReturnBps','Typical gross nominal return',-99,30),('highReturnBps','High gross nominal return',-99,30),('inflationBps','Annual inflation',0,20),('feeBps','Annual proportional fee',0,10)]:
            field=ScrollSafeDoubleSpinBox();field.setRange(minimum,maximum);field.setDecimals(2);field.setSuffix(' %');field.setValue(self.settings[key]/100)
            self.rates[key]=field;rates.addRow(title,field)
        controls.addLayout(rates)
        controls.addWidget(text('Defaults are editable examples, not recommended assumptions. Constant annual returns omit sequence risk. No Social Security eligibility, RMD, account withdrawal ordering, tax brackets, healthcare trend, mortality or Monte Carlo success score is inferred. Already-retired users can enter an earlier retirement age.','muted',True))
        account_row=QHBoxLayout()
        self.account=choices([('', 'Choose one reviewed USD account balance recorded today')]);self.accounts={}
        for row in owner.planning_store.accounts():
            if row['kind'] in ('savings','brokerage','ira','roth','401k') and row['currency']=='USD' and row['as_of']==date.today().isoformat():
                self.accounts[row['id']]=row;self.account.addItem(row['name']+' · '+money(row['amount_cents']),row['id'])
        account_row.addWidget(self.account,1);copy=QPushButton('Use this balance');copy.clicked.connect(self.use_balance);account_row.addWidget(copy)
        controls.addLayout(account_row)
        controls.addWidget(text('Copying replaces the starting total with one manual balance. Enter a reviewed combined total to count several accounts, avoiding the same brokerage balance/trade value twice. No cash, real estate, household assets or goal allocations are implicitly added.','muted',True))
        scroll.setWidget(content);self.tabs.addTab(scroll,'Assumptions')
        scenarios=QWidget();scenario_layout=QVBoxLayout(scenarios)
        self.scenario=choices([(i,title) for i,title in enumerate(('Low return','Typical return','High return'))],1)
        self.scenario.currentIndexChanged.connect(self.show_result);scenario_layout.addWidget(self.scenario)
        self.summary=text('Review assumptions and calculate to inspect a scenario.','muted',True);scenario_layout.addWidget(self.summary)
        self.table=grid(['Age interval','Funds · today USD','Funds · nominal USD','Unfunded USD','Withdrawal USD','Contribution USD','Spending + buffer','Outside income'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Fixed);self.table.setColumnWidth(0,100)
        scenario_layout.addWidget(self.table,1);self.tabs.addTab(scenarios,'Annual scenarios')
        self.details=QTextEdit();self.details.setReadOnly(True);self.tabs.addTab(self.details,'Timing and coverage')
        self.error=text(error,'muted',True);layout.addWidget(self.error)
        footer=QHBoxLayout()
        for title,handler in [('Calculate scenarios',self.calculate),('Save assumptions',self.save),('Export calculated CSV…',self.export)]:
            button=QPushButton(title);button.clicked.connect(handler);footer.addWidget(button)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);footer.addWidget(buttons);layout.addLayout(footer)
        for field in [*self.ages.values(),*self.amounts.values(),*self.rates.values()]:field.valueChanged.connect(self.invalidate)
        self.as_of.dateChanged.connect(self.invalidate);self.source.textChanged.connect(self.invalidate)

    def values(self):
        settings=defaults();settings.update({key:field.value() for key,field in self.ages.items()})
        settings.update({key:cash_value(field) for key,field in self.amounts.items()})
        settings.update({key:int((Decimal(str(field.value()))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) for key,field in self.rates.items()})
        settings.update(asOf=self.as_of.date().toString('yyyy-MM-dd'),source=self.source.text().strip())
        return validate(settings)

    def invalidate(self,*_):
        self.result=None;self.table.setRowCount(0);self.details.clear();self.summary.setText('Assumptions changed. Calculate again to review current scenarios.')

    def use_balance(self):
        ident=self.account.currentData()
        fresh=next((row for row in self.owner.planning_store.accounts() if row['id']==ident),None)
        if not fresh or fresh['as_of']!=date.today().isoformat() or fresh['currency']!='USD' or fresh['kind'] not in ('savings','brokerage','ira','roth','401k'):
            self.error.setText('Choose a USD eligible account balance recorded today, or enter reviewed starting funds manually.');return
        field=self.amounts['openingCents'];field.setValue(fresh['amount_cents']/100)
        field.setProperty('initialValue',field.value());field.setProperty('initialCents',fresh['amount_cents'])
        self.source.setText('Manual account: '+fresh['name']);self.as_of.setDate(QDate.currentDate());self.error.clear()

    def calculate(self):
        try:self.calculated_settings=deepcopy(self.values());self.result=project(self.calculated_settings);self.show_result();self.tabs.setCurrentIndex(1);self.error.clear()
        except (ValueError,sqlite3.Error,OSError) as exc:self.invalidate();self.error.setText(str(exc))

    def show_result(self,*_):
        if self.result is None:return
        scenario=self.result['scenarios'][self.scenario.currentData()]
        def age(value):return 'none within entered horizon' if value is None else str(value)
        self.summary.setText(scenario['name']+' · first unfunded spending at age '+age(scenario['firstUnfundedAge'])+' · first below reserve at age '+age(scenario['firstBelowReserveAge'])+' · final funds in today’s dollars: '+money(scenario['finalRealCents']))
        self.table.setRowCount(len(scenario['rows']))
        for row,item in enumerate(scenario['rows']):
            values=[f"{item['startAge']} → {item['endAge']}",money(item['realClosing']),money(item['closing']),money(item['unfunded']),money(item['fundedWithdrawal']),money(item['contribution']),money(item['spending']+item['taxBuffer']),money(item['income'])]
            for col,value in enumerate(values):self.table.setItem(row,col,QTableWidgetItem(value))
        lines=['Funds source: '+self.result['source']+' · as of '+self.result['asOf'],self.result['assumptions']]
        lines.extend(self.result['warnings'])
        for item in scenario['rows']:
            lines.append(f"Age {item['startAge']} → {item['endAge']}: opening {money(item['opening'])}; nominal return {money(item['gain'])}; fee {money(item['fees'])}; inflated reserve {money(item['reserve'])}; above reserve {money(item['aboveReserve'])}.")
        self.details.setPlainText('\n\n'.join(lines))

    def save(self):
        try:self.store.save(self.values());self.error.setText('Saved locally and included in portable backups; calculate to update scenarios.')
        except (ValueError,sqlite3.Error,OSError) as exc:self.error.setText('Could not save assumptions: '+str(exc))

    def export(self):
        if self.result is None:self.error.setText('Calculate current scenarios before exporting.');return
        try:
            path,_=QFileDialog.getSaveFileName(self,'Export deterministic retirement scenarios','finance-compass-retirement.csv','CSV files (*.csv)')
            if not path:return
            destination=Path(path)
            if destination.resolve() in {(self.owner.directory/name).resolve() for name in ('profile.json','transactions.sqlite3','investments-workspace.json','market-cache.json')}:raise ValueError('Choose a separate export file.')
            if destination.suffix.lower()!='.csv':destination=Path(str(destination)+'.csv')
            headers=['schema_version','dataset','as_of','source','scenario','gross_return_percent','inflation_percent','fee_percent','current_age','retirement_age','final_age','income_start_age','starting_funds_usd','annual_contribution_today_usd','annual_spending_today_usd','annual_outside_income_today_usd','annual_buffer_today_usd','reserve_today_usd','flow_timing','fee_timing','start_age','end_age','opening_usd','contribution_usd','spending_usd','tax_buffer_usd','outside_income_usd','funded_withdrawal_usd','unfunded_spending_usd','nominal_gain_usd','fee_usd','closing_nominal_usd','closing_today_dollars_usd','reserve_usd','above_reserve_usd']
            settings=self.calculated_settings;rows=[]
            for scenario in self.result['scenarios']:
                for item in scenario['rows']:
                    metadata=[1,'retirement_scenarios',self.result['asOf'],self.result['source'],scenario['name'],Decimal(scenario['grossReturnBps'])/100,Decimal(settings['inflationBps'])/100,Decimal(settings['feeBps'])/100]+[settings[key] for key in ('currentAge','retireAge','endAge','incomeStartAge')]+[Decimal(settings[key])/100 for key in ('openingCents','annualContributionCents','annualSpendingCents','annualIncomeCents','taxBufferCents','reserveCents')]+['start_of_interval','after_return']
                    rows.append(metadata+[item['startAge'],item['endAge']]+[Decimal(item[key])/100 for key in ('opening','contribution','spending','taxBuffer','income','fundedWithdrawal','unfunded','gain','fees','closing','realClosing','reserve','aboveReserve')])
            count=write_csv(destination,headers,rows);self.error.setText(f'Exported {count} scenario-year rows. CSV contains assumptions, not actual future results.')
        except (ValueError,OSError) as exc:self.error.setText('Could not export: '+str(exc))
