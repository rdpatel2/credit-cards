"""Explicit account valuations and flows, supported returns and price benchmarks."""
from copy import deepcopy
from datetime import date
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QVBoxLayout,QHBoxLayout,QFormLayout,QPushButton,QLineEdit,
 QDialogButtonBox,QTabWidget,QTableWidgetItem,QTextEdit,QCheckBox,QMessageBox,QHeaderView)
from .ui import FitDialog,text
from .finance_ui import dollars
from .planning_ui import choices,dated
from .import_ui import grid
from .investments import symbol
from .performance import save_record,delete_record,calculate,calculate_all,benchmark_return


def usd(value):return f'USD {value:,.2f}'
def percent(value):return 'Unavailable' if value is None else f'{value*100:+.2f}%'


class PerformanceRecordDialog(FitDialog):
    def __init__(self,parent,flow=False,row=None):
        super().__init__(parent)
        self.flow=flow
        row=row or {}
        self.row=row
        self.setWindowTitle(('Edit ' if row else 'Add ')+('external cash flow' if flow else 'account valuation'))
        layout=QVBoxLayout(self)
        layout.addWidget(text('Valuations include securities and uninvested cash, after that day’s flows. Contributions and withdrawals cross the account boundary. Trades and dividends retained in the account are internal; distributions paid out are withdrawals. All external flows are modeled at day end; intraday timing can change actual returns.', 'muted',True))
        form=QFormLayout()
        self.day=dated(row.get('date'));self.day.setMaximumDate(QDate.currentDate())
        self.day.setEnabled(not row or flow)
        self.amount=dollars(float(row.get('amount',0)))
        self.kind=choices([('contribution','Contribution into account'),('withdrawal','Withdrawal from account')],row.get('kind','contribution'))
        self.source=QLineEdit(row.get('source','Broker statement' if not flow else 'Broker transfer record'))
        if flow:form.addRow('External flow',self.kind)
        for title,control in [('Date',self.day),('USD amount',self.amount),('Source / reference',self.source)]:form.addRow(title,control)
        layout.addLayout(form)
        self.error=text('','muted',True);layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.resize(590,440)

    def values(self):
        return dict(day=self.day.date().toString('yyyy-MM-dd'),amount=self.amount.value(),source=self.source.text(),
                    kind=self.kind.currentData() if self.flow else 'valuation',ident=self.row.get('id'))


class PerformanceDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner
        self.subscription='performance-'+str(id(self))
        self.benchmark_key=None
        self.setWindowTitle('Account performance · contributions and returns')
        layout=QVBoxLayout(self)
        layout.addWidget(text('Use broker account totals including cash, plus every external contribution/withdrawal. Remaining trade basis is not account value. Exact opening/closing valuations are required; TWR also needs a valuation on every flow date. This uses an end-of-day flow convention and USD only.', 'muted',True))
        controls=QHBoxLayout()
        self.account=choices([(a['id'],a['name']) for a in owner.store.state['investmentAccounts']])
        self.account.addItem('All investment accounts (aligned valuations)',None)
        controls.addWidget(self.account,1)
        layout.addLayout(controls)
        controls=QHBoxLayout()
        for title,handler in [('Add valuation',lambda:self.edit_record(False)),('Add external flow',lambda:self.edit_record(True))]:
            button=QPushButton(title);button.clicked.connect(handler);controls.addWidget(button)
        imported=QPushButton('Import valuation / flow CSV…');imported.clicked.connect(self.import_csv);controls.addWidget(imported)
        layout.addLayout(controls)
        dates=QHBoxLayout()
        self.start=dated(date(date.today().year,1,1).isoformat());self.start.setMaximumDate(QDate.currentDate())
        self.end=dated();self.end.setMaximumDate(QDate.currentDate())
        dates.addWidget(text('From'));dates.addWidget(self.start);dates.addWidget(text('Through'));dates.addWidget(self.end)
        dates.addStretch()
        button=QPushButton('Use recorded date range');button.clicked.connect(self.use_range);dates.addWidget(button)
        layout.addLayout(dates)
        benchmark=QHBoxLayout()
        settings=owner.store.state.get('performanceBenchmark',{})
        self.benchmark=QLineEdit(settings.get('symbol','SPY'));self.benchmark.setMaxLength(10)
        self.benchmark.setAccessibleName('Optional benchmark ticker; blank disables comparison')
        self.benchmark.setPlaceholderText('Optional benchmark ticker')
        self.range=choices([('1y','One year of history'),('5y','Five years of history')],settings.get('range','5y'))
        benchmark.addWidget(text('Price benchmark'));benchmark.addWidget(self.benchmark,1);benchmark.addWidget(self.range)
        layout.addLayout(benchmark)
        load=QPushButton('Load selected benchmark history');load.clicked.connect(self.load_benchmark);layout.addWidget(load)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self.valuations=grid(['Date','Account total (USD)','Source','Account'])
        self.flows=grid(['Date','Flow','Amount (USD)','Source','ID','Account'])
        self.intervals=grid(['From','Through','Opening value','Closing value','End-day net flow','Period return'])
        for table,title in [(self.valuations,'Valuations'),(self.flows,'External flows'),(self.intervals,'TWR intervals')]:
            table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents)
            self.tabs.addTab(table,title)
        self.valuations.itemDoubleClicked.connect(lambda *_:self.edit_selected())
        self.flows.itemDoubleClicked.connect(lambda *_:self.edit_selected())
        actions=QHBoxLayout()
        for title,handler in [('Edit selected record',self.edit_selected),('Delete selected record',self.delete_selected)]:
            button=QPushButton(title);button.clicked.connect(handler);actions.addWidget(button)
        layout.addLayout(actions)
        self.confirmation=QCheckBox('I confirm flow completeness and whole-account valuations.')
        self.confirmation.setToolTip('All external flows in this range are recorded; account totals include cash and retained distributions. This is your confirmation, not broker verification.')
        layout.addWidget(self.confirmation)
        calculate_button=QPushButton('Calculate supported returns');calculate_button.setObjectName('primary')
        calculate_button.clicked.connect(self.calculate_returns);layout.addWidget(calculate_button)
        self.results=QTextEdit();self.results.setReadOnly(True);self.results.setMinimumHeight(100);self.results.setMaximumHeight(180)
        self.results.setAccessibleName('Return calculations and data limitations');layout.addWidget(self.results)
        self.status=text('Add dated broker valuations and flows. This does not place trades or certify reported performance.','muted',True)
        layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.account.currentIndexChanged.connect(self.reload)
        self.start.dateChanged.connect(self.invalidate);self.end.dateChanged.connect(self.invalidate)
        self.benchmark.textChanged.connect(self.invalidate);self.range.currentIndexChanged.connect(self.invalidate)
        self.confirmation.toggled.connect(self.clear_results)
        owner.data_layer.changed.connect(self.update_benchmark_state)
        self.resize(1120,900)
        self.reload()

    @staticmethod
    def fill(table,rows):
        table.clearSelection();table.setCurrentCell(-1,-1);table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):
                item=QTableWidgetItem(str(value));item.setToolTip(str(value));table.setItem(i,j,item)

    def clear_results(self,*_):
        self.results.clear();self.intervals.setRowCount(0)

    def invalidate(self,*_):
        expected=('quote',self.benchmark.text().strip().upper()+'|'+self.range.currentData())
        if self.benchmark_key is not None and self.benchmark_key!=expected:
            self.benchmark_key=None;self.owner.data_layer.unsubscribe(self.subscription)
        self.confirmation.setChecked(False);self.clear_results()

    def reload(self,*_):
        self.invalidate()
        records=self.owner.store.state['performanceRecords']
        ident=self.account.currentData()
        accounts={a['id']:a['name'] for a in self.owner.store.state['investmentAccounts']}
        self.value_rows=sorted([row for row in records['valuations'] if ident is None or row['accountId']==ident],key=lambda row:row['date'])
        self.flow_rows=sorted([row for row in records['flows'] if ident is None or row['accountId']==ident],key=lambda row:row['date'])
        self.fill(self.valuations,[[row['date'],row['amount'],row['source'],accounts[row['accountId']]] for row in self.value_rows])
        self.fill(self.flows,[[row['date'],row['kind'],row['amount'],row['source'],row['id'],accounts[row['accountId']]] for row in self.flow_rows])

    def use_range(self):
        if len(self.value_rows)<2:
            self.status.setText('Record opening and closing values on two dates first.');return
        self.start.setDate(QDate.fromString(self.value_rows[0]['date'],'yyyy-MM-dd'))
        self.end.setDate(QDate.fromString(self.value_rows[-1]['date'],'yyyy-MM-dd'))

    def import_csv(self):
        from .performance_import_ui import PerformanceImportDialog
        dialog=PerformanceImportDialog(self.owner)
        if dialog.exec()==dialog.DialogCode.Accepted:self.reload()

    def selected(self):
        table=self.tabs.currentWidget()
        if table is self.valuations:
            i=table.currentRow();return (False,self.value_rows[i]) if 0<=i<len(self.value_rows) else None
        if table is self.flows:
            i=table.currentRow();return (True,self.flow_rows[i]) if 0<=i<len(self.flow_rows) else None
        return None

    def edit_selected(self):
        selected=self.selected()
        if selected:self.edit_record(*selected)

    def edit_record(self,flow=False,row=None):
        if row is None and self.account.currentData() is None:
            self.status.setText('Choose one investment account before adding a valuation or flow.');return
        dialog=PerformanceRecordDialog(self,flow,row)
        while dialog.exec()==dialog.DialogCode.Accepted:
            try:
                if not self.owner.layout_store.flush():raise ValueError('Save the workspace before recording performance data.')
                state=save_record(self.owner.store.state,row['accountId'] if row else self.account.currentData(),**dialog.values())
                self.owner.store.save(state);self.reload();return
            except (ValueError,OSError) as exc:dialog.error.setText(str(exc))

    def delete_selected(self):
        selected=self.selected()
        if not selected:return
        flow,row=selected
        if QMessageBox.question(self,'Delete performance record','Delete this recorded flow or valuation? Returns may become unavailable until it is replaced.')!=QMessageBox.StandardButton.Yes:return
        try:
            if not self.owner.layout_store.flush():raise ValueError('Save the workspace before deleting performance data.')
            state=delete_record(self.owner.store.state,row['accountId'],'flows' if flow else 'valuations',row['id'] if flow else row['date'])
            self.owner.store.save(state);self.reload()
        except (ValueError,OSError) as exc:self.status.setText(str(exc))

    def calculate_returns(self):
        self.clear_results()
        try:
            if not self.confirmation.isChecked():raise ValueError('Confirm recorded flow and valuation coverage before calculating; the app cannot infer missing deposits.')
            start,end=self.start.date().toString('yyyy-MM-dd'),self.end.date().toString('yyyy-MM-dd')
            result=calculate_all(self.owner.store.state,start,end) if self.account.currentData() is None else calculate(self.owner.store.state['performanceRecords'],self.account.currentData(),start,end)
            report=[f'{start} through {end} · {self.account.currentText()}',
                    f"Dollar gain after external funding: {usd(result['gain'])} · Net contributions: {usd(result['netFlows'])}",
                    'TWR (cumulative, end-day flows): '+percent(result['twr']),
                    'MWR (annualized XIRR, actual days / 365): '+percent(result['mwr'])]
            if result['twrAnnualized'] is not None:report.append('TWR annualized: '+percent(result['twrAnnualized']))
            for key in ('twrError','mwrError'):
                if result[key]:report.append(result[key])
            self.fill(self.intervals,[[row['start'],row['end'],usd(row['opening']),usd(row['closing']),usd(row['flow']),percent(row['returnRate'])] for row in result['intervals']])
            if self.benchmark.text().strip():
                ticker=symbol(self.benchmark.text())
                try:
                    comparison=benchmark_return(self.owner.data('quote',ticker+'|'+self.range.currentData()),start,end)
                    report.append(ticker+' price benchmark: '+percent(comparison['returnRate'])+' · '+comparison['source']+' · as of '+comparison['asOf']+(' · Cached history' if comparison['cached'] else ''))
                    report.append(comparison['definition'])
                except ValueError as exc:report.append('Benchmark unavailable: '+str(exc))
            if 'accountNames' in result:report.append('Included accounts: '+', '.join(result['accountNames']))
            references=set(result.get('sourceReferences',[]))|{row['source'] for row in [*result['valuations'],*result['flows']]}
            report.append('User-recorded totals and flows; completeness is confirmed by you, not verified against a broker. Sources: '+', '.join(sorted(references)))
            self.results.setPlainText('\n'.join(report))
            self.status.setText('Annualizing a short period can exaggerate apparent growth. Past returns do not predict future performance. Cash-flow timing and omitted records can change the result.')
        except (ValueError,OSError) as exc:self.results.setPlainText('Returns unavailable: '+str(exc))

    def load_benchmark(self):
        self.invalidate()
        try:
            ticker=symbol(self.benchmark.text())
            self.benchmark_key=('quote',ticker+'|'+self.range.currentData())
            if not self.owner.layout_store.flush():raise ValueError('Save the workspace before changing benchmark settings.')
            state=deepcopy(self.owner.store.state)
            state['performanceBenchmark']=dict(symbol=ticker,range=self.range.currentData())
            self.owner.store.save(state)
            self.owner.data_layer.subscribe({self.benchmark_key},self.subscription)
            self.owner.data_layer.refresh({self.benchmark_key})
            self.update_benchmark_state()
        except (ValueError,OSError) as exc:self.status.setText('Benchmark unavailable: '+str(exc))

    def update_benchmark_state(self,changed=None):
        if changed is not None and self.benchmark_key and changed not in (':'.join(self.benchmark_key),'workspace'):
            return
        if self.benchmark_key:
            self.clear_results()
            self.status.setText('Benchmark history: '+self.owner.data_layer.describe({self.benchmark_key})+'. Select dates present in the series; public history may be incomplete.')

    def done(self,result):
        self.owner.data_layer.unsubscribe(self.subscription)
        try:self.owner.data_layer.changed.disconnect(self.update_benchmark_state)
        except (RuntimeError,TypeError):pass
        super().done(result)
