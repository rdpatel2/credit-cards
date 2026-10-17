"""Reviewable broker CSV imports and explicit investment-account metadata."""
from copy import deepcopy
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QFileDialog, QCheckBox, QTableWidgetItem, QDialogButtonBox, QHeaderView)
from .ui import text, ScrollSafeComboBox, FitDialog
from .import_ui import grid
from .investments import INVESTMENT_ACCOUNT_TYPES
from .broker_import import parse_trade_file, parse_positions, build_preview, commit_preview, new_account


class InvestmentAccountsDialog(FitDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.owner=parent
        self.setWindowTitle('Investment accounts')
        layout=QVBoxLayout(self)
        layout.addWidget(text('Account identity keeps FIFO lots separate. These USD trade accounts are separate from manual net-worth balances; balances are never added automatically. Unassigned history has unknown tax treatment.', 'muted',True))
        self.accounts=ScrollSafeComboBox()
        self.accounts.addItem('Create a new account',None)
        for account in parent.store.state['investmentAccounts']:
            self.accounts.addItem(account['name'],account['id'])
        self.accounts.currentIndexChanged.connect(self.choose)
        form=QFormLayout()
        self.name=QLineEdit()
        self.owner_name=QLineEdit()
        self.kind=ScrollSafeComboBox()
        for key,title in INVESTMENT_ACCOUNT_TYPES.items():self.kind.addItem(title,key)
        for label,control in [('Account',self.accounts),('Name',self.name),('Owner',self.owner_name),('Type',self.kind)]:form.addRow(label,control)
        layout.addLayout(form)
        self.source=text('Local recorded account; no service-copy mapping.','muted',True);self.source.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.source)
        self.status=text('','muted',True)
        layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(600,340)

    def choose(self,*_):
        account=next((a for a in self.owner.store.state['investmentAccounts'] if a['id']==self.accounts.currentData()),None)
        self.name.setText(account['name'] if account else '')
        self.owner_name.setText(account['owner'] if account else '')
        self.kind.setCurrentIndex(self.kind.findData(account['kind'] if account else 'unknown'))
        from .shared_sync import investment_signature
        source=next((row for row in self.owner.store.state.get('serviceCopies',[]) if account and row['local_id']==account['id']),None)
        changed=bool(source and source['local_signature']!=investment_signature(self.owner.store.state,account['id']))
        self.source.setText(('Last copied service snapshot v'+str(source['remote_version'])+' · '+source['origin']+'\nCopied '+source['imported_at']+(' · local records changed since this copy.' if changed else ' · recorded history matches this copy.')+' Not a live bank/broker certification.') if source else 'Local recorded account; no service-copy mapping.')

    def save(self):
        try:
            if not self.owner.layout_store.flush():raise ValueError('Save the current workspace successfully before editing accounts.')
            state=deepcopy(self.owner.store.state)
            ident=self.accounts.currentData()
            account=next((a for a in state['investmentAccounts'] if a['id']==ident),None)
            values=dict(name=self.name.text().strip(),owner=self.owner_name.text().strip(),kind=self.kind.currentData())
            if account:account.update(values)
            else:state['investmentAccounts'].append(new_account(**values))
            self.owner.store.save(state)
            self.owner.reload()
            self.accept()
        except (ValueError,OSError) as exc:self.status.setText(str(exc))


class BrokerImportDialog(FitDialog):
    def __init__(self,parent):
        super().__init__(parent)
        self.owner=parent
        self.rows=[]
        self.trade_summary=''
        self.snapshots=[]
        self.preview=None
        self.mappings={}
        self.setWindowTitle('Import broker CSV · preview and reconcile')
        layout=QVBoxLayout(self)
        layout.addWidget(text('Choose a Vanguard custom activity report or the documented generic trade CSV. Activity reports import BUY and SELL rows using trade date, ticker, fractional shares, price and fees. Include earlier purchases to establish cost basis for sales. Reinvestments, splits and other activity are ignored, so recorded holdings may differ from current broker holdings. USD only.', 'muted',True))
        layout.addWidget(text('For activity reports, select the same destination account on later uploads. Matching account type, side, trade date, ticker, shares, price and fees are skipped, including repeated rows within one file. The report has no transaction IDs, so separate executions with identical values are treated as duplicates. Map reports from separate brokerage accounts to separate investment accounts.', 'muted',True))
        controls=QHBoxLayout()
        for label,handler in [('Choose trade CSV',self.load_trades),('Choose position snapshot (optional)',self.load_positions),('Preview import',self.make_preview)]:
            button=QPushButton(label);button.clicked.connect(handler);controls.addWidget(button)
        layout.addLayout(controls)
        history=QPushButton('Review saved import history…')
        history.clicked.connect(self.show_history)
        layout.addWidget(history)
        self.files=text('No CSV selected. Position snapshots verify only the listed account/ticker pairs; omitted holdings remain unverified.', 'muted',True)
        layout.addWidget(self.files)
        self.mapping_table=grid(['CSV account','Map to investment account'])
        self.mapping_table.setMaximumHeight(160)
        self.mapping_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.mapping_table)
        self.results=grid(['Record / account','Ticker','Action / date','Shares / recorded','Broker / price','Difference / fees'])
        self.results.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Fixed)
        self.results.setColumnWidth(0,160)
        layout.addWidget(self.results,1)
        self.duplicates=QCheckBox('I reviewed possible duplicate executions and intend to add them.')
        self.variances=QCheckBox('I reviewed the share differences; keep them visible without altering trades to force a match.')
        self.duplicates.setVisible(False);self.variances.setVisible(False)
        layout.addWidget(self.duplicates);layout.addWidget(self.variances)
        self.status=text('Nothing is saved until you confirm a valid preview.', 'muted',True)
        layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.confirm=buttons.addButton('Confirm import',QDialogButtonBox.ButtonRole.AcceptRole)
        self.confirm.setEnabled(False)
        self.confirm.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(1050,650)

    def invalidate(self,*_):
        self.preview=None
        self.confirm.setEnabled(False)
        self.results.setRowCount(0)
        self.duplicates.setChecked(False);self.variances.setChecked(False)
        self.duplicates.setVisible(False);self.variances.setVisible(False)
        self.status.setText('CSV or account mapping changed. Preview again before confirming.')

    def load_trades(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose trade CSV or Vanguard custom activity report','','CSV (*.csv)')
        if not path:return
        self.invalidate()
        self.rows=[]
        self.trade_summary=''
        if hasattr(self,'trade_path'):del self.trade_path
        try:
            parsed=parse_trade_file(path)
            self.rows=parsed['rows']
            if parsed['format']=='vanguard_activity':
                buys=sum(row['side']=='buy' for row in self.rows)
                sells=sum(row['side']=='sell' for row in self.rows)
                self.trade_summary=f"Vanguard activity report: {buys} BUY rows; {sells} SELL rows; {parsed['ignored']} other activity rows ignored. "
            self.trade_path=Path(path).name
            self.rebuild_mapping()
        except (OSError,ValueError,UnicodeError) as exc:
            self.rebuild_mapping()
            self.status.setText('Could not read trades: '+str(exc))

    def load_positions(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose broker position snapshot','','CSV (*.csv)')
        if not path:return
        self.invalidate()
        self.snapshots=[]
        try:
            self.snapshots=parse_positions(path)
            self.position_path=Path(path).name
            self.rebuild_mapping()
        except (OSError,ValueError,UnicodeError) as exc:self.status.setText('Could not read positions: '+str(exc))

    def rebuild_mapping(self):
        prior={label:control.currentData() for label,control in self.mappings.items()}
        self.invalidate()
        self.mappings={}
        labels=sorted({row['account'] for row in [*self.rows,*self.snapshots]})
        self.mapping_table.setRowCount(len(labels))
        for i,label in enumerate(labels):
            self.mapping_table.setItem(i,0,QTableWidgetItem(label))
            combo=ScrollSafeComboBox()
            combo.addItem('Create '+label+' (tax type unknown)',None)
            for account in self.owner.store.state['investmentAccounts']:
                combo.addItem(account['name'],account['id'])
                if account['name']==label:combo.setCurrentIndex(combo.count()-1)
            if label in prior:combo.setCurrentIndex(max(0,combo.findData(prior[label])))
            combo.currentIndexChanged.connect(self.invalidate)
            combo.setAccessibleName('Map CSV account '+label)
            self.mapping_table.setCellWidget(i,1,combo)
            self.mapping_table.setRowHeight(i,48)
            self.mappings[label]=combo
        self.files.setText(self.trade_summary+f"Trades: {getattr(self,'trade_path','none')} · Position snapshot: {getattr(self,'position_path','none')}. Snapshot coverage is limited to listed pairs; missing pairs remain unverified.")

    def make_preview(self):
        self.invalidate()
        try:
            if not self.rows:raise ValueError('Choose a trade CSV first.')
            if not self.owner.layout_store.flush():raise ValueError('Save the current workspace successfully before previewing an import.')
            mapping,new={},[]
            for label,control in self.mappings.items():
                ident=control.currentData()
                if ident is None:
                    account=new_account(label)
                    new.append(account);ident=account['id']
                mapping[label]=ident
            sources=[getattr(self,key) for key in ('trade_path','position_path') if hasattr(self,key)]
            self.preview=build_preview(self.owner.store.state,self.rows,mapping,new,self.snapshots,sources)
            values=[]
            for ticker,row in self.preview['added']:
                values.append([row['externalId'],ticker,row['side']+' · '+row['date'],row['quantity'],row['price'],row['fees']])
            for ticker,row in self.preview['skipped']:
                values.append(['Skip duplicate',ticker,row['side']+' · '+row['date'],row['quantity'],row['price'],row['fees']])
            for row in self.preview['reconciliation']:
                values.append([row['accountId'],row['ticker'],'Reconcile · '+row['as_of'],str(row['recorded']),str(row['broker']),str(row['difference'])])
            self.results.setRowCount(len(values))
            for i,row in enumerate(values):
                for j,value in enumerate(row):self.results.setItem(i,j,QTableWidgetItem(str(value)))
            self.duplicates.setVisible(bool(self.preview['possible']))
            self.variances.setVisible(any(row['difference'] for row in self.preview['reconciliation']))
            self.status.setText(f"Add {len(self.preview['added'])} trades · skip {len(self.preview['skipped'])} identical transactions · review {len(self.preview['possible'])} possible duplicate executions. FIFO validated separately per account. "+('No broker snapshot supplied: shares are unverified.' if not self.snapshots else f"Compared {len(self.preview['reconciliation'])} listed account/ticker snapshots."))
            self.confirm.setEnabled(True)
        except (ValueError,OSError) as exc:self.status.setText('Preview unavailable: '+str(exc))

    def save(self):
        if self.preview is None:return
        try:
            commit_preview(self.owner.store,self.preview,self.duplicates.isChecked(),self.variances.isChecked())
            self.owner.reload()
            self.owner.owner.refresh_investment_summary()
            self.owner.status.setText(f"Imported {len(self.preview['added'])} broker trades; skipped {len(self.preview['skipped'])} identical transactions. "+('Reconciliation differences were acknowledged; inspect the ledger.' if any(row['difference'] for row in self.preview['reconciliation']) else ''))
            self.accept()
        except (ValueError,OSError) as exc:self.status.setText('Import not saved: '+str(exc))

    def show_history(self):
        dialog=FitDialog(self)
        dialog.setWindowTitle('Saved broker import history')
        layout=QVBoxLayout(dialog)
        report=text('','muted',True)
        layout.addWidget(report)
        table=grid(['Imported (UTC)','Source / account','Ticker','Snapshot date','Recorded shares','Broker shares','Difference'])
        layout.addWidget(table,1)
        values=[]
        history=self.owner.store.state.get('brokerImports',[])
        accounts={a['id']:a['name'] for a in self.owner.store.state['investmentAccounts']}
        for record in reversed(history):
            if not record['reconciliation']:
                values.append([record['importedAt'],', '.join(record['files']),'Unverified','—','—','—','—'])
            for row in record['reconciliation']:
                values.append([record['importedAt'],accounts[row['accountId']],row['ticker'],row['as_of'],row['recorded'],row['broker'],row['difference']])
        table.setRowCount(len(values))
        for i,row in enumerate(values):
            for j,value in enumerate(row):table.setItem(i,j,QTableWidgetItem(value))
        report.setText(f'{len(history)} saved import runs. Comparisons describe the records at import time, not current holdings. Differences are preserved after acknowledgement. '+('No imports recorded yet.' if not history else 'Investment workspace exports and full backups include this history.'))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(1100,650)
        dialog.exec()
