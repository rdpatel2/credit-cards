"""Manual dividends and per-year account-aware review of recorded events/disposals."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,
    QPushButton,QCheckBox,QTableWidgetItem,QTabWidget,QDialogButtonBox,QMessageBox,
    QSpinBox,QTextEdit,QHeaderView)
from .ui import FitDialog,text
from .planning_ui import choices,dated,money
from .cash_forecast_ui import cash_field,cash_value
from .dividends import CLASSIFICATIONS,event_values,save_event,year_review
from .import_ui import grid


class DividendEventDialog(FitDialog):
    def __init__(self,owner,event=None):
        super().__init__(owner)
        self.record=event or {}
        self.setWindowTitle('Edit dividend record' if event else 'Record dividend cash')
        layout=QVBoxLayout(self)
        layout.addWidget(text('Record gross ordinary dividend cash from your statement. The qualified portion is included in gross, never added a second time. Eligibility and final classification must be reviewed against payer statements.','muted',True))
        form=QFormLayout()
        self.account=choices([(r['id'],r['name']) for r in owner.store.state['investmentAccounts']],self.record.get('accountId'))
        self.ticker=QLineEdit(self.record.get('symbol',''));self.ticker.setMaxLength(10)
        self.paid=dated(self.record.get('paid'));self.paid.setMaximumDate(QDate.currentDate())
        self.gross=cash_field(self.record.get('grossCents',0))
        self.classification=choices(list(CLASSIFICATIONS.items()),self.record.get('classification','unknown'))
        self.qualified=cash_field(self.record.get('qualifiedCents',0))
        self.withheld=cash_field(self.record.get('withheldCents',0))
        self.source=QLineEdit(self.record.get('source',''));self.source.setMaxLength(120)
        self.source.setPlaceholderText('Statement / payer / tax form and period')
        self.notes=QLineEdit(self.record.get('notes',''));self.notes.setMaxLength(1000)
        self.has_ex_date=QCheckBox('Ex-dividend date is known');self.has_ex_date.setChecked(bool(self.record.get('exDate')))
        self.ex_date=dated(self.record.get('exDate'));self.ex_date.setMaximumDate(QDate.currentDate())
        for title,field in [('Investment account',self.account),('Symbol',self.ticker),('Paid date',self.paid),('Gross ordinary dividends (USD)',self.gross),('Classification',self.classification),('Qualified portion (USD)',self.qualified),('Total withheld (USD)',self.withheld),('Source',self.source),('Notes',self.notes),('',self.has_ex_date),('Ex-dividend date',self.ex_date)]:form.addRow(title,field)
        layout.addLayout(form)
        layout.addWidget(text('Capital-gain distributions, return of capital, exempt-interest dividends and other 1099-DIV boxes are outside this event model. Reinvested cash needs a separate recorded purchase. This entry does not change cash-flow budgets, tax assumptions, share counts, cost basis or measured account returns.','muted',True))
        self.duplicate=QCheckBox('I reviewed a matching record and this is a separate payment')
        layout.addWidget(self.duplicate)
        self.error=text('','muted',True);layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.classification.currentIndexChanged.connect(self.update_classification)
        self.gross.valueChanged.connect(self.update_classification)
        self.has_ex_date.toggled.connect(self.ex_date.setEnabled)
        self.ex_date.setEnabled(self.has_ex_date.isChecked());self.update_classification()

    def update_classification(self,*_):
        kind=self.classification.currentData()
        self.qualified.setEnabled(kind=='mixed')
        if kind!='mixed':self.qualified.setValue(self.gross.value() if kind=='qualified' else 0)

    def values(self):
        gross=cash_value(self.gross)
        kind=self.classification.currentData()
        qualified=gross if kind=='qualified' else cash_value(self.qualified) if kind=='mixed' else 0
        return event_values(self.account.currentData(),self.ticker.text(),self.paid.date().toString('yyyy-MM-dd'),
            Decimal(gross)/100,kind,Decimal(qualified)/100,Decimal(cash_value(self.withheld))/100,
            self.source.text(),self.notes.text(),self.ex_date.date().toString('yyyy-MM-dd') if self.has_ex_date.isChecked() else '',self.record.get('id'))


class DividendReviewDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner;self.records=[]
        self.setWindowTitle('Dividends / yearly investment review')
        self.resize(1000,720)
        layout=QVBoxLayout(self)
        layout.addWidget(text('Review recorded dividends and FIFO disposals by account and calendar year. This is not a broker-certified tax report; unknown and retirement account types stay separate.','muted',True))
        controls=QHBoxLayout()
        self.year=QSpinBox();self.year.setRange(1900,2200)
        self.year.setValue((owner.store.state.get('taxProfile') or {}).get('year',date.today().year))
        self.year.setAccessibleName('Investment review year')
        self.account=choices([(None,'All recorded accounts')]+[(r['id'],r['name']) for r in owner.store.state['investmentAccounts']])
        controls.addWidget(text('Year'));controls.addWidget(self.year);controls.addWidget(self.account,1)
        layout.addLayout(controls)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        events=QWidget();event_layout=QVBoxLayout(events)
        self.table=grid(['Paid','Account','Symbol','Gross USD','Qualified portion','Classification','Source'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Fixed);self.table.setColumnWidth(0,100)
        event_layout.addWidget(self.table,1)
        actions=QHBoxLayout()
        for title,handler in [('Record dividend…',lambda:self.edit_event(True)),('Edit selected…',self.edit_event),('Delete selected…',self.delete_event)]:
            button=QPushButton(title);button.clicked.connect(handler);actions.addWidget(button)
        event_layout.addLayout(actions);self.tabs.addTab(events,'Dividend records')
        gains=QWidget();gains_layout=QVBoxLayout(gains)
        self.gains=grid(['Sold','Account','Symbol','Acquired','Shares','Basis USD','Proceeds USD','Gain USD','Term','Account type'])
        self.gains.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Fixed);self.gains.setColumnWidth(0,100)
        gains_layout.addWidget(self.gains,1)
        gains_layout.addWidget(text('FIFO is the recorded method within each account. Tax treatment of retirement/HSA distributions and unknown account types is not inferred. Wash sales, corporate actions and basis adjustments are not modeled.','muted',True))
        self.tabs.addTab(gains,'Recorded disposals')
        self.summary=QTextEdit();self.summary.setReadOnly(True);self.tabs.addTab(self.summary,'Coverage and totals')
        self.status=text('','muted',True);layout.addWidget(self.status)
        footer=QHBoxLayout()
        export=QPushButton('Open dated CSV exports');export.clicked.connect(self.open_exports);footer.addWidget(export)
        assumptions=QPushButton('Tax year / jurisdiction / assumptions…');assumptions.clicked.connect(self.tax_settings);footer.addWidget(assumptions)
        footer.addStretch();buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);footer.addWidget(buttons)
        layout.addLayout(footer)
        self.year.valueChanged.connect(self.reload);self.account.currentIndexChanged.connect(self.reload)
        self.reload()

    def reload(self,*_):
        try:
            result=year_review(self.owner.store.state,self.year.value(),self.account.currentData())
            self.records=result['events'];accounts={r['id']:r for r in self.owner.store.state['investmentAccounts']}
            self.table.setRowCount(len(self.records))
            for row,event in enumerate(self.records):
                values=[event['paid'],accounts[event['accountId']]['name'],event['symbol'],money(event['grossCents']),money(event['qualifiedCents']) if event['classification']!='unknown' else 'Not classified',CLASSIFICATIONS[event['classification']],event['source']]
                for col,value in enumerate(values):self.table.setItem(row,col,QTableWidgetItem(value))
            self.gains.setRowCount(len(result['disposals']))
            short=long=Decimal(0)
            for row,disposal in enumerate(result['disposals']):
                values=[disposal['sold'],accounts[disposal['accountId']]['name'],disposal['symbol'],disposal['acquired'],str(disposal['quantity']),f"USD {disposal['basis']:,.2f}",f"USD {disposal['proceeds']:,.2f}",f"USD {disposal['gain']:,.2f}",'Long' if disposal['longTerm'] else 'Short',disposal['accountKind']]
                for col,value in enumerate(values):self.gains.setItem(row,col,QTableWidgetItem(value))
                if disposal['accountKind']=='brokerage':
                    if disposal['longTerm']:long+=disposal['gain']
                    else:short+=disposal['gain']
            totals=result['taxable'];profile=self.owner.store.state.get('taxProfile')
            scope='No tax assumptions configured' if not profile else str(profile['year'])+' · '+profile.get('country','US')+' / '+profile['state']+' · '+profile['filingStatus']
            lines=[f'Review calendar year: {result["year"]}', 'Saved tax scope: '+scope,
                'Explicit taxable-brokerage accounts only:', 'Gross recorded ordinary dividend cash: '+money(totals['grossCents']),
                'Qualified portion (included in gross): '+money(totals['qualifiedCents']),
                'Reviewed nonqualified portion: '+money(totals['nonqualifiedCents']),
                'Unclassified gross: '+money(totals['unclassifiedCents']), 'Recorded total withheld: '+money(totals['withheldCents']),
                'Recorded short-term FIFO gains/losses: '+f'USD {short:,.2f}', 'Recorded long-term FIFO gains/losses: '+f'USD {long:,.2f}',
                'Separate retirement/unknown/other dividend cash: '+money(result['excluded']['grossCents']),
                'Dividend records do not update the sale-estimate income inputs or account returns. Include income once when reviewing tax assumptions; no rates or tax liability are calculated in this review.',
                'Ordinary gross includes qualified portions. Not all 1099-DIV boxes are represented. No corporate-action or return-of-capital basis correction is made.',
                'Coverage: manually recorded events and trade executions through today or year-end, whichever is earlier; missing events/accounts are not estimated.']
            lines.extend(result['warnings']);self.summary.setPlainText('\n\n'.join(lines));self.status.clear()
        except (ValueError,OSError,KeyError,TypeError) as exc:
            self.records=[];self.table.setRowCount(0);self.gains.setRowCount(0);self.summary.clear();self.status.setText('Could not review records: '+str(exc))

    def edit_event(self,new=False):
        row=self.table.currentRow();event=None if new else self.records[row] if 0<=row<len(self.records) else None
        if not new and event is None:self.status.setText('Select a dividend record first.');return
        dialog=DividendEventDialog(self.owner,event)
        while dialog.exec()==dialog.DialogCode.Accepted:
            try:
                self.flush_pending()
                candidate=save_event(self.owner.store.state,dialog.values(),dialog.duplicate.isChecked())
                self.persist(candidate)
                self.reload();return
            except (ValueError,OSError) as exc:dialog.error.setText(str(exc))

    def delete_event(self):
        row=self.table.currentRow()
        if not 0<=row<len(self.records):return
        event=self.records[row]
        if QMessageBox.question(self,'Delete dividend record?','Delete this recorded dividend? Trades and account values remain unchanged.')!=QMessageBox.StandardButton.Yes:return
        try:
            self.flush_pending()
            candidate=deepcopy(self.owner.store.state);candidate['dividendEvents']=[r for r in candidate.get('dividendEvents',[]) if r['id']!=event['id']]
            self.persist(candidate);self.reload()
        except (ValueError,OSError) as exc:self.status.setText('Could not delete record: '+str(exc))

    def flush_pending(self):
        self.owner.data_layer.flush_local()
        if not self.owner.layout_store.flush():raise OSError('Pending workspace edits could not be saved. Retry that save before changing dividend records.')

    def persist(self,candidate):
        self.owner.store.save(candidate)

    def tax_settings(self):
        self.owner.tax_settings();self.reload()

    def open_exports(self):
        self.owner.owner.navigate('reports');self.accept()
