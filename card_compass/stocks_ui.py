"""Native investment shell; dashboard rendering lives in dashboard modules."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import uuid
from PySide6.QtCore import Qt, QDate, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
 QTabWidget, QDialog, QDialogButtonBox, QFormLayout, QDateEdit, QSpinBox, QFileDialog,
 QInputDialog, QMessageBox, QCheckBox, QLabel, QMenu)
from .catalog import write_json
from .investments import WIDGETS, METRICS, RANGES, widget, page, symbol, position, simulate_sale, validate_workspace, VIEW_COLUMNS, validate_view_settings
from .investment_tax import STATUSES, DEDUCTIONS, NC_DEDUCTIONS, FIELDS, estimate_tax
from .finance_ui import dollars
from .ui import Metric, text, ScrollSafeComboBox, ScrollSafeDoubleSpinBox
from .market_news import SECTORS
from .dashboard.grid_canvas import GridCanvas
from .dashboard.layout_engine import ENGINE, dashboard_layout
from .dashboard.layout_store import LayoutStore
from .dashboard.data_layer import DataLayer
from .dashboard.widget_registry import REGISTRY, ConfigPanel, money, numeric


class ViewSettingsDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowTitle('Customize investment views')
        layout = QVBoxLayout(self)
        settings = owner.store.state.get('viewSettings', {})
        self.metrics = []
        for index, title in enumerate(('Remaining cost basis', 'Realized gain / loss', 'Market value')):
            check = QCheckBox(title)
            check.setChecked(settings.get('metrics', [True] * 3)[index])
            layout.addWidget(check)
            self.metrics.append(check)
        form = QFormLayout()
        self.height = QSpinBox()
        self.height.setRange(24, 96)
        self.height.setValue(settings.get('rowHeight', 58))
        form.addRow('Table row height', self.height)
        layout.addLayout(form)
        tabs = QTabWidget()
        self.columns = {}
        for name, titles in VIEW_COLUMNS.items():
            panel = QWidget()
            fields = QVBoxLayout(panel)
            self.columns[name] = []
            selected = settings.get('columns', {}).get(name, list(range(len(titles))))
            for index, title in enumerate(titles):
                check = QCheckBox(title)
                check.setChecked(index in selected)
                self.columns[name].append(check)
                fields.addWidget(check)
            fields.addStretch()
            tabs.addTab(panel, name.title())
        layout.addWidget(tabs)
        layout.addWidget(text('Drag table headers to reorder columns. Dashboards, stock tabs, and sector research all support adding, removing, moving, resizing, and configuring widgets.', 'muted', True))
        self.error = text('', 'muted', True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(460, 580)

    def values(self):
        return dict(metrics=[check.isChecked() for check in self.metrics],
                    rowHeight=self.height.value(),
                    columns={name: [i for i, check in enumerate(fields) if check.isChecked()]
                             for name, fields in self.columns.items()})

class TradeDialog(QDialog):
    def __init__(self,parent,ticker,row=None,scenario=False):
        super().__init__(parent)
        self.setWindowTitle(f'{ticker} · '+('What if I sell?' if scenario else 'Edit trade' if row else 'Record trade'))
        self.row=row or dict(id=uuid.uuid4().hex,side='sell' if scenario else 'buy',date=date.today().isoformat(),quantity=1,price=0,fees=0)
        layout=QVBoxLayout(self)
        form=QFormLayout()
        self.side=ScrollSafeComboBox()
        for v in ('buy','sell'): self.side.addItem(v.title(),v)
        self.side.setCurrentIndex(self.side.findData(self.row['side']))
        self.side.setEnabled(not scenario)
        self.day=QDateEdit(QDate.fromString(self.row['date'],'yyyy-MM-dd'))
        self.day.setDisplayFormat('yyyy-MM-dd')
        self.day.setCalendarPopup(True)
        self.quantity=ScrollSafeDoubleSpinBox()
        self.quantity.setDecimals(8)
        self.quantity.setRange(.00000001,1e9)
        self.quantity.setValue(self.row['quantity'])
        self.price=dollars(self.row['price'])
        self.price.setDecimals(6)
        self.fees=dollars(self.row['fees'])
        for title,entry in [('Side',self.side),('Date',self.day),('Shares',self.quantity),('Price per share (USD)',self.price),('Fees (USD)',self.fees)]:form.addRow(title,entry)
        layout.addLayout(form)
        layout.addWidget(text('FIFO uses the oldest available lots. This records your history; it does not place a broker order. Enter execution prices manually.', 'muted',True))
        if scenario:
            self.tax_status = text('', 'muted', True)
            layout.addWidget(self.tax_status)
            tax = QPushButton('Set up / edit tax assumptions…')
            def update_tax():
                configured = bool(parent.store.state.get('taxProfile'))
                self.tax_status.setText('Tax assumptions saved · 2026 North Carolina only.' if configured else 'Optional: set tax assumptions to include a tax estimate. Your sale inputs stay here.')
            tax.clicked.connect(lambda: (parent.tax_settings(), update_tax()))
            layout.addWidget(tax)
            update_tax()
        self.error=text('','muted',True)
        layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok if scenario else QDialogButtonBox.StandardButton.Save)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    def values(self):
        return dict(id=self.row['id'],side=self.side.currentData(),date=self.day.date().toString('yyyy-MM-dd'),quantity=self.quantity.value(),price=self.price.value(),fees=self.fees.value())

class DashboardPanel(QWidget):
    def __init__(self, owner, ident):
        super().__init__()
        self.owner, self.dashboard_id = owner, ident
        layout=QVBoxLayout(self);layout.setContentsMargins(12,12,12,12)
        self.canvas=GridCanvas(lambda frame:owner.create_component(frame,self),self)
        self.canvas.layoutChanged.connect(lambda size,geometry:owner.layout_changed(self,size,geometry))
        self.canvas.command.connect(lambda ident,command:owner.widget_command(self,ident,command))
        self.canvas.announced.connect(owner.announce)
        layout.addWidget(self.canvas)


class ResearchPanel(DashboardPanel):
    def __init__(self,owner,ticker):
        self.ticker=ticker
        super().__init__(owner,'stock-'+ticker)
        controls=QHBoxLayout()
        controls.addWidget(text(ticker+' · Research','sectionTitle'),1)
        self.customize=QCheckBox('Customize only this stock')
        self.customize.toggled.connect(owner.customize_stock)
        controls.addWidget(self.customize)
        for label,handler in [('Record trade',lambda:owner.record_trade(ticker)),('What if I sell?',lambda:owner.sale_scenario(ticker))]:
            button=QPushButton(label);button.clicked.connect(handler);controls.addWidget(button)
        self.layout().insertLayout(0,controls)
        self.scope_label=text('','muted',True)
        self.layout().insertWidget(1,self.scope_label)


class StocksPage(QWidget):
    def __init__(self,owner):
        super().__init__()
        self.owner=owner;self.store=owner.investment_store
        self.network_enabled=owner.artwork.network_enabled
        self.selected_symbol='AAPL';self.page_id='home';self.news_sector='all'
        self.stock_view=False;self.news_view=False;self.research_tabs={};self.note_editors={}
        self.layout_store=LayoutStore(self.store,validate_workspace,self)
        self.data_layer=DataLayer(self.store,owner.directory,self.network_enabled,self)
        self.cache=self.data_layer.cache;self.failures=self.data_layer.failures
        existing={p['id'] for p in self.store.state['pages']}
        for ident,name,kinds in [('positions','Watchlists and positions',['positions']),('trade-history','Trade history',['ledger']),('market-news','Market news',['headlines','notes'])]:
            if ident not in existing:self.store.state['pages'].append(page(ident,name,kinds))
        self.store.state=validate_workspace(self.store.state)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(8)
        toolbar=QHBoxLayout()
        self.ticker=QLineEdit();self.ticker.setPlaceholderText('Open a ticker · AAPL, MSFT');self.ticker.setMaximumWidth(250)
        self.ticker.returnPressed.connect(self.open_ticker);toolbar.addWidget(self.ticker,1)
        open_button=QPushButton('Open');open_button.clicked.connect(self.open_ticker);toolbar.addWidget(open_button)
        self.refresh_button=QPushButton('Refresh market data');self.refresh_button.clicked.connect(self.refresh_data);toolbar.addWidget(self.refresh_button)
        self.mode=ScrollSafeComboBox();self.mode.addItem('Public market data','auto');self.mode.addItem('Demo preview','demo')
        self.mode.setCurrentIndex(self.mode.findData(self.store.state.get('dataMode','auto')));self.mode.currentIndexChanged.connect(self.set_mode);toolbar.addWidget(self.mode)
        utilities=QPushButton('Tools');menu=QMenu(utilities)
        for label,handler in [('View settings',self.configure_views),('Import investments…',self.import_workspace),('Export investments…',self.export_workspace),('Tax assumptions…',self.tax_settings)]:menu.addAction(label,handler)
        utilities.setMenu(menu);toolbar.addWidget(utilities);layout.addLayout(toolbar)
        self.metrics=[Metric('Remaining cost basis','Across all saved positions'),Metric('Realized gain / loss','FIFO · recorded sales'),Metric('Market value','USD positions with usable live quotes')]
        self.summary_tiles=QWidget();row=QHBoxLayout(self.summary_tiles);row.setContentsMargins(0,0,0,0)
        for metric in self.metrics:metric.set_compact();row.addWidget(metric,1)
        self.valuation_details=QPushButton('See unpriced positions');self.valuation_details.setObjectName('quiet');self.valuation_details.clicked.connect(self.show_unpriced);row.addWidget(self.valuation_details)
        layout.addWidget(self.summary_tiles)
        self.status=text('Start with a watchlist or Record trade. Use Edit layout to arrange blocks.','muted',True);layout.addWidget(self.status)
        controls=QHBoxLayout()
        self.dashboards=ScrollSafeComboBox();self.dashboards.setMinimumContentsLength(10);self.dashboards.setSizeAdjustPolicy(self.dashboards.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.dashboards.currentIndexChanged.connect(self.choose_dashboard);controls.addWidget(self.dashboards,1)
        create=QPushButton('New dashboard');create.clicked.connect(self.new_dashboard);controls.addWidget(create)
        self.dashboard_tools=QPushButton('Dashboard tools');self.tools_menu=QMenu(self.dashboard_tools)
        for label,handler in [('Rename',self.rename_dashboard),('Delete',self.delete_dashboard)]:self.tools_menu.addAction(label,handler)
        self.dashboard_tools.setMenu(self.tools_menu);controls.addWidget(self.dashboard_tools)
        add=QPushButton('Add widget');add.clicked.connect(self.add_widget);controls.addWidget(add)
        self.edit_layout=QCheckBox('Edit layout');self.edit_layout.toggled.connect(self.set_editing);controls.addWidget(self.edit_layout)
        self.saved=QPushButton('Saved');self.saved.setObjectName('quiet');self.saved.clicked.connect(self.layout_store.flush);controls.addWidget(self.saved)
        layout.addLayout(controls)
        self.undo_button=QPushButton('Undo');self.undo_button.setObjectName('rowAction');self.undo_button.clicked.connect(self.layout_store.undo)
        self.redo_button=QPushButton('Redo');self.redo_button.setObjectName('rowAction');self.redo_button.clicked.connect(self.layout_store.redo)
        history=QHBoxLayout();history.addWidget(self.undo_button);history.addWidget(self.redo_button);history.addStretch();layout.addLayout(history)
        self.tabs=QTabWidget();self.tabs.setTabsClosable(True);self.tabs.tabBar().setMovable(True);self.tabs.tabCloseRequested.connect(self.close_research_tab);layout.addWidget(self.tabs,1)
        for attr,ident,label in [('dashboard_page','home','Home dashboard'),('watchlist_page','positions','Watchlists and positions'),('history_page','trade-history','Trade history'),('news_page','market-news','Market news')]:
            panel=DashboardPanel(self,ident);setattr(self,attr,panel);self.tabs.addTab(panel,label)
        self.sectors=ScrollSafeComboBox()
        for key,title in SECTORS.items():self.sectors.addItem(title,key)
        self.sectors.currentIndexChanged.connect(self.select_sector)
        self.news_page.layout().insertWidget(0,self.sectors)
        for index in range(4):
            for side in (self.tabs.tabBar().ButtonPosition.LeftSide,self.tabs.tabBar().ButtonPosition.RightSide):self.tabs.tabBar().setTabButton(index,side,None)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.data_layer.actions=dict(open_symbol=self.open_symbol,open_url=lambda url:QDesktopServices.openUrl(QUrl(url)),record_trade=self.record_trade,add_symbol=self.add_symbol,sale_selected=self.sell_selected,edit_trade=self.edit_trade,delete_trade=self.delete_trade)
        self.data_layer.message.connect(self.status.setText)
        self.data_layer.changed.connect(lambda key:self.render_metrics())
        self.layout_store.changed.connect(self.reload);self.layout_store.historyChanged.connect(self.update_history)
        self.layout_store.saveState.connect(self.saved.setText)
        for sequence,handler in [(QKeySequence.StandardKey.Undo,self.layout_store.undo),(QKeySequence.StandardKey.Redo,self.layout_store.redo)]:
            shortcut=QShortcut(QKeySequence(sequence),self);shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut);shortcut.activated.connect(handler)
        self.ensure_news_page()
        self.reload()

    def panels(self):return [self.dashboard_page,self.watchlist_page,self.history_page,self.news_page,*self.research_tabs.values()]
    def dashboard_for(self,panel,state=None):
        state=state or self.store.state
        if isinstance(panel,ResearchPanel):return state['stockOverrides'].get(panel.ticker,state['stockTemplate'])
        if panel is self.news_page:return state['newsPages'].get(self.news_sector,next(p for p in state['pages'] if p['id']=='market-news'))
        ident=self.page_id if panel is self.dashboard_page else panel.dashboard_id
        return next((p for p in state['pages'] if p['id']==ident),state['pages'][0])
    def current_page(self,state=None):return self.dashboard_for(self.tabs.currentWidget(),state)
    def active_canvas(self):return self.tabs.currentWidget().canvas
    def create_component(self,frame,panel):
        definition=REGISTRY.get(frame.block['type']);config=frame.block['config']
        def change(values):
            if 'note' in values:self.data_layer.put_note(config['_noteKey'],values['note']);return
            def edit(state):
                block=next(w for w in self.dashboard_for(panel,state)['widgets'] if w['id']==frame.ident)
                block['config'].update(values)
            self.layout_store.mutate('Configure '+frame.block['title'],edit)
        component=definition.Component(definition,config,frame.body.size(),self.edit_layout.isChecked(),change,self.data_layer)
        component.footerChanged.connect(frame.set_footer);frame.set_footer(component.footer_text)
        component.crashed.connect(frame.show_error)
        frame.refresh_button.setVisible(bool(definition.dataRequirements(config,self.store.state)))
        if frame.block['type']=='positions':self.watchlist_table=component.table;self.watchlists=component.watchlists;self.positions_component=component
        if frame.block['type']=='ledger':self.trade_table=component.table;self.trade_filter=component.filter;self.ledger_component=component
        if frame.block['type']=='headlines':self.news_table=component.table;self.news_search=component.filter
        if component.editor:self.note_editors[config['_noteKey']]=component.editor
        return component
    def render_panel(self,panel):
        dashboard=deepcopy(self.dashboard_for(panel))
        if isinstance(panel,ResearchPanel):
            separate=panel.ticker in self.store.state['stockOverrides']
            panel.customize.blockSignals(True);panel.customize.setChecked(separate);panel.customize.blockSignals(False)
            panel.scope_label.setText('Editing only '+panel.ticker if separate else 'Editing the shared stock layout · Customize only this stock for separate blocks.')
        for block in dashboard['widgets']:
            config=block['config']
            if isinstance(panel,ResearchPanel) and config.get('followSymbol',True):config['symbol']=panel.ticker
            note_context='stock-'+panel.ticker if isinstance(panel,ResearchPanel) else dashboard['id']
            config['_noteKey']=note_context+':'+block['id']
            if panel is self.news_page:config['_sector']=self.news_sector
        panel.canvas.set_dashboard(dashboard);panel.canvas.set_editing(self.edit_layout.isChecked())
    def reload(self):
        self.dashboards.blockSignals(True);self.dashboards.clear()
        for p in self.store.state['pages']:self.dashboards.addItem(p['name'],p['id'])
        self.dashboards.setCurrentIndex(max(0,self.dashboards.findData(self.current_page()['id'])));self.dashboards.blockSignals(False)
        for panel in self.panels():self.render_panel(panel)
        self.data_layer.notify_local();self.render_metrics();self.apply_view_settings();self.update_history()
    def render_dashboard(self):self.render_panel(self.tabs.currentWidget())
    def layout_changed(self,panel,size,layout):
        self.layout_store.mutate('Arrange blocks',lambda state:self.dashboard_for(panel,state)['layouts'].__setitem__(size,deepcopy(layout)))
    def set_editing(self,editing):
        for panel in self.panels():panel.canvas.set_editing(editing)
        self.status.setText('Edit mode · drag headers, resize edges, or use Space + arrows. Escape cancels.' if editing else 'View mode · content is interactive. Edit layout to arrange blocks.')
    def announce(self,message):self.status.setText(message)
    def update_history(self):
        self.undo_button.setEnabled(bool(self.layout_store.past));self.redo_button.setEnabled(bool(self.layout_store.future))
        self.undo_button.setText('Undo '+self.layout_store.past[-1][0] if self.layout_store.past else 'Undo')
    def commit(self,state,render=True):
        try:self.layout_store.replace(state);self.layout_store.flush();return True
        except (ValueError,OSError) as exc:self.status.setText(str(exc));return False
    def flush_notes(self):
        self.data_layer.flush_local();self.layout_store.flush()
    def page_context(self):
        if self.news_view:return ('news',self.news_sector)
        if self.stock_view:return ('stock',self.selected_symbol) if self.selected_symbol in self.store.state['stockOverrides'] else ('template','')
        return ('dashboard',self.current_page()['id'])
    def widget_command(self,panel,ident,command):
        block=next(w for w in self.dashboard_for(panel)['widgets'] if w['id']==ident)
        if command=='refresh':
            frame=panel.canvas.frames[ident]
            if frame.component:frame.guarded(frame.component.refresh)
            else:frame.retry()
            return
        if command=='configure':self.configure_widget(ident,panel);return
        if command=='remove':self.remove_widget(ident,panel);return
        if command=='rename':
            title,ok=QInputDialog.getText(self,'Rename widget','Title',text=block['title'])
            if ok and title.strip():self.layout_store.mutate('Rename widget',lambda state:next(w for w in self.dashboard_for(panel,state)['widgets'] if w['id']==ident).__setitem__('title',title.strip()[:80]))
        elif command=='lock':
            def lock(state):
                for layout in self.dashboard_for(panel,state)['layouts'].values():
                    g=next(g for g in layout if g['i']==ident);g['locked']=not g.get('locked',False)
            self.layout_store.mutate('Lock position',lock)
        elif command=='duplicate':self.duplicate_widget(ident,panel)
        elif command in ('move','copy'):self.move_widget_to(ident,panel,command=='copy')
        elif command=='expand':self.expand_widget(ident,panel)
    def configure_widget(self,ident,panel=None):
        panel=panel or self.tabs.currentWidget();block=next(w for w in self.dashboard_for(panel)['widgets'] if w['id']==ident)
        definition=REGISTRY.get(block['type']);dialog=definition.ConfigPanel(definition,block['config'],self.data_layer,self)
        while dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                values=dialog.values()
                self.layout_store.mutate('Configure '+block['title'],lambda state:next(w for w in self.dashboard_for(panel,state)['widgets'] if w['id']==ident).__setitem__('config',values));return
            except ValueError as exc:dialog.error.setText(str(exc))
    def add_block(self,kind,panel=None):
        panel=panel or self.tabs.currentWidget();definition=REGISTRY.get(kind);block=widget(kind)
        def add(state):
            dashboard=self.dashboard_for(panel,state)
            if len(dashboard['widgets'])>=30:raise ValueError('Limit: 30 widgets per dashboard.')
            block['config']['watchlistId']=state['watchlists'][0]['id'];dashboard['widgets'].append(block)
            for key,layout in dashboard['layouts'].items():
                width=12 if key=='xs' else definition.defaultSize[0]
                dashboard['layouts'][key]=ENGINE.insert(layout,dict(i=block['id'],x=0,y=0,w=width,h=definition.defaultSize[1],minW=definition.minSize[0],minH=definition.minSize[1]),dashboard['settings']['compact'])
        self.layout_store.mutate('Add '+definition.title,add)
        return block['id']
    def add_widget(self):
        title,ok=QInputDialog.getItem(self,'Add widget','Widget',[d.title for d in REGISTRY.definitions.values() if d.type!='unknown'],0,False)
        if ok:self.add_block(next(d.type for d in REGISTRY.definitions.values() if d.title==title))
    def remove_widget(self,ident,panel=None):
        panel=panel or self.tabs.currentWidget()
        def remove(state):
            dashboard=self.dashboard_for(panel,state);dashboard['widgets']=[w for w in dashboard['widgets'] if w['id']!=ident]
            for key,layout in dashboard['layouts'].items():dashboard['layouts'][key]=ENGINE.repair([g for g in layout if g['i']!=ident],dashboard['settings']['compact'])
        self.layout_store.mutate('Remove widget',remove)
        self.status.setText('Widget removed · Undo restores the block and its notes.')
    def duplicate_widget(self,ident,panel=None):
        panel=panel or self.tabs.currentWidget();new_id=uuid.uuid4().hex
        def duplicate(state):
            dashboard=self.dashboard_for(panel,state)
            if len(dashboard['widgets'])>=30:raise ValueError('Limit: 30 widgets.')
            block=deepcopy(next(w for w in dashboard['widgets'] if w['id']==ident));block['id']=new_id;block['title']+=' copy';dashboard['widgets'].append(block)
            for key,layout in dashboard['layouts'].items():
                geometry=next(deepcopy(g) for g in layout if g['i']==ident);geometry['i']=new_id
                dashboard['layouts'][key]=ENGINE.insert(layout,geometry,dashboard['settings']['compact'])
            old_key=('stock-'+panel.ticker if isinstance(panel,ResearchPanel) else dashboard['id'])+':'+ident
            state['notes'][old_key[:-len(ident)]+new_id]=state['notes'].get(old_key,'')
        self.layout_store.mutate('Duplicate widget',duplicate);return new_id
    def move_widget_to(self,ident,panel=None,copy=False,target_id=None):
        panel=panel or self.tabs.currentWidget()
        targets=[p for p in self.store.state['pages'] if p['id']!=self.dashboard_for(panel)['id']]
        if target_id is None:
            title,ok=QInputDialog.getItem(self,'Copy to dashboard' if copy else 'Move to dashboard','Dashboard',[p['name'] for p in targets],0,False)
            if not ok:return
            target_id=next(p['id'] for p in targets if p['name']==title)
        def move(state):
            source=self.dashboard_for(panel,state);target=next(p for p in state['pages'] if p['id']==target_id)
            if len(target['widgets'])>=30:raise ValueError('Destination has 30 widgets.')
            block=deepcopy(next(w for w in source['widgets'] if w['id']==ident));new_id=uuid.uuid4().hex if copy else ident;block['id']=new_id
            if any(w['id']==new_id for w in target['widgets']):new_id=uuid.uuid4().hex;block['id']=new_id
            target['widgets'].append(block)
            for key,layout in target['layouts'].items():
                geometry=next(deepcopy(g) for g in source['layouts'][key] if g['i']==ident);geometry['i']=new_id
                target['layouts'][key]=ENGINE.insert(layout,geometry,target['settings']['compact'])
            old_key=('stock-'+panel.ticker if isinstance(panel,ResearchPanel) else source['id'])+':'+ident
            state['notes'][target['id']+':'+new_id]=state['notes'].get(old_key,'')
            if not copy:
                source['widgets']=[w for w in source['widgets'] if w['id']!=ident]
                for key,layout in source['layouts'].items():source['layouts'][key]=ENGINE.repair([g for g in layout if g['i']!=ident],source['settings']['compact'])
        self.layout_store.mutate('Copy widget' if copy else 'Move widget to dashboard',move)
    def expand_widget(self,ident,panel=None):
        panel=panel or self.tabs.currentWidget();block=deepcopy(next(w for w in self.dashboard_for(panel)['widgets'] if w['id']==ident))
        dialog=QDialog(self);dialog.setWindowTitle(block['title']);dialog.resize(1000,700);layout=QVBoxLayout(dialog)
        from .dashboard.widget_frame import WidgetFrame
        config=deepcopy(panel.canvas.frames[ident].block['config']);block['config']=config
        frame=WidgetFrame(block,lambda f:self.create_component(f,panel));layout.addWidget(frame)
        dialog.showMaximized();dialog.exec();dialog.deleteLater()
    def new_dashboard(self):
        name,ok=QInputDialog.getText(self,'New dashboard','Name')
        if ok and name.strip():
            ident=uuid.uuid4().hex
            self.layout_store.mutate('Create dashboard',lambda state:state['pages'].append(page(ident,name.strip(),[])))
            self.page_id=ident;self.tabs.setCurrentWidget(self.dashboard_page);self.reload()
    def rename_dashboard(self):
        dashboard=self.current_page();name,ok=QInputDialog.getText(self,'Rename dashboard','Name',text=dashboard['name'])
        if ok and name.strip():self.layout_store.mutate('Rename dashboard',lambda state:self.current_page(state).__setitem__('name',name.strip()))
    def delete_dashboard(self):
        ident=self.current_page()['id']
        if ident in ('home','positions','trade-history','market-news') or self.stock_view or self.news_view:self.status.setText('Primary pages are preserved. Remove their widgets or create a custom dashboard.');return
        self.page_id='home'
        self.layout_store.mutate('Delete dashboard',lambda state:state.__setitem__('pages',[p for p in state['pages'] if p['id']!=ident]))
    def choose_dashboard(self,*_):
        ident=self.dashboards.currentData()
        if not ident:return
        reserved={'positions':self.watchlist_page,'trade-history':self.history_page,'market-news':self.news_page}
        if ident in reserved:self.tabs.setCurrentWidget(reserved[ident])
        else:self.page_id=ident;self.tabs.setCurrentWidget(self.dashboard_page)
        self.reload()
    def open_ticker(self):
        try:self.open_symbol(symbol(self.ticker.text()))
        except ValueError as exc:self.status.setText(str(exc))
    def open_symbol(self,ticker):
        ticker=symbol(ticker)
        if ticker not in self.research_tabs:
            panel=ResearchPanel(self,ticker);self.research_tabs[ticker]=panel;self.tabs.addTab(panel,ticker+' · Research')
        self.tabs.setCurrentWidget(self.research_tabs[ticker]);self.selected_symbol=ticker;self.ticker.setText(ticker);self.reload()
    def close_research_tab(self,index):
        panel=self.tabs.widget(index)
        if not isinstance(panel,ResearchPanel):return
        self.flush_notes();self.research_tabs.pop(panel.ticker,None);self.tabs.removeTab(index);panel.deleteLater()
    def customize_stock(self,checked):
        if not self.stock_view:return
        ticker=self.selected_symbol
        def customize(state):
            if checked:state['stockOverrides'].setdefault(ticker,deepcopy(state['stockTemplate']))
            else:state['stockOverrides'].pop(ticker,None)
        self.layout_store.mutate('Change stock layout scope',customize)
    def tab_changed(self,index):
        active=self.tabs.widget(index);self.stock_view=isinstance(active,ResearchPanel);self.news_view=active is self.news_page
        if self.stock_view:self.selected_symbol=active.ticker;self.ticker.setText(active.ticker)
        self.reload()
    def ensure_news_page(self):
        if self.news_sector not in self.store.state['newsPages']:
            source=deepcopy(next(p for p in self.store.state['pages'] if p['id']=='market-news'));source.update(id='news-'+self.news_sector,name=SECTORS[self.news_sector]);self.store.state['newsPages'][self.news_sector]=source
        dashboard=self.store.state['newsPages'][self.news_sector]
        if not any(w['type']=='headlines' for w in dashboard['widgets']):
            block=widget('headlines');dashboard['widgets'].append(block)
            for key,layout in dashboard['layouts'].items():dashboard['layouts'][key]=ENGINE.insert(layout,dict(i=block['id'],x=0,y=0,w=12,h=32),True)
    def select_sector(self,*_):
        self.news_sector=self.sectors.currentData() or 'all';self.ensure_news_page();self.render_panel(self.news_page)
    def widget_symbol(self,w):return self.selected_symbol if self.stock_view and w['config'].get('followSymbol') else w['config'].get('symbol','AAPL')
    @property
    def trade_rows(self):return self.ledger_component.trade_rows if hasattr(self,'ledger_component') else []
    def selected_ticker(self):
        row=self.watchlist_table.currentRow();return self.watchlist_table.item(row,0).text() if row>=0 else None
    def selected_watchlist(self):return next(w for w in self.store.state['watchlists'] if w['id']==self.watchlists.currentData())
    def add_symbol(self):
        ticker,ok=QInputDialog.getText(self,'Add ticker','US ticker')
        if ok:
            try:
                ticker=symbol(ticker);state=deepcopy(self.store.state);listing=next(w for w in state['watchlists'] if w['id']==self.watchlists.currentData())
                if ticker not in listing['symbols']:listing['symbols'].append(ticker)
                self.commit(state)
            except ValueError as exc:self.status.setText(str(exc))
    def remove_symbol(self):
        ticker=self.selected_ticker()
        if ticker:
            state=deepcopy(self.store.state);listing=next(w for w in state['watchlists'] if w['id']==self.watchlists.currentData());listing['symbols']=[s for s in listing['symbols'] if s!=ticker];self.commit(state)
    def sell_selected(self):
        ticker=self.selected_ticker()
        if ticker:self.sale_scenario(ticker)
    def data(self,kind,key):return self.data_layer.data(kind,key)
    def quote(self,ticker,period='1d'):return self.data('quote',ticker+'|'+period)
    def source_label(self,value,kind,key):return ('Cached · ' if value.get('stale') else '')+value.get('source','') if value else self.failures.get(kind+':'+key,'Not loaded · Refresh')
    def render_metrics(self):
        portfolio=self.store.portfolio();self.metrics[0].value.setText(money(portfolio['basis']));self.metrics[1].value.setText(money(portfolio['realized']))
        total=0;covered=0;self.unpriced=[]
        for ticker,p in portfolio['holdings'].items():
            if not p['quantity']:continue
            value=self.data('quote',ticker+'|1d')
            if value and not value.get('demo') and not value.get('stale') and value.get('currency')=='USD':total+=float(p['quantity'])*value['price'];covered+=1
            else:self.unpriced.append(ticker+' · No usable live USD quote')
        self.metrics[2].value.setText(money(total) if covered else '—');self.metrics[2].caption.setText('PARTIAL MARKET VALUE' if self.unpriced else 'MARKET VALUE')
        self.metrics[2].description.setText(f'{covered} of {covered+len(self.unpriced)} positions priced' if covered or self.unpriced else 'No positions yet · record a trade to start')
        self.valuation_details.setVisible(bool(self.unpriced))
    def show_unpriced(self):QMessageBox.information(self,'Positions excluded from market value','\n'.join(self.unpriced))
    def view_tables(self):
        result={}
        for panel in self.panels():
            for frame in panel.canvas.frames.values():
                component=frame.component
                group={'positions':'watchlist','ledger':'trades','headlines':'news'}.get(frame.block['type'])
                if group and component and component.table:result[group]=component.table
        return result
    def apply_view_settings(self):
        settings=self.store.state.get('viewSettings',{})
        for index,metric in enumerate(self.metrics):metric.setVisible(settings.get('metrics',[True]*3)[index])
        for name,table in self.view_tables().items():
            table.verticalHeader().setDefaultSectionSize(settings.get('rowHeight',42))
            for column in range(table.columnCount()):table.setColumnHidden(column,column not in settings.get('columns',{}).get(name,list(range(table.columnCount()))))
    def configure_views(self):
        dialog=ViewSettingsDialog(self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                values=validate_view_settings(dialog.values());state=deepcopy(self.store.state);state.setdefault('viewSettings',{}).update(values);self.commit(state)
            except ValueError as exc:self.status.setText(str(exc))
    def requests(self):return sorted(self.data_layer.requested)
    def start(self):
        if self.network_enabled:self.refresh_data()
    def refresh_data(self,*_,missing_only=False):self.data_layer.refresh()
    def set_mode(self,*_):
        state=deepcopy(self.store.state);state['dataMode']=self.mode.currentData();self.commit(state);self.data_layer.refresh()
    def market_result(self,kind,key,value):self.data_layer.result(kind,key,value)
    def market_failed(self,kind,key,message):self.data_layer.failed(kind,key,message)
    def market_finished(self):self.data_layer.finished();self.render_metrics()
    def render_watchlist(self):self.positions_component.safe_render()
    def render_trades(self):self.ledger_component.safe_render()
    def render_news(self):self.data_layer.changed.emit('news:market')
    def import_workspace(self):
        path,_=QFileDialog.getOpenFileName(self,'Import investment workspace','','JSON files (*.json)')
        if path:
            try:
                if Path(path).stat().st_size>10_000_000:raise ValueError('Workspace exceeds 10 MB.')
                state=validate_workspace(json.loads(Path(path).read_text()))
                if QMessageBox.question(self,'Import investments','Replace investment data with this file?')!=QMessageBox.StandardButton.Yes:return
                self.page_id='home';self.tabs.setCurrentWidget(self.dashboard_page);self.commit(state)
            except (ValueError,OSError) as exc:self.status.setText(str(exc))
    def export_workspace(self):
        self.flush_notes();path,_=QFileDialog.getSaveFileName(self,'Export investments','investments-workspace.json','JSON files (*.json)')
        if path:write_json(Path(path),self.store.state)
    def shutdown(self):self.layout_store.flush();self.data_layer.shutdown()
    def record_trade(self,ticker=None,row=None):
        if not isinstance(ticker,str):
            ticker,ok=QInputDialog.getText(self,'Record trade','Ticker',text=self.selected_symbol)
            if not ok:return
        try:ticker=symbol(ticker)
        except ValueError as exc:self.status.setText(str(exc));return
        dialog=TradeDialog(self,ticker,row)
        while dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                self.store.trade(ticker,dialog.values());self.reload();self.owner.refresh_investment_summary();return
            except (ValueError,OSError) as exc:dialog.error.setText(str(exc))
    def selected_trade(self):
        index=self.trade_table.currentRow()
        return self.trade_rows[index] if 0<=index<len(self.trade_rows) else None
    def edit_trade(self):
        row=self.selected_trade()
        if row:self.record_trade(*row)
    def delete_trade(self):
        row=self.selected_trade()
        if row and QMessageBox.question(self,'Delete trade','Delete this trade? Later sales must still have sufficient shares.')==QMessageBox.StandardButton.Yes:
            try:self.store.trade(*row,delete=True);self.reload();self.owner.refresh_investment_summary()
            except (ValueError,OSError) as exc:self.status.setText(str(exc))
    def tax_settings(self):
        dialog=QDialog(self);dialog.setWindowTitle('Sale estimate · 2026 North Carolina');layout=QVBoxLayout(dialog);form=QFormLayout()
        existing=self.store.state.get('taxProfile') or {}
        status=ScrollSafeComboBox()
        for key,title in STATUSES.items():status.addItem(title,key)
        status.setCurrentIndex(max(0,status.findData(existing.get('filingStatus','single'))));form.addRow('Filing status',status)
        fields={}
        for key,title in FIELDS.items():
            initial=existing.get(key,DEDUCTIONS[status.currentData()] if key=='deduction' else NC_DEDUCTIONS[status.currentData()] if key=='stateDeduction' else 0)
            fields[key]=dollars(initial);form.addRow(title,fields[key])
        status.currentIndexChanged.connect(lambda:(fields['deduction'].setValue(DEDUCTIONS[status.currentData()]),fields['stateDeduction'].setValue(NC_DEDUCTIONS[status.currentData()])))
        layout.addLayout(form)
        layout.addWidget(text('Estimate for 2026 full-year NC residents only. Include realized gains from this ledger and other accounts in the income inputs. Loss scenarios require separate review; wash sales, carryforwards, AMT, credits, and corporate actions are not modeled.','muted',True))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            state=deepcopy(self.store.state);state['taxProfile']=dict(year=2026,state='NC',filingStatus=status.currentData(),**{key:entry.value() for key,entry in fields.items()});self.commit(state)
    def sale_scenario(self,ticker):
        dialog=TradeDialog(self,ticker,scenario=True)
        while dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                trades=self.store.state['trades'].get(ticker,[]);sale=dialog.values();result=simulate_sale(trades,sale)
                message=f"Short-term gain: {money(result['shortGain'])}\nLong-term gain: {money(result['longGain'])}\n"
                profile=self.store.state.get('taxProfile')
                has_losses=result['hasLoss'] or any(d['gain']<0 for d in position(trades)['disposals'])
                if profile and sale['date'][:4]=='2026' and not has_losses:
                    tax=estimate_tax(profile,result['shortGain'],result['longGain']);message+=f"\nIncremental federal: {money(tax['federal'])}\nIncremental NC: {money(tax['state'])}\nTotal estimate: {money(tax['total'])}"
                else:message+='\nTax estimate unavailable: set up 2026 NC assumptions; loss histories need review.'
                QMessageBox.information(self,'FIFO sale scenario',message);return
            except (ValueError,OSError) as exc:dialog.error.setText(str(exc))
