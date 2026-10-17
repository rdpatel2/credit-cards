"""Saved comparisons and basic screening over an explicit selected ticker universe."""
from copy import deepcopy
import re
import uuid
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPushButton,
 QListWidget,QListWidgetItem,QTableWidgetItem,QHeaderView,QCheckBox,QDialogButtonBox,QInputDialog,QMessageBox,QTabWidget,QWidget,QScrollArea)
from .ui import FitDialog,text
from .planning_ui import choices
from .import_ui import grid
from .investments import symbol,METRICS
from .education import definition
from .security_research import compare,validate_config,threshold,PERCENT_FIELDS,RESEARCH_CURRENCIES


class SecurityResearchDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner;self.subscription='research-'+str(id(self));self.loading_config=False
        self.setWindowTitle('Compare and screen selected securities')
        layout=QVBoxLayout(self)
        layout.addWidget(text('Compare up to 30 tickers you choose, not a market-wide universe. Fundamentals can have missing fields or different reporting periods. Modes and source timestamps stay visible. This organizes research; metric filters are not buy/sell signals.', 'muted',True))
        views=QHBoxLayout()
        self.views=choices([(None,'Unsaved comparison')]);views.addWidget(self.views,1)
        for title,handler in [('Save view…',self.save_view),('Save as new…',lambda:self.save_view(True)),('Delete saved view',self.delete_view)]:
            button=QPushButton(title);button.clicked.connect(handler);views.addWidget(button)
        layout.addLayout(views)
        universe=QHBoxLayout()
        self.tickers=QLineEdit(', '.join(owner.store.state['watchlists'][0]['symbols']))
        self.tickers.setAccessibleName('Comma or space separated comparison tickers')
        self.tickers.setPlaceholderText('AAPL, MSFT, SPY');universe.addWidget(self.tickers,1)
        watchlist=QPushButton('Use watchlist…');watchlist.clicked.connect(self.use_watchlist);universe.addWidget(watchlist)
        layout.addLayout(universe)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        results_page=QWidget();results_layout=QVBoxLayout(results_page)
        self.tabs.addTab(results_page,'Comparison results')
        settings_page=QWidget();settings_layout=QVBoxLayout(settings_page)
        settings_scroll=QScrollArea();settings_scroll.setWidgetResizable(True);settings_scroll.setWidget(settings_page)
        self.tabs.addTab(settings_scroll,'Fields and filters')
        self.fields=QListWidget();self.fields.setMaximumHeight(130)
        for key,title in METRICS.items():
            item=QListWidgetItem(title);item.setData(Qt.ItemDataRole.UserRole,key)
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if key in ('trailingPE','marketCap','dividendYield','beta') else Qt.CheckState.Unchecked)
            item.setToolTip(definition(key));self.fields.addItem(item)
        settings_layout.addWidget(self.fields)
        form=QFormLayout()
        self.filter_field=choices([(None,'No range filter')]+list(METRICS.items()))
        self.minimum=QLineEdit();self.minimum.setPlaceholderText('No minimum')
        self.maximum=QLineEdit();self.maximum.setPlaceholderText('No maximum')
        self.currency=choices([(key,key) for key in RESEARCH_CURRENCIES],'USD')
        bounds=QHBoxLayout();bounds.addWidget(self.minimum);bounds.addWidget(self.maximum)
        form.addRow('Filter metric',self.filter_field);form.addRow('Minimum / maximum',bounds);form.addRow('Monetary filter currency',self.currency)
        settings_layout.addLayout(form)
        self.units=text('','muted',True);settings_layout.addWidget(self.units)
        self.complete=QCheckBox('Require all selected fields in comparable units')
        self.missing=QCheckBox('Include missing or currency-incompatible filter values')
        self.cached=QCheckBox('Include cached/demo fields with visible labels');self.cached.setChecked(True)
        for control in (self.complete,self.missing,self.cached):settings_layout.addWidget(control)
        view_results=QPushButton('View comparison results');view_results.clicked.connect(lambda:self.tabs.setCurrentIndex(0));settings_layout.addWidget(view_results)
        settings_layout.addStretch()
        refresh=QPushButton('Refresh selected fundamentals');refresh.clicked.connect(self.refresh);layout.addWidget(refresh)
        self.table=grid(['Ticker','Mode','Comparable coverage','Source / retrieved'])
        results_layout.addWidget(self.table,1)
        self.status=text('','muted',True);layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.views.currentIndexChanged.connect(self.load_view)
        self.tickers.textChanged.connect(self.render);self.fields.itemChanged.connect(self.render)
        self.filter_field.currentIndexChanged.connect(self.render);self.minimum.textChanged.connect(self.render);self.maximum.textChanged.connect(self.render)
        self.currency.currentIndexChanged.connect(self.render)
        for control in (self.complete,self.missing,self.cached):control.toggled.connect(self.render)
        owner.data_layer.changed.connect(self.render)
        self.resize(1120,850)
        self.refresh_views();self.render()

    def config(self):
        tickers=[]
        for raw in re.split(r'[,\s]+',self.tickers.text().strip()):
            if not raw:continue
            ticker=symbol(raw)
            if ticker not in tickers:tickers.append(ticker)
        field=self.filter_field.currentData()
        low,high=(threshold(self.minimum.text()),threshold(self.maximum.text())) if field is not None else (None,None)
        if field in PERCENT_FIELDS:
            low=low/100 if low is not None else None;high=high/100 if high is not None else None
        config=dict(symbols=tickers,fields=[self.fields.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.fields.count()) if self.fields.item(i).checkState()==Qt.CheckState.Checked],
                    filterField=field,minimum=str(low) if low is not None else None,maximum=str(high) if high is not None else None,
                    currency=self.currency.currentData(),requireComplete=self.complete.isChecked(),includeMissing=self.missing.isChecked(),includeCachedDemo=self.cached.isChecked())
        return validate_config(config)

    def render(self,*_):
        if self.loading_config:return
        field=self.filter_field.currentData()
        self.minimum.setEnabled(field is not None);self.maximum.setEnabled(field is not None)
        self.units.setText('Enter percentage bounds as displayed percentages (e.g. 5 means 5%).' if field in PERCENT_FIELDS else 'Bounds use raw ratios or selected-currency amounts. Native monetary values retain their stated currency; no FX conversion. Coverage counts comparable selected fields. As-of is retrieval time, not a financial reporting date.')
        try:
            config=self.config()
            subscribed=self.owner.data_layer.subscriptions.get(self.subscription)
            if subscribed is not None and subscribed!={('modules',ticker) for ticker in config['symbols']}:
                self.owner.data_layer.unsubscribe(self.subscription)
            result=compare(config,lambda ticker:self.owner.data('modules',ticker))
            headers=['Ticker','Mode',*[METRICS[key] for key in config['fields']],'Comparable coverage','Source / retrieved']
            self.table.clearSelection();self.table.setCurrentCell(-1,-1)
            self.table.setColumnCount(len(headers));self.table.setHorizontalHeaderLabels(headers)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            for col,width in [(0,100),(1,105)]:
                self.table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeMode.Fixed);self.table.setColumnWidth(col,width)
            for col,label in enumerate(headers):self.table.horizontalHeaderItem(col).setToolTip(definition(config['fields'][col-2]) if 2<=col<2+len(config['fields']) else label)
            self.table.setRowCount(len(result['rows']))
            for i,row in enumerate(result['rows']):
                values=[row['ticker'],row['mode'],*[row['formatted'][key] for key in config['fields']],f"{row['comparable']}/{row['fieldCount']} ({row['available']} supplied)",str(row['source'])+' · '+str(row['asOf'])]
                for j,value in enumerate(values):
                    item=QTableWidgetItem(str(value));item.setToolTip(str(value));self.table.setItem(i,j,item)
            detail=' · '.join(row['ticker']+': '+row['reason'] for row in result['excluded'][:4])
            problems=[ticker+': '+self.owner.data_layer.failures['modules:'+ticker] for ticker in config['symbols'] if 'modules:'+ticker in self.owner.data_layer.failures]
            self.status.setText(f"{len(result['rows'])} of {len(config['symbols'])} selected tickers match · {sum(row['mode']=='DEMO' for row in result['rows'])} demo · {sum(row['mode']=='CACHED' for row in result['rows'])} cached. "+detail+(' No matches: change filters or refresh; missing values are not zero.' if not result['rows'] else '')+(' Feed failures: '+' · '.join(problems[:3]) if problems else ''))
            self.result_data=result
        except ValueError as exc:
            if self.subscription in self.owner.data_layer.subscriptions:self.owner.data_layer.unsubscribe(self.subscription)
            self.table.setRowCount(0);self.status.setText(str(exc))

    def refresh(self):
        try:
            config=self.config();keys={('modules',ticker) for ticker in config['symbols']}
            self.owner.data_layer.subscribe(keys,self.subscription);self.owner.data_layer.refresh(keys)
            self.render()
        except ValueError as exc:self.status.setText(str(exc))

    def use_watchlist(self):
        listings=self.owner.store.state['watchlists']
        label,ok=QInputDialog.getItem(self,'Choose watchlist','Watchlist',[row['name'] for row in listings],editable=False)
        if ok:self.tickers.setText(', '.join(next(row for row in listings if row['name']==label)['symbols']))

    def refresh_views(self,selected=None):
        self.views.blockSignals(True);self.views.clear();self.views.addItem('Unsaved comparison',None)
        for view in self.owner.store.state.get('researchViews',[]):self.views.addItem(view['name'],view['id'])
        self.views.setCurrentIndex(max(0,self.views.findData(selected)));self.views.blockSignals(False)

    def load_view(self):
        ident=self.views.currentData()
        view=next((row for row in self.owner.store.state.get('researchViews',[]) if row['id']==ident),None)
        if not view:return
        config=view['config'];self.loading_config=True
        try:
            self.tickers.setText(', '.join(config['symbols']))
            for i in range(self.fields.count()):self.fields.item(i).setCheckState(Qt.CheckState.Checked if self.fields.item(i).data(Qt.ItemDataRole.UserRole) in config['fields'] else Qt.CheckState.Unchecked)
            self.filter_field.setCurrentIndex(self.filter_field.findData(config['filterField']))
            multiplier=100 if config['filterField'] in PERCENT_FIELDS else 1
            self.minimum.setText(str(threshold(config['minimum'])*multiplier) if config['minimum'] is not None else '')
            self.maximum.setText(str(threshold(config['maximum'])*multiplier) if config['maximum'] is not None else '')
            self.currency.setCurrentIndex(self.currency.findData(config['currency']))
            self.complete.setChecked(config['requireComplete']);self.missing.setChecked(config['includeMissing']);self.cached.setChecked(config['includeCachedDemo'])
        finally:self.loading_config=False
        self.render()

    def save_view(self,as_new=False):
        try:
            config=self.config()
            ident=None if as_new else self.views.currentData()
            previous=next((row for row in self.owner.store.state.get('researchViews',[]) if row['id']==ident),None)
            name,ok=QInputDialog.getText(self,'Save research view','Name',text=previous['name'] if previous else '')
            if not ok:return
            if not name.strip():raise ValueError('Enter a view name.')
            if not self.owner.layout_store.flush():raise ValueError('Save the workspace before recording a research view.')
            state=deepcopy(self.owner.store.state)
            views=state.setdefault('researchViews',[])
            view=dict(id=ident or uuid.uuid4().hex,name=name.strip(),config=config)
            index=next((i for i,row in enumerate(views) if row['id']==view['id']),None)
            if index is None:views.append(view)
            else:views[index]=view
            self.owner.store.save(state);self.refresh_views(view['id']);self.status.setText('View saved. It stores the universe and filters; market values remain separately sourced current data.')
        except (ValueError,OSError) as exc:self.status.setText(str(exc))

    def delete_view(self):
        ident=self.views.currentData()
        if not ident:return
        if QMessageBox.question(self,'Delete research view','Delete this saved comparison? Watchlists and trades are retained.')!=QMessageBox.StandardButton.Yes:return
        try:
            if not self.owner.layout_store.flush():raise ValueError('Save the workspace before deleting a view.')
            state=deepcopy(self.owner.store.state);state['researchViews']=[row for row in state['researchViews'] if row['id']!=ident]
            self.owner.store.save(state);self.refresh_views();self.render()
        except (ValueError,OSError) as exc:self.status.setText(str(exc))

    def done(self,result):
        self.owner.data_layer.unsubscribe(self.subscription)
        try:self.owner.data_layer.changed.disconnect(self.render)
        except (RuntimeError,TypeError):pass
        super().done(result)
