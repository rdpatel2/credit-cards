"""Explicit monthly history coverage and local normalized-factor review."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from PySide6.QtCore import QDate,Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,
    QPushButton,QCheckBox,QTableWidgetItem,QDialogButtonBox,QFileDialog,QTabWidget,
    QScrollArea,QTextEdit,QHeaderView,QMessageBox,QListWidget,QListWidgetItem)
from .ui import FitDialog,text,MonthPicker,ScrollSafeDoubleSpinBox
from .planning_ui import choices
from .finance import shift_month
from .risk_analysis import monthly_series,risk_summary,correlations,parse_factors,validate_factor_sets
from .import_ui import grid
from .reports import write_csv


class FactorImportDialog(FitDialog):
    def __init__(self,owner,path):
        super().__init__(owner)
        self.path=Path(path);self.dataset=None
        self.setWindowTitle('Review normalized monthly factors');self.resize(900,680)
        layout=QVBoxLayout(self)
        layout.addWidget(text('Monthly USD data only. Headers: period (YYYY-MM), market_excess_percent, rf_percent, smb_percent, hml_percent. RF is a monthly return, not an annual yield. Factor values are percentages: 2 means 2%. Vendor files must be normalized first.','muted',True))
        form=QFormLayout();self.name=QLineEdit(self.path.stem);self.name.setMaxLength(120)
        self.source=QLineEdit();self.source.setMaxLength(250);self.source.setPlaceholderText('Provider / dataset / version / release, with licensing checked')
        form.addRow('Dataset name',self.name);form.addRow('Source / release',self.source);layout.addLayout(form)
        preview=QPushButton('Load reviewed CSV preview');preview.clicked.connect(self.preview);layout.addWidget(preview)
        self.summary=text('Choose source details and preview before saving. Existing history is unchanged.','muted',True);layout.addWidget(self.summary)
        self.table=grid(['Period','Market minus RF %','RF %','SMB %','HML %']);layout.addWidget(self.table,1)
        self.error=text('','muted',True);layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.confirm);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.name.textChanged.connect(self.invalidate);self.source.textChanged.connect(self.invalidate)

    def invalidate(self,*_):
        self.dataset=None;self.table.setRowCount(0);self.summary.setText('Source/name changed. Preview again before saving.')

    def preview(self):
        self.invalidate()
        try:
            self.dataset=parse_factors(self.path,self.name.text(),self.source.text())
            rows=self.dataset['rows'];self.table.setRowCount(len(rows))
            for row,item in enumerate(rows):
                values=[item['period']]+[str(Decimal(item[key])*100) for key in ('market_excess','rf','smb','hml')]
                for col,value in enumerate(values):self.table.setItem(row,col,QTableWidgetItem(value))
            self.summary.setText(f"{len(rows)} normalized monthly rows · {rows[0]['period']} to {rows[-1]['period']} · {self.dataset['file']} · source: {self.dataset['source']}. Preview is immutable; no account data is uploaded.")
            self.error.clear()
        except (ValueError,OSError) as exc:self.error.setText(str(exc))

    def confirm(self):
        if self.dataset is None:self.error.setText('Load and review a valid preview first.');return
        self.accept()


class RiskAnalysisDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner;self.result=None;self.series=None;self.used_factors=None
        self.setWindowTitle('Monthly account risk / factor analysis');self.resize(1020,720)
        layout=QVBoxLayout(self)
        layout.addWidget(text('Use recorded whole-account history to inspect volatility, drawdown and factor sensitivities. Completed monthly observations only; these are historical sample measurements, not a prediction or a buy/sell recommendation.','muted',True))
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True);settings=QWidget();controls=QVBoxLayout(settings)
        form=QFormLayout()
        self.account=choices([(None,'All aligned recorded accounts')]+[(r['id'],r['name']) for r in owner.store.state['investmentAccounts']])
        end=shift_month(date.today().strftime('%Y-%m'),-1)
        self.start=MonthPicker(QDate.fromString(shift_month(end,-11)+'-01','yyyy-MM-dd'))
        self.end=MonthPicker(QDate.fromString(end+'-01','yyyy-MM-dd'));self.end.setMaximumDate(QDate.fromString(end+'-01','yyyy-MM-dd'))
        self.factors=choices([(None,'No factor dataset')])
        self.use_rf=QCheckBox('Use my effective annual risk-free assumption when aligned RF data is unavailable')
        self.rf=ScrollSafeDoubleSpinBox();self.rf.setRange(-99.99,100);self.rf.setDecimals(2);self.rf.setSuffix(' %');self.rf.setEnabled(False)
        for title,field in [('Account scope',self.account),('First completed month',self.start),('Last completed month',self.end),('Factor dataset',self.factors),('',self.use_rf),('Annual risk-free assumption',self.rf)]:form.addRow(title,field)
        controls.addLayout(form)
        controls.addWidget(text('Requires an exact whole-account valuation at the preceding and every selected calendar month-end, plus every external-flow date. Missing months/accounts are not interpolated. At least 12 months for annualized volatility/Sharpe, 36 for three-factor regression. Monthly sampling omits intramonth losses.','muted',True))
        self.complete=QCheckBox('I reviewed account values including cash, external flows and missing records for this scope')
        controls.addWidget(self.complete)
        action_row=QHBoxLayout()
        for title,handler in [('Import factor CSV…',self.import_factors),('Remove selected factor dataset…',self.remove_factors),('Open account history / CSV import…',self.open_performance)]:
            button=QPushButton(title);button.clicked.connect(handler);action_row.addWidget(button)
        controls.addLayout(action_row)
        controls.addWidget(text('Factor datasets are manually supplied monthly USD observations with a named source/release. RF, market-minus-RF, SMB (small minus big) and HML (high minus low) are imported, not inferred from fundamentals or current holdings. Check methodology, version and permission to use the data.','muted',True))
        controls.addWidget(text('Account correlations: select two to twenty accounts with the same complete monthly window. Cash/constant series have undefined correlations; missing accounts stop the comparison.','muted',True))
        self.correlation_accounts=QListWidget();self.correlation_accounts.setMaximumHeight(160)
        for account in owner.store.state['investmentAccounts']:
            item=QListWidgetItem(account['name']);item.setData(Qt.ItemDataRole.UserRole,account['id'])
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Unchecked)
            self.correlation_accounts.addItem(item)
        controls.addWidget(self.correlation_accounts)
        compare=QPushButton('Calculate selected account correlations');compare.clicked.connect(self.compare_accounts);controls.addWidget(compare)
        scroll.setWidget(settings);self.tabs.addTab(scroll,'History and inputs')
        results=QWidget();result_layout=QVBoxLayout(results)
        self.summary=QTextEdit();self.summary.setReadOnly(True);self.summary.setMaximumHeight(230);result_layout.addWidget(self.summary)
        self.table=grid(['Completed month','TWR %','Funding-adjusted index','Drawdown %'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Fixed);self.table.setColumnWidth(0,140)
        result_layout.addWidget(self.table,1);self.tabs.addTab(results,'Risk results')
        self.details=QTextEdit();self.details.setReadOnly(True);self.tabs.addTab(self.details,'Sources and limitations')
        correlation_page=QWidget();correlation_layout=QVBoxLayout(correlation_page)
        self.correlation_summary=text('Select accounts and review coverage before calculating.','muted',True);correlation_layout.addWidget(self.correlation_summary)
        self.matrix=grid(['Account']);correlation_layout.addWidget(self.matrix,1);self.tabs.addTab(correlation_page,'Account correlations')
        self.error=text('','muted',True);layout.addWidget(self.error)
        footer=QHBoxLayout()
        for title,handler in [('Calculate risk',self.calculate),('Export calculated CSV…',self.export)]:
            button=QPushButton(title);button.clicked.connect(handler);footer.addWidget(button)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);footer.addStretch();footer.addWidget(buttons);layout.addLayout(footer)
        self.refresh_factors()
        self.use_rf.toggled.connect(self.rf.setEnabled)
        self.account.currentIndexChanged.connect(self.scope_changed);self.factors.currentIndexChanged.connect(self.invalidate)
        for field in (self.start,self.end):field.dateChanged.connect(self.scope_changed)
        self.use_rf.toggled.connect(self.invalidate);self.rf.valueChanged.connect(self.invalidate);self.complete.toggled.connect(self.invalidate)
        self.correlation_accounts.itemChanged.connect(self.scope_changed)

    def scope_changed(self,*_):
        self.complete.setChecked(False);self.invalidate()

    def refresh_factors(self,selected=None):
        self.factors.blockSignals(True);self.factors.clear();self.factors.addItem('No factor dataset',None)
        for dataset in self.owner.store.state.get('riskFactorSets',[]):self.factors.addItem(dataset['name'],dataset['id'])
        self.factors.setCurrentIndex(max(0,self.factors.findData(selected)));self.factors.blockSignals(False)

    def invalidate_correlations(self,*_):
        self.matrix.setRowCount(0);self.matrix.setColumnCount(1);self.matrix.setHorizontalHeaderLabels(['Account'])
        self.correlation_summary.setText('Inputs changed. Recalculate selected account correlations.')

    def invalidate(self,*_):
        self.result=None;self.series=None;self.used_factors=None;self.table.setRowCount(0);self.summary.clear();self.details.clear();self.invalidate_correlations()

    def window(self):return self.start.date().toString('yyyy-MM'),self.end.date().toString('yyyy-MM')

    def calculate(self):
        self.invalidate()
        if not self.complete.isChecked():self.error.setText('Review and confirm whole-account history/flow coverage before calculating.');return
        try:
            self.series=monthly_series(self.owner.store.state,self.account.currentData(),*self.window())
            self.used_factors=deepcopy(next((r for r in self.owner.store.state.get('riskFactorSets',[]) if r['id']==self.factors.currentData()),None))
            self.used_rf=self.rf.value()/100 if self.use_rf.isChecked() else None
            self.result=risk_summary(self.series,self.used_rf,self.used_factors)
            self.show_result();self.tabs.setCurrentIndex(1);self.error.clear()
        except (ValueError,OSError) as exc:self.invalidate();self.error.setText('Could not calculate: '+str(exc))

    def show_result(self):
        result=self.result
        def percentage(value):return 'Unavailable' if value is None else f'{float(value)*100:,.2f}%'
        lines=[f"{result['count']} completed monthly observations · {result['start']} to {result['end']}",
            'Linked TWR: '+percentage(result['cumulative']), 'Monthly-sampled maximum drawdown: '+percentage(result['maxDrawdown']),
            'Worst observed month: '+percentage(result['worstMonth']), 'Annualized sample volatility: '+percentage(result['annualVolatility']),
            'Annualized Sharpe: '+('Unavailable' if result['sharpe'] is None else f"{result['sharpe']:.3f}"),
            'Risk-free source: '+(result['sharpeSource'] or 'Not supplied')]
        model=result['factorModel']
        if model:
            lines.extend([f"Three-factor sample sensitivities: market {model['marketBeta']:.3f}, SMB {model['smb']:.3f}, HML {model['hml']:.3f}",
                'Monthly regression intercept: '+percentage(model['interceptMonthly'])+' (sample residual average, not future alpha)',
                'Sample R²: '+percentage(model['rSquared'])])
        elif result['factorError']:lines.append('Factor model unavailable: '+result['factorError'])
        else:lines.append('Factor model: no imported factor observations selected.')
        self.summary.setPlainText('\n'.join(lines));self.table.setRowCount(len(result['curve']))
        for row,item in enumerate(result['curve']):
            for col,value in enumerate([item['period'],percentage(item['returnRate']),f"{item['index']:,.4f}",percentage(item['drawdown'])]):self.table.setItem(row,col,QTableWidgetItem(value))
        detail=['Scope: '+self.account.currentText(),self.series['method'],'Recorded sources: '+('; '.join(self.series['sources']) or 'None'),
            'Risk-free source: '+(result['sharpeSource'] or 'Not supplied')+'. A selected annual assumption is a fallback only; fully aligned imported monthly RF takes precedence.',
            'Index begins at 100 before the selected window; drawdown is relative to its running high including that starting point. It reflects linked recorded TWR, not current-price weights or nominal balances.',
            'Sample standard deviation uses n−1. Volatility and Sharpe annualize with √12. Sharpe uses sample monthly excess returns and their standard deviation; entered annual RF converts to an effective monthly rate.',
            'Factor regression uses an intercept and market-minus-RF, SMB and HML to fit account return minus imported RF. Missing dates stop the fit; collinear/ill-conditioned inputs withhold coefficients. Regression is descriptive, with no causal attribution, statistical significance or investment recommendation claimed.']
        if self.used_factors:detail.append('Factor source: '+self.used_factors['source']+' · file '+self.used_factors['file']+' · imported '+self.used_factors['importedAt'])
        detail.extend(result['warnings']);self.details.setPlainText('\n\n'.join(detail))

    def import_factors(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose normalized monthly factor CSV','','CSV files (*.csv)')
        if not path:return
        dialog=FactorImportDialog(self,path)
        if dialog.exec()!=dialog.DialogCode.Accepted:return
        try:
            if dialog.dataset is None:raise ValueError('Review a valid factor preview first.')
            self.owner.data_layer.flush_local()
            if not self.owner.layout_store.flush():raise OSError('Pending workspace save failed; retry before importing factor data.')
            state=deepcopy(self.owner.store.state);state.setdefault('riskFactorSets',[]).append(deepcopy(dialog.dataset))
            validate_factor_sets(state['riskFactorSets']);self.owner.store.save(state)
            self.refresh_factors(dialog.dataset['id']);self.invalidate();self.error.setText('Saved reviewed factor snapshot locally; account data was not uploaded.')
        except (ValueError,OSError) as exc:self.error.setText('Could not save factor data: '+str(exc))

    def remove_factors(self):
        ident=self.factors.currentData()
        if not ident:self.error.setText('Select a saved dataset first.');return
        if QMessageBox.question(self,'Remove factor snapshot?','Remove the selected saved factor dataset? Account history is unchanged.')!=QMessageBox.StandardButton.Yes:return
        try:
            self.owner.data_layer.flush_local()
            if not self.owner.layout_store.flush():raise OSError('Pending workspace save failed; retry first.')
            state=deepcopy(self.owner.store.state);state['riskFactorSets']=[r for r in state.get('riskFactorSets',[]) if r['id']!=ident]
            self.owner.store.save(state);self.refresh_factors();self.invalidate();self.error.clear()
        except (ValueError,OSError) as exc:self.error.setText('Could not remove factor data: '+str(exc))

    def compare_accounts(self):
        self.invalidate_correlations()
        if not self.complete.isChecked():self.error.setText('Confirm reviewed history coverage first.');return
        selected=[self.correlation_accounts.item(i) for i in range(self.correlation_accounts.count()) if self.correlation_accounts.item(i).checkState()==Qt.CheckState.Checked]
        if not 2<=len(selected)<=20:self.error.setText('Select between two and twenty accounts.');return
        try:
            histories={item.data(Qt.ItemDataRole.UserRole):monthly_series(self.owner.store.state,item.data(Qt.ItemDataRole.UserRole),*self.window()) for item in selected}
            result=correlations(histories);ids=list(histories);labels={item.data(Qt.ItemDataRole.UserRole):item.text() for item in selected}
            self.matrix.setColumnCount(len(ids)+1);self.matrix.setHorizontalHeaderLabels(['Account']+[labels[ident] for ident in ids]);self.matrix.setRowCount(len(ids))
            for row,left in enumerate(ids):
                self.matrix.setItem(row,0,QTableWidgetItem(labels[left]))
                for col,right in enumerate(ids,1):
                    value=result['matrix'][left][right];self.matrix.setItem(row,col,QTableWidgetItem('Undefined: constant' if value is None else f'{value:.3f}'))
            self.correlation_summary.setText(f"{result['count']} identical monthly periods · {result['periods'][0]} to {result['periods'][-1]}. Correlation is historical account co-movement, not a diversification guarantee or security look-through.")
            self.tabs.setCurrentIndex(3);self.error.clear()
        except (ValueError,OSError) as exc:self.error.setText('Could not compare accounts: '+str(exc))

    def open_performance(self):
        self.owner.show_performance();self.scope_changed()

    def export(self):
        if self.result is None:self.error.setText('Calculate current risk results before exporting.');return
        try:
            path,_=QFileDialog.getSaveFileName(self,'Export recorded monthly risk','finance-compass-monthly-risk.csv','CSV files (*.csv)')
            if not path:return
            destination=Path(path)
            if destination.resolve() in {(self.owner.owner.directory/name).resolve() for name in ('profile.json','transactions.sqlite3','investments-workspace.json','market-cache.json')}:raise ValueError('Choose a separate export file.')
            if destination.suffix.lower()!='.csv':destination=Path(str(destination)+'.csv')
            headers=['schema_version','dataset','scope','window_start','window_end','period','account_return_percent','twr_index','drawdown_percent','annual_volatility_percent','annual_sharpe','risk_free_source','annual_rf_assumption_percent','market_excess_percent','rf_percent','smb_percent','hml_percent','factor_source','recorded_sources']
            factors={row['period']:row for row in self.used_factors['rows']} if self.used_factors else {};rows=[]
            for item in self.result['curve']:
                factor=factors.get(item['period'],{})
                rows.append([1,'risk_monthly',self.account.currentText(),self.result['start'],self.result['end'],item['period'],item['returnRate']*100,item['index'],item['drawdown']*100,
                    None if self.result['annualVolatility'] is None else self.result['annualVolatility']*100,self.result['sharpe'],self.result['sharpeSource'],None if self.used_rf is None else self.used_rf*100,
                    *[None if key not in factor else Decimal(factor[key])*100 for key in ('market_excess','rf','smb','hml')],self.used_factors['source'] if self.used_factors else '', '; '.join(self.series['sources'])])
            count=write_csv(destination,headers,rows);self.error.setText(f'Exported {count} monthly observations with method inputs. Missing metrics remain blank, not zero.')
        except (ValueError,OSError) as exc:self.error.setText('Could not export risk: '+str(exc))
