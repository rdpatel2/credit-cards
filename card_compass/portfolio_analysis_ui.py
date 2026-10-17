"""Account-filtered allocation with visible price and classification coverage."""
from copy import deepcopy
from datetime import date
from PySide6.QtWidgets import (QVBoxLayout,QHBoxLayout,QFormLayout,QPushButton,QTableWidgetItem,
                               QDialogButtonBox,QTabWidget,QWidget,QHeaderView)
from .ui import FitDialog,text
from .planning_ui import choices,dated
from .import_ui import grid
from .portfolio_analysis import allocation,ASSET_TYPES,PORTFOLIO_SECTORS


def dollars(value):return 'Unavailable' if value is None else f'USD {value:,.2f}'
def percentage(value):return 'Unavailable' if value is None else f'{value*100:.1f}%'


class ClassificationDialog(FitDialog):
    def __init__(self,parent,ticker):
        super().__init__(parent)
        self.setWindowTitle('Classify '+ticker)
        row=parent.owner.store.state.get('securityClassifications',{}).get(ticker,{})
        layout=QVBoxLayout(self)
        layout.addWidget(text('These are your labels, not verified provider classifications. Funds may hold many sectors: leave sector unclassified unless the label describes the exposure you intend. This screen does not look through fund holdings.', 'muted',True))
        form=QFormLayout()
        self.asset=choices(list(ASSET_TYPES.items()),row.get('assetType','unclassified'))
        self.sector=choices(list(PORTFOLIO_SECTORS.items()),row.get('sector','unclassified'))
        self.as_of=dated(row.get('asOf',date.today().isoformat()))
        self.as_of.setMaximumDate(self.as_of.date().currentDate())
        for title,control in [('Asset type',self.asset),('Sector',self.sector),('Reviewed as of',self.as_of)]:form.addRow(title,control)
        layout.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject)
        layout.addWidget(buttons);self.resize(550,340)

    def values(self):
        return dict(assetType=self.asset.currentData(),sector=self.sector.currentData(),asOf=self.as_of.date().toString('yyyy-MM-dd'),source='manual')


class AllocationDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner
        self.subscription='allocation-'+str(id(self))
        self._refreshing=False
        self.setWindowTitle('Portfolio allocation and concentration')
        layout=QVBoxLayout(self)
        layout.addWidget(text('Recorded securities only. Unrecorded cash, other assets and funding transfers are outside this view. Market weights require usable public USD quotes for every selected holding. Basis weights describe money remaining invested, not current exposure. No buy/sell recommendations or fund look-through.', 'muted',True))
        controls=QHBoxLayout()
        self.mode=choices([('market','Market value'),('basis','Remaining cost basis')],'market')
        self.account=choices([(None,'All investment accounts')]+[(a['id'],a['name']) for a in owner.store.state['investmentAccounts']])
        controls.addWidget(self.mode);controls.addWidget(self.account,1)
        refresh=QPushButton('Refresh selected quotes');refresh.clicked.connect(self.refresh_quotes);controls.addWidget(refresh)
        layout.addLayout(controls)
        self.summary=text('','muted',True);layout.addWidget(self.summary)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        panel=QWidget();content=QVBoxLayout(panel)
        self.table=grid(['Security','Shares','Value / basis','Weight','Asset type','Sector','Classification','Quote source / as of'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0,100)
        content.addWidget(self.table)
        self.classify=QPushButton('Classify selected security…')
        self.classify.clicked.connect(self.edit_classification);self.classify.setEnabled(False)
        self.table.itemSelectionChanged.connect(lambda:self.classify.setEnabled(self.table.currentRow()>=0))
        content.addWidget(self.classify)
        self.tabs.addTab(panel,'Securities')
        self.group_tables={}
        for key,title in [('assetType','Asset types'),('sector','Sectors')]:
            table=grid([title,'Value / basis','Weight'])
            self.group_tables[key]=table;self.tabs.addTab(table,title)
        self.details=text('','muted',True);layout.addWidget(self.details)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.mode.currentIndexChanged.connect(self.reload);self.account.currentIndexChanged.connect(self.reload)
        owner.data_layer.changed.connect(self.reload)
        self.resize(1100,690)
        self.reload()

    def reload(self,*_):
        if self._refreshing:return
        self._refreshing=True
        try:
            result=allocation(self.owner.store.state,self.owner.quote,self.mode.currentData(),self.account.currentData())
            self.result_data=result
            keys={('quote',row['ticker']+'|1d') for row in result['rows']}
            self.owner.data_layer.subscribe(keys,self.subscription)
            self.table.setCurrentCell(-1,-1);self.table.clearSelection();self.classify.setEnabled(False)
            values=[]
            for row in result['rows']:
                classified=('Manual · '+row['classificationAsOf']+(' · Review old label' if row['classificationStale'] else '')) if row['classificationAsOf'] else 'Not classified'
                values.append([row['ticker'],str(row['quantity']),dollars(row['value'])+((' · '+row['reason']) if row['reason'] else ''),percentage(row['weight']),ASSET_TYPES[row['assetType']],PORTFOLIO_SECTORS[row['sector']],classified,row['source']+' · '+row['asOf']])
            self.fill(self.table,values)
            for key,table in self.group_tables.items():
                labels=ASSET_TYPES if key=='assetType' else PORTFOLIO_SECTORS
                self.fill(table,[[labels[group],dollars(value),percentage(value/result['total']) if result['weightsAvailable'] else 'Unavailable'] for group,value in sorted(result['groups'][key].items(),key=lambda item:item[1],reverse=True)])
            coverage=f"{len(result['rows'])-len(result['missing'])} of {len(result['rows'])} selected holdings have usable values."
            if not result['rows']:
                self.summary.setText('No open positions in this selection. Record a trade or import purchases to start.')
                self.details.setText('Closed positions and realized gains are outside allocation; use Trade history or CSV exports.')
            elif not result['weightsAvailable']:
                self.summary.setText(coverage+f" Available subtotal: {dollars(result['total'])}. Weights and concentration are unavailable until all selected holdings have values and the total is positive.")
                self.details.setText('Missing: '+(', '.join(row['ticker']+' ('+row['reason']+')' for row in result['missing']) or 'No positive basis total. Zero-basis positions cannot establish basis weights.')+' Use Market data health for recovery or switch to remaining basis. No estimate fills a missing quote.')
            else:
                self.summary.setText(coverage+f" Total: {dollars(result['total'])} · Largest security: {percentage(result['largest'])} · Top five: {percentage(result['topFive'])} · HHI: {result['hhi']:.3f}.")
                self.details.setText(f"Unclassified asset share: {percentage(result['unclassifiedAsset']/result['total'])} · Unclassified sector share: {percentage(result['unclassifiedSector']/result['total'])}. HHI sums squared security weights (0–1); a larger value means more concentration, not a prediction of loss. Labels are manual and may need review; fund diversification is not inferred.")
        finally:self._refreshing=False

    @staticmethod
    def fill(table,rows):
        table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):
                item=QTableWidgetItem(str(value));item.setToolTip(str(value));table.setItem(i,j,item)
        for i in range(table.columnCount()):
            table.horizontalHeaderItem(i).setToolTip(table.horizontalHeaderItem(i).text())

    def refresh_quotes(self):
        self.owner.data_layer.refresh({('quote',row['ticker']+'|1d') for row in self.result_data['rows']})

    def edit_classification(self):
        row=self.table.currentRow()
        if row<0:return
        ticker=self.table.item(row,0).text()
        dialog=ClassificationDialog(self,ticker)
        if dialog.exec()!=dialog.DialogCode.Accepted:return
        try:
            if not self.owner.layout_store.flush():raise ValueError('Save the workspace before editing classification.')
            state=deepcopy(self.owner.store.state)
            state.setdefault('securityClassifications',{})[ticker]=dialog.values()
            self.owner.store.save(state);self.reload()
        except (ValueError,OSError) as exc:self.details.setText('Classification not saved: '+str(exc))

    def done(self,result):
        self.owner.data_layer.unsubscribe(self.subscription)
        try:self.owner.data_layer.changed.disconnect(self.reload)
        except (RuntimeError,TypeError):pass
        super().done(result)
