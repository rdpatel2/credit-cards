"""Selected institution current-balance review and authenticated recheck."""
from datetime import datetime
from decimal import Decimal
import json
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QTabWidget,QLineEdit,
    QCheckBox,QDialogButtonBox,QTextEdit,QPushButton,QTableWidgetItem,QMessageBox)
from .ui import FitDialog
from .connected_ui import text
from .planning_ui import choices,dated
from .planning import ACCOUNT_TYPES
from .import_ui import grid
from .institution_balances import InstitutionBalances,account_value,snapshot_value
from .shared_sync import moment


class InstitutionBalanceReview(FitDialog):
    def __init__(self,parent,snapshot):
        super().__init__(parent);self.service_page=parent;self.owner=parent.owner;self.manager=parent.manager;self.client=parent.client
        self.context=(self.client.url,self.client.identity['id'],self.client.token);self.snapshot=snapshot;self.connection=snapshot['id'];self.preview=None;self.saved_result=None;self._subscribed=True;self.pending=None
        self.model=InstitutionBalances(self.owner.planning_store)
        self.setWindowTitle('Review institution balance for your desktop');self.resize(980,700)
        layout=QVBoxLayout(self);layout.addWidget(text(f"Service: {self.client.url} · identity {self.client.identity['username']} · {snapshot['environment'].upper()}. This imports one saved current account balance, not transactions, holdings or historical cost basis.",'muted',True))
        form=QFormLayout();self.account=choices([(row['account_id'],str(row.get('name') or row['account_id'])+' · '+str(row.get('mask') or 'no mask')) for row in snapshot.get('accounts',[]) if isinstance(row,dict) and isinstance(row.get('account_id'),str)]);form.addRow('Provider account',self.account);layout.addLayout(form)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        page=QWidget();box=QVBoxLayout(page);form=QFormLayout()
        self.destination=choices([(None,'Create a separate local account')]+[(row['id'],row['name']+' · '+row['currency']) for row in self.owner.planning_store.accounts()])
        self.local_name=QLineEdit();self.local_name.setMaxLength(120);self.owner_name=QLineEdit();self.owner_name.setMaxLength(120);self.kind=choices([(key,value[0]) for key,value in ACCOUNT_TYPES.items()]);self.day=dated(datetime.now().date().isoformat())
        for title,field in [('Local destination',self.destination),('Local account name',self.local_name),('Owner label',self.owner_name),('Reviewed local account type',self.kind),('Recorded balance date',self.day)]:form.addRow(title,field)
        self.terms_form=form;self.apr=QLineEdit();self.apr.setMaxLength(20);self.apr.setPlaceholderText('Reviewed APR percent, e.g. 19.99');self.minimum=QLineEdit();self.minimum.setMaxLength(30);self.minimum.setPlaceholderText('Reviewed minimum payment in account currency')
        form.addRow('New debt APR (%)',self.apr);form.addRow('New debt minimum payment',self.minimum);form.setRowVisible(self.apr,False);form.setRowVisible(self.minimum,False)
        box.addLayout(form);self.date_notice=text('','muted',True);box.addWidget(self.date_notice)
        box.addStretch();self.tabs.addTab(page,'Destination')
        changes_page=QWidget();changes_layout=QVBoxLayout(changes_page);self.changes=grid(['Record / field','Local before','Incoming balance']);changes_layout.addWidget(self.changes,1);self.tabs.addTab(changes_page,'Changes')
        source_page=QWidget();source_layout=QVBoxLayout(source_page);self.incoming=QTextEdit();self.incoming.setReadOnly(True);source_layout.addWidget(self.incoming,1);self.tabs.addTab(source_page,'Selected source')
        self.before=QTextEdit();self.before.setReadOnly(True);self.tabs.addTab(self.before,'Local records / mapping')
        source_layout.addWidget(text('Other dates, account APR/minimum payments and private goal links remain. The selected date can replace a local value. Investment total value is for this balance sheet only; no lots, executions, dividends, returns or tax values are inferred. Avoid counting the same asset in another local account.','muted',True))
        self.reviewed=QCheckBox('I reviewed balance, currency, account type, source and recorded date');self.replace=QCheckBox('I reviewed updating this existing account, including local edits');self.duplicates=QCheckBox('I reviewed same-name accounts and possible double counting');self.sandbox=QCheckBox('I reviewed SANDBOX synthetic data affecting my local balance sheet')
        for field in (self.reviewed,self.replace,self.duplicates,self.sandbox):layout.addWidget(field)
        self.sandbox.setVisible(snapshot['environment']=='sandbox')
        self.status=text('Choose a provider account and review the local destination.','muted',True);layout.addWidget(self.status)
        detach=QPushButton('Detach selected provider/local mapping…');detach.clicked.connect(self.detach);layout.addWidget(detach)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('Recheck source and save reviewed balance');self.buttons.accepted.connect(self.confirm);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.account.currentIndexChanged.connect(self.load_account);self.destination.currentIndexChanged.connect(self.load_destination)
        for field in (self.local_name,self.owner_name,self.apr,self.minimum):field.textChanged.connect(self.rebuild)
        self.kind.currentIndexChanged.connect(self.rebuild);self.day.dateChanged.connect(self.rebuild)
        self.manager.changed.connect(self.result);self.load_account()

    def valid_context(self):
        return self.manager.client is self.client and self.client.signed_in and (self.client.url,self.client.identity['id'],self.client.token)==self.context and not getattr(self.owner,'is_demo',False)

    def load_account(self,*_):
        try:
            selected=snapshot_value(self.snapshot,self.account.currentData());account=selected['account'];binding=None
            with self.owner.planning_store.connection() as db:binding=self.model.binding(db,self.model.key(self.client.url,self.client.identity['id'],selected))
            self.destination.blockSignals(True);self.destination.setCurrentIndex(max(0,self.destination.findData(binding['local_id'] if binding else None)));self.destination.blockSignals(False)
            self.destination.setEnabled(binding is None)
            updated=account['balance_updated_at'];day=moment(updated or selected['retrieved_at']).astimezone().date().isoformat();self.day.setDate(QDate.fromString(day,'yyyy-MM-dd'));self.day.setEnabled(not updated)
            self.local_name.setText(account['name']);self.owner_name.setText(self.client.identity['username']);self.kind.setCurrentIndex(self.kind.findData(account['suggested_kind']))
            self.date_notice.setText('Provider reported balance time: '+updated+'. Keep its local calendar date; retrieval time is separate.' if updated else 'Provider did not report a balance timestamp. Review the recorded date explicitly; it cannot follow retrieval. Cached data can be older than this date assumption.')
            self.load_destination()
        except (ValueError,KeyError,TypeError) as exc:self.preview=None;self.changes.setRowCount(0);self.incoming.clear();self.before.clear();self.status.setText(str(exc))

    def load_destination(self,*_):
        row=next((row for row in self.owner.planning_store.accounts() if row['id']==self.destination.currentData()),None)
        if row:
            self.local_name.setText(row['name']);self.owner_name.setText(row['owner']);self.kind.setCurrentIndex(self.kind.findData(row['kind']))
        self.rebuild()

    def rebuild(self,*_):
        self.preview=None;self.reviewed.setChecked(False);self.replace.setChecked(False);self.duplicates.setChecked(False);self.sandbox.setChecked(False)
        try:
            selected=snapshot_value(self.snapshot,self.account.currentData());needs_terms=selected['account']['side']=='liability' and self.destination.currentData() is None
            self.terms_form.setRowVisible(self.apr,needs_terms);self.terms_form.setRowVisible(self.minimum,needs_terms)
            self.preview=self.model.preview(self.client.url,self.context[1],self.snapshot,self.account.currentData(),self.day.date().toString('yyyy-MM-dd'),self.local_name.text(),self.owner_name.text(),self.kind.currentData(),self.destination.currentData(),apr=self.apr.text(),minimum_payment=self.minimum.text())
            value=self.preview.value;selected=value['selected'];account=selected['account'];local=value['local'];previous=local['record'] if local else {};same_day=next((row['amount_cents'] for row in local['history'] if row['date']==value['change']['as_of']),None) if local else None
            basis='Provider-reported date' if value['change']['date_basis']=='provider_timestamp' else 'User-reviewed; provider date unknown'
            mode='Provider-recorded (cached)' if selected['balance_mode']=='provider_recorded' else 'Current retrieval requested'
            fields=[('Balance on '+value['change']['as_of'],'Not recorded' if same_day is None else str(Decimal(same_day)/100),str(Decimal(account['amount_cents'])/100)),('Currency',previous.get('currency','New'),account['currency']),('Type',ACCOUNT_TYPES[previous['kind']][0] if previous else 'New',ACCOUNT_TYPES[value['change']['kind']][0]),('Balance date basis','',basis),('Provider balance time','',account['balance_updated_at'] or 'Not reported'),('Provider retrieval','',selected['retrieved_at']),('Balance request mode','',mode),('Name',previous.get('name','New'),value['change']['name']),('Owner',previous.get('owner','New'),value['change']['owner'])]
            if value['change']['debt_terms']:
                terms=value['change']['debt_terms'];fields.extend([('User-reviewed APR (%)','New',str(Decimal(terms['apr_bps'])/100)),('User-reviewed minimum payment','New',str(Decimal(terms['minimum_payment_cents'])/100))])
            self.changes.setRowCount(len(fields))
            for index,row in enumerate(fields):
                for col,item in enumerate(row):self.changes.setItem(index,col,QTableWidgetItem(str(item)))
            self.incoming.setPlainText(json.dumps(selected,indent=2,ensure_ascii=False));self.before.setPlainText(json.dumps(dict(local=local,mapping=value['binding'],possibleDuplicates=value['possibleDuplicates']),indent=2,ensure_ascii=False))
            self.status.setText('Already imported; unchanged local record will be preserved.' if value['unchanged'] else 'Review all shown fields and acknowledge updating/duplicates/sandbox where applicable. Current values are rounded to integer cents; available balances and limits are excluded.')
        except (ValueError,KeyError,TypeError,IndexError) as exc:self.changes.setRowCount(0);self.incoming.clear();self.before.clear();self.status.setText(str(exc))

    def confirm(self):
        try:
            if self.manager.busy:raise ValueError('Wait for the current service request.')
            if not self.valid_context():raise ValueError('Reviewed service identity/session changed; no local balance was saved.')
            if not self.preview:raise ValueError('Build a valid selected-account review first.')
            value=self.preview.value
            if not self.reviewed.isChecked():raise ValueError('Review the exact balance, source, type and recorded date assumption.')
            if value['target'] and not value['unchanged'] and not self.replace.isChecked():raise ValueError('Confirm updating this existing local account.')
            if value['possibleDuplicates'] and not self.duplicates.isChecked():raise ValueError('Review possible same-name accounts and double counting.')
            if value['selected']['environment']=='sandbox' and not self.sandbox.isChecked():raise ValueError('Acknowledge that SANDBOX values are synthetic.')
            self.pending=(self.preview,self.replace.isChecked(),self.duplicates.isChecked(),self.sandbox.isChecked());connection=self.connection
            self.manager.submit('institution_balance_verified',lambda:self.client.call('GET',f'/service/v1/connections/{connection}/balances'));self.status.setText('Rechecking selected source and access before saving local data…')
        except ValueError as exc:self.status.setText(str(exc))

    def result(self,tag,value,error):
        if tag!='institution_balance_verified':return
        pending=self.pending;self.pending=None
        try:
            if error:raise ValueError(error)
            if not pending or not self.valid_context():raise ValueError('Reviewed service identity/session changed; no local balance was saved.')
            preview,replace,duplicates,sandbox=pending
            self.saved_result=self.model.commit(preview,value,reviewed=True,replace_local=replace,duplicates_reviewed=duplicates,sandbox_reviewed=sandbox)
            self.owner.refresh_planning()
            self.snapshot=value;ident=self.saved_result['localId']
            if self.destination.findData(ident)<0:
                self.destination.blockSignals(True);self.destination.addItem(preview.value['change']['name']+' · '+preview.value['selected']['account']['currency'],ident);self.destination.setCurrentIndex(self.destination.findData(ident));self.destination.blockSignals(False)
            self.destination.setEnabled(False);self.rebuild();self.status.setText('Already imported; local records preserved.' if self.saved_result['status']=='unchanged' else 'Reviewed institution balance saved with dated source. Other accounts/dates remain; no trades or transactions were imported.')
        except (ValueError,OSError) as exc:self.status.setText(str(exc))

    def detach(self):
        try:
            if self.manager.busy:raise ValueError('Wait for the current service request.')
            if not self.valid_context():raise ValueError('Sign in to the reviewed service again before detaching its mapping.')
            account_id=self.account.currentData()
            if QMessageBox.question(self,'Detach institution source mapping?','Keep local balances and historical provenance, but stop mapping this provider account to that local record? No bank consent is revoked and no server record is changed.')==QMessageBox.StandardButton.Yes:
                self.model.detach(self.client.url,self.context[1],self.snapshot['environment'],self.connection,account_id);self.status.setText('Local mapping detached. Balances/provenance remain; close and review a new destination if needed.')
        except ValueError as exc:self.status.setText(str(exc))

    def cleanup(self):
        self.pending=None;self.preview=None;self.snapshot=None;self.context=();self.incoming.clear();self.before.clear();self.changes.setRowCount(0)
        for field in (self.account,self.destination):field.blockSignals(True);field.clear();field.blockSignals(False)
        for field in (self.local_name,self.owner_name,self.apr,self.minimum):field.blockSignals(True);field.setText('');field.blockSignals(False)
        if self._subscribed:self.manager.changed.disconnect(self.result);self._subscribed=False

    def done(self,result):
        if self.manager.busy:self.status.setText('Wait for the source check before closing.');return
        self.cleanup();super().done(result)

    def closeEvent(self,event):
        if self.manager.busy:self.status.setText('Wait for the source check before closing.');event.ignore();return
        self.cleanup();super().closeEvent(event)
