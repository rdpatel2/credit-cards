"""Independent widget definitions, native components and declarative configuration."""
from dataclasses import dataclass
from datetime import date
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QLineEdit, QComboBox,
    QDialog, QDialogButtonBox, QFormLayout, QSpinBox, QCheckBox, QListWidget, QListWidgetItem)
from ..investments import METRICS, RANGES, position, symbol


def money(value):
    return '—' if value is None else f"{'−' if value < 0 else ''}${abs(value):,.2f}"


def numeric(value):
    return '—' if value is None else f'{float(value):,.3f}'.rstrip('0').rstrip('.')


def requirements(kind, config, state):
    ticker = config.get('symbol', 'AAPL')
    listing = next((w for w in state['watchlists'] if w['id'] == config.get('watchlistId')), state['watchlists'][0])
    if kind == 'chart': return {('quote', ticker + '|' + config.get('range', '1mo'))}
    if kind == 'quote': return {('quote', ticker + '|1d')}
    if kind in ('metrics', 'analyst'):
        tickers = listing['symbols'] if kind == 'analyst' and config.get('analystScope') == 'watchlist' else [ticker]
        return {('modules', s) for s in tickers}
    if kind in ('news', 'headlines'): return {('news', 'market' if kind == 'headlines' or config.get('newsScope') == 'market' else ticker)}
    if kind == 'earnings': return {('earnings', config.get('earningsDate') or date.today().isoformat())}
    if kind in ('watchlist', 'positions', 'market', 'movers'):
        tickers = ['SPY','QQQ','DIA','IWM'] if kind == 'market' else listing['symbols']
        return {('quote', s + '|1d') for s in tickers}
    return set()


@dataclass(frozen=True)
class WidgetDefinition:
    type: str
    title: str
    icon: str
    description: str
    category: str
    defaultSize: tuple
    minSize: tuple
    configSchema: dict
    Component: object
    ConfigPanel: object
    dataRequirements: object


class WidgetRegistry:
    def __init__(self): self.definitions = {}
    def register(self, definition): self.definitions[definition.type] = definition
    def get(self, kind): return self.definitions.get(kind, self.definitions['unknown'])


class DataWidget(QStackedWidget):
    footerChanged = Signal(str)
    crashed = Signal(str)
    def __init__(self, definition, config, size, isEditing, onConfigChange, layer):
        super().__init__()
        self.definition, self.config, self.onConfigChange, self.layer = definition, dict(config), onConfigChange, layer
        self.isEditing = isEditing
        self.footer_text = ''
        self.setMinimumSize(0,0)
        self.content = QWidget()
        self.layout_body = QVBoxLayout(self.content)
        self.layout_body.setContentsMargins(0,0,0,0)
        self.layout_body.setSpacing(4)
        self.table = None
        self.editor = None
        self.chart = None
        self.controls = None
        self.message_page = QWidget()
        messages = QVBoxLayout(self.message_page)
        messages.setContentsMargins(8,8,8,8)
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        messages.addWidget(self.message_label,1)
        retry = QPushButton('Refresh')
        retry.clicked.connect(self.refresh)
        messages.addWidget(retry)
        self.addWidget(self.content)
        self.addWidget(self.message_page)
        kind = definition.type
        if kind == 'notes':
            self.editor = QTextEdit()
            self.editor.setMinimumSize(0,0)
            self.editor.setPlaceholderText('Your thesis, questions, and next steps…')
            self.editor.setPlainText(layer.state['notes'].get(config.get('_noteKey',''),''))
            self.editor.textChanged.connect(lambda: onConfigChange({'note':self.editor.toPlainText()}))
            self.layout_body.addWidget(self.editor)
        elif kind == 'chart':
            from .price_chart import PriceChart
            self.chart = PriceChart([])
            self.chart.setMinimumSize(0,0)
            self.layout_body.addWidget(self.chart)
        elif kind != 'unknown':
            if kind in ('positions', 'ledger', 'headlines'):
                self.controls = QWidget()
                controls = QHBoxLayout(self.controls)
                controls.setContentsMargins(0,0,0,0)
                if kind == 'positions':
                    self.watchlists = QComboBox()
                    self.watchlists.currentIndexChanged.connect(lambda: onConfigChange({'watchlistId':self.watchlists.currentData()}))
                    controls.addWidget(self.watchlists,1)
                    for label, action in [('Add ticker','add_symbol'),('Record trade','record_trade'),('Sell','sale_selected')]:
                        button=QPushButton(label);button.setObjectName('rowAction')
                        button.clicked.connect(lambda checked=False,a=action:layer.action(a));controls.addWidget(button)
                elif kind == 'ledger':
                    self.filter = QLineEdit();self.filter.setPlaceholderText('Filter ticker')
                    self.filter.textChanged.connect(lambda: self.safe_render())
                    controls.addWidget(self.filter,1)
                    for label, action in [('Record trade','record_trade'),('Edit','edit_trade'),('Delete','delete_trade')]:
                        button=QPushButton(label);button.setObjectName('rowAction')
                        button.clicked.connect(lambda checked=False,a=action:layer.action(a));controls.addWidget(button)
                else:
                    self.filter = QLineEdit();self.filter.setPlaceholderText('Search headlines')
                    self.filter.textChanged.connect(lambda:self.safe_render())
                    controls.addWidget(self.filter,1)
                self.layout_body.addWidget(self.controls)
            self.table = QTableWidget()
            self.table.setMinimumSize(0,0)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.verticalHeader().hide()
            self.table.horizontalHeader().setMinimumSectionSize(30)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.table.itemDoubleClicked.connect(self.open_row)
            self.layout_body.addWidget(self.table,1)
        layer.changed.connect(self.on_data_changed)
        self.set_config(config)

    def set_config(self, config):
        self.config = dict(config)
        self.keys = self.definition.dataRequirements(config, self.layer.state)
        self.layer.subscribe(self.keys)
        self.safe_render()

    @Slot(str)
    def on_data_changed(self, key):
        if key == 'workspace' or any(key == k + ':' + v for k,v in self.keys):
            if key=='workspace':
                self.keys=self.definition.dataRequirements(self.config,self.layer.state)
                self.layer.subscribe(self.keys)
            self.safe_render()

    def safe_render(self):
        try:
            self.render()
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            self.crashed.emit(str(exc))

    def refresh(self):
        self.layer.refresh(self.keys)
        self.safe_render()

    def state_message(self, message):
        self.message_label.setText(message)
        self.setCurrentWidget(self.message_page)

    def render(self):
        kind=self.definition.type;config=self.config;ticker=config.get('symbol','AAPL')
        if kind=='unknown':
            self.state_message('This widget type is unavailable. Its saved configuration is preserved. Remove it or install its component to continue.')
            return
        if kind=='notes':
            self.setCurrentWidget(self.content);self.set_footer('Notes · saved on this device');return
        statuses=[self.layer.status(k,v) for k,v in self.keys]
        loaded=[self.layer.data(k,v) for k,v in sorted(self.keys)]
        sources=[f"{v.get('source','Public data')} · {v.get('asOf','')[:19]}" for v in loaded if v]
        self.set_footer(('Cached · ' if 'stale' in statuses else '') + (sources[0] if sources else 'Refresh to load data' if self.keys else 'Saved on this device'))
        if self.keys and not any(loaded) and kind not in ('watchlist','positions','market','movers'):
            if 'loading' in statuses:
                self.state_message('Loading '+self.definition.title.lower()+'…');return
            if 'error' in statuses:
                errors=[self.layer.failures.get(k+':'+v,'') for k,v in self.keys]
                self.state_message('Could not load '+self.definition.title.lower()+'. '+next((e for e in errors if e),'')+' Use Refresh to retry.');return
            self.state_message('No data loaded for '+ticker+'. Refresh to load '+self.definition.title.lower()+'.');return
        self.setCurrentWidget(self.content)
        listing=next((w for w in self.layer.state['watchlists'] if w['id']==config.get('watchlistId')),self.layer.state['watchlists'][0])
        limit=max(1,min(120,int(config.get('limit',8))))
        if kind=='chart':
            data=self.layer.data('quote',ticker+'|'+config.get('range','1mo')) or {}
            self.chart.points=data.get('points',[]);self.chart.currency=data.get('currency','USD');self.chart.update();return
        if kind in ('quote','metrics','analyst'):
            data=self.layer.data('quote',ticker+'|1d') if kind=='quote' else self.layer.data('modules',ticker)
            data=data or {}
            if kind=='quote':
                rows=[['Price',money(data.get('price'))],['Day change',numeric(data.get('changePercent'))+'%'],['Volume',numeric(data.get('volume'))],['Day high / low',numeric(data.get('high'))+' / '+numeric(data.get('low'))]]
            elif kind=='metrics':
                rows=[[METRICS.get(k,k),numeric(data.get('values',{}).get(k))] for k in config.get('metrics',list(METRICS)[:8])]
            else:
                periods=data.get('periods',[])
                if config.get('analystScope')=='watchlist':
                    totals={}
                    for s in listing['symbols']:
                        for period in (self.layer.data('modules',s) or {}).get('periods',[]):
                            totals.setdefault(period['period'],[0]*5)
                            totals[period['period']]=[a+b for a,b in zip(totals[period['period']],period['counts'])]
                    periods=[dict(period=k,counts=v) for k,v in totals.items()]
                rows=[[p['period'],' / '.join(str(n) for n in p['counts'])] for p in periods]
                if not rows:
                    self.state_message('No analyst coverage is available for '+ticker+'. Refresh or choose another ticker in Configure.');return
            self.rows(['Period' if kind=='analyst' else 'Metric','Value'],rows)
        elif kind in ('watchlist','positions','market','movers'):
            if kind=='positions':
                self.watchlists.blockSignals(True);self.watchlists.clear()
                for w in self.layer.state['watchlists']:self.watchlists.addItem(w['name'],w['id'])
                self.watchlists.setCurrentIndex(max(0,self.watchlists.findData(listing['id'])));self.watchlists.blockSignals(False)
            tickers=['SPY','QQQ','DIA','IWM'] if kind=='market' else list(listing['symbols'])
            if kind=='movers':tickers.sort(key=lambda s:(self.layer.data('quote',s+'|1d') or {}).get('changePercent',0),reverse=config.get('direction','gainers')=='gainers')
            rows=[]
            self.symbols=tickers[:limit] if kind!='positions' else tickers
            for s in self.symbols:
                q=self.layer.data('quote',s+'|1d') or {};p=position(self.layer.state['trades'].get(s,[]))
                if kind=='positions':rows.append([s,q.get('name','—'),money(q.get('price')),numeric(q.get('changePercent'))+'%',numeric(p['quantity']),money(p['averageCost']),money(p['basis'])])
                else:rows.append([s,money(q.get('price')),numeric(q.get('changePercent'))+'%'])
            self.rows(['Ticker','Company','Price','Day %','Shares','Average cost','Remaining basis'] if kind=='positions' else ['Ticker','Price','Day %'],rows)
        elif kind in ('trades','ledger'):
            query=self.filter.text().upper() if kind=='ledger' else ''
            self.trade_rows=sorted([(s,t) for s,trades in self.layer.state['trades'].items() if query in s for t in trades],key=lambda pair:pair[1]['date'],reverse=True)
            rows=[[t['date'],s,t['side'].title(),numeric(t['quantity']),money(t['price']),money(t['fees'])] for s,t in self.trade_rows]
            self.rows(['Date','Ticker','Side','Shares','Price','Fees'],rows if kind=='ledger' else rows[:limit])
        elif kind in ('news','headlines','earnings'):
            req=next(iter(self.keys));data=self.layer.data(*req) or {}
            query=self.filter.text().casefold() if kind=='headlines' else ''
            sector=config.get('_sector',config.get('sector','all'))
            items=[i for i in data.get('items',[]) if kind=='earnings' or (query in i['title'].casefold() and (sector=='all' or sector in i.get('sectors',[])))]
            self.articles=items if kind=='headlines' else items[:limit]
            if not items:
                if kind=='headlines':
                    self.rows(['Headline','Published','Sector tags'],[])
                    self.set_footer('No matching headlines · change search/sector or Refresh')
                    return
                self.state_message('No '+('earnings events for this date. Configure another date or refresh.' if kind=='earnings' else 'matching headlines. Change the search/sector or refresh.'));return
            rows=[[i.get('symbol',''),i.get('name',''),i.get('epsForecast','—')] if kind=='earnings' else [i['title'],i.get('date',''),', '.join(i.get('sectors',[]))] for i in self.articles]
            self.rows(['Ticker','Company','EPS forecast'] if kind=='earnings' else ['Headline','Published','Sector tags'],rows)
        self.on_size(self.size())

    def rows(self, headers, rows):
        selected=self.table.item(self.table.currentRow(),0)
        selected=selected.text() if selected else None
        offset=self.table.verticalScrollBar().value()
        self.table.setColumnCount(len(headers));self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):self.table.setItem(i,j,QTableWidgetItem(str(value)))
            if selected and str(row[0])==selected:self.table.selectRow(i)
        self.table.verticalScrollBar().setValue(offset)

    def set_footer(self, value):
        self.footer_text=value;self.footerChanged.emit(value)

    def on_size(self,size):
        if self.table:
            columns=self.table.columnCount()
            visible=2 if size.width()<320 else 3 if size.width()<480 else columns
            group={'positions':'watchlist','ledger':'trades','headlines':'news'}.get(self.definition.type)
            selected=self.layer.state.get('viewSettings',{}).get('columns',{}).get(group,list(range(columns)))
            shown=selected[:visible]
            for column in range(columns):self.table.setColumnHidden(column,column not in shown)
            self.table.verticalHeader().setDefaultSectionSize(32 if size.width()<320 else 42)
        if self.controls:
            for button in self.controls.findChildren(QPushButton):button.setVisible(size.width()>=320)

    def content_height(self):
        if self.table:return min(900,self.table.rowCount()*42+48+(40 if self.controls else 0))
        if self.editor:return max(140,min(800,self.editor.document().size().height()+24))
        return 230

    def open_row(self,item):
        kind=self.definition.type
        if kind in ('watchlist','positions','movers','market'):
            self.layer.action('open_symbol',self.table.item(item.row(),0).text())
        elif kind in ('news','headlines') and self.articles[item.row()].get('url'):
            self.layer.action('open_url',self.articles[item.row()]['url'])


class ConfigPanel(QDialog):
    def __init__(self, definition, config, layer, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Configure '+definition.title)
        self.definition,self.config=definition,dict(config)
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.fields={}
        for key,(label,kind) in definition.configSchema.items():
            if kind=='bool':field=QCheckBox();field.setChecked(bool(config.get(key,True)))
            elif kind=='int':field=QSpinBox();field.setRange(1,120);field.setValue(config.get(key,8))
            elif kind=='watchlist':
                field=QComboBox()
                for w in layer.state['watchlists']:field.addItem(w['name'],w['id'])
                field.setCurrentIndex(max(0,field.findData(config.get(key))))
            elif kind=='metrics':
                field=QListWidget();field.setMaximumHeight(240)
                for metric,title in METRICS.items():
                    item=QListWidgetItem(title);item.setData(Qt.ItemDataRole.UserRole,metric)
                    item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if metric in config.get(key,list(METRICS)[:8]) else Qt.CheckState.Unchecked)
                    field.addItem(item)
            elif isinstance(kind,tuple):field=QComboBox();field.addItems(kind);field.setCurrentText(config.get(key,kind[0]))
            else:field=QLineEdit(str(config.get(key,'')))
            form.addRow(label,field);self.fields[key]=field
        self.error=QLabel();self.error.setWordWrap(True);layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def values(self):
        config=dict(self.config)
        for key,field in self.fields.items():
            if isinstance(field,QListWidget):
                config[key]=[field.item(i).data(Qt.ItemDataRole.UserRole) for i in range(field.count()) if field.item(i).checkState()==Qt.CheckState.Checked]
                if not config[key]:raise ValueError('Choose at least one metric.')
                continue
            config[key]=field.isChecked() if isinstance(field,QCheckBox) else field.value() if isinstance(field,QSpinBox) else field.currentData() if isinstance(field,QComboBox) and key=='watchlistId' else field.currentText() if isinstance(field,QComboBox) else field.text()
        if 'symbol' in config:config['symbol']=symbol(config['symbol'])
        if config.get('earningsDate'):date.fromisoformat(config['earningsDate'])
        return config


REGISTRY=WidgetRegistry()
TICKER=dict(symbol=('Ticker','text'),followSymbol=('Follow this stock','bool'))
WATCH=dict(watchlistId=('Watchlist','watchlist'),limit=('Row limit','int'))
definitions=[
 ('watchlist','Watchlist','Portfolio','Saved tickers, prices and daily changes.',WATCH),
 ('positions','Watchlists and positions','Portfolio','Holdings, cost basis and watchlist actions.',WATCH),
 ('trades','Recent trades','Portfolio','Your most recent recorded transactions.',dict(limit=('Row limit','int'))),
 ('ledger','Trade history','Portfolio','Search and edit recorded trades.',{}),
 ('chart','Price chart','Research','Closing prices over the selected period.',dict(**TICKER,range=('Chart range',RANGES))),
 ('quote','Key statistics','Research','Price, volume and daily trading range.',TICKER),
 ('metrics','Advanced metrics','Research','Company fundamentals and valuation metrics.',dict(**TICKER,metrics=('Metrics','metrics'))),
 ('analyst','Analyst trend','Research','Analyst recommendation counts by period.',dict(**TICKER,**WATCH,analystScope=('Coverage scope',('stock','watchlist')))),
 ('movers','Daily movers','Market','Watchlist daily gains and losses.',dict(**WATCH,direction=('Direction',('gainers','losers')))),
 ('market','Market overview','Market','Benchmark ETF prices and daily changes.',dict(limit=('Row limit','int'))),
 ('news','News','Market','Company or market headlines.',dict(**TICKER,limit=('Row limit','int'),newsScope=('Headlines',('company','market')))),
 ('headlines','Market headlines','Market','Searchable sector-filtered market headlines.',{}),
 ('earnings','Earnings calendar','Market','Events and EPS forecasts for a selected date.',dict(earningsDate=('Date (YYYY-MM-DD)','text'),limit=('Row limit','int'))),
 ('notes','Research notes','Personal','Your saved thesis and research notes.',{}),
 ('unknown','Unavailable widget','Other','Preserved block whose component is unavailable.',{}),
]
for kind,title,category,description,schema in definitions:
    REGISTRY.register(WidgetDefinition(kind,title,'▦',description,category,(12 if kind in ('positions','ledger','headlines') else 6,32),(3,18),schema,DataWidget,ConfigPanel,lambda config,state,k=kind:requirements(k,config,state)))
