"""Explicit controls for the optional local read-only service."""
from datetime import datetime,timezone
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QSpinBox,
    QPushButton,QCheckBox,QDialogButtonBox,QListWidget,QListWidgetItem,QMessageBox,
    QApplication,QTableWidgetItem,QTabWidget)
from .ui import FitDialog,text
from .import_ui import grid
from .reports import DATASETS


class APITokenDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.secret=''
        self.setWindowTitle('Issue a scoped local API token');self.resize(800,700)
        layout=QVBoxLayout(self)
        layout.addWidget(text('Grant only the datasets an integration needs. The secret is shown in this dialog and is not saved. Tokens expire and are revoked when the API stops or the desktop exits. Anyone holding a token can read its allowed personal records.','muted',True))
        form=QFormLayout();self.name=QLineEdit();self.name.setMaxLength(120);self.hours=QSpinBox();self.hours.setRange(1,168);self.hours.setValue(24);self.hours.setSuffix(' hours');form.addRow('Integration / token name',self.name);form.addRow('Lifetime',self.hours);layout.addLayout(form)
        self.scopes=QListWidget()
        for key,label in DATASETS.items():
            item=QListWidgetItem(label);item.setData(Qt.ItemDataRole.UserRole,key);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Unchecked);self.scopes.addItem(item)
        layout.addWidget(self.scopes,1)
        self.issue_button=QPushButton('Issue reviewed token');self.issue_button.clicked.connect(self.issue);layout.addWidget(self.issue_button)
        self.token=QLineEdit();self.token.setReadOnly(True);self.token.setEchoMode(QLineEdit.EchoMode.Password);layout.addWidget(self.token)
        reveal=QCheckBox('Show token on this screen');reveal.toggled.connect(lambda value:self.token.setEchoMode(QLineEdit.EchoMode.Normal if value else QLineEdit.EchoMode.Password));layout.addWidget(reveal)
        copy=QPushButton('Copy token to clipboard');copy.clicked.connect(self.copy_token);layout.addWidget(copy)
        self.status=text('','muted',True);layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def issue(self):
        try:
            if getattr(self.owner,'is_demo',False):raise ValueError('API tokens are disabled in the disposable demo.')
            if not self.owner.local_api.running:raise ValueError('Start the reviewed local service first.')
            if self.secret:raise ValueError('This dialog has already issued a token; close it to issue another.')
            scopes=[self.scopes.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.scopes.count()) if self.scopes.item(i).checkState()==Qt.CheckState.Checked]
            _,self.secret=self.owner.local_api.issue(self.name.text(),scopes,self.hours.value())
            self.token.setText(self.secret);self.issue_button.setEnabled(False);self.status.setText('Save this secret in your integration now. Closing clears this display; use Revoke if it is lost.')
        except ValueError as exc:self.status.setText(str(exc))

    def copy_token(self):
        if not self.secret:self.status.setText('Issue a reviewed token first.');return
        QApplication.clipboard().setText(self.secret);self.status.setText('Copied explicitly to your clipboard. Protect clipboard history; the app does not clear other clipboard contents.')

    def done(self,result):
        self.secret='';self.token.setText('');super().done(result)

    def closeEvent(self,event):
        self.secret='';self.token.setText('');super().closeEvent(event)


class LocalAPIDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.setWindowTitle('Optional read-only local API');self.resize(1000,700)
        layout=QVBoxLayout(self)
        layout.addWidget(text('Disabled by default. This session-only API listens on 127.0.0.1 and reads saved records; unsaved edits, live quotes and forecasts are outside its datasets. Explicit per-dataset Bearer tokens, expiry/revocation, read-only methods, rate limits and immutable pagination. Browser-origin requests are rejected. No remote listening or uploads.','muted',True))
        self.address=QLineEdit();self.address.setReadOnly(True);layout.addWidget(self.address)
        row=QHBoxLayout()
        for label,handler in [('Start reviewed API…',self.start),('Stop / revoke all',self.stop),('Issue scoped token…',self.issue),('Revoke selected',self.revoke),('Refresh',self.reload)]:
            button=QPushButton(label);button.setToolTip(label);button.clicked.connect(handler);row.addWidget(button)
        layout.addLayout(row)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self.tokens=grid(['Integration','Dataset scopes','Expires (UTC)']);self.tabs.addTab(self.tokens,'Scoped tokens')
        self.activity=grid(['UTC time','Token ID','Dataset','HTTP result']);self.tabs.addTab(self.activity,'Session request activity')
        layout.addWidget(text('GET /api/v1/datasets · GET /api/v1/records/{dataset}?start=YYYY-MM-DD&end=YYYY-MM-DD&limit=1000&offset=0. Follow next_offset with the returned snapshot ID and unchanged dates. Snapshots expire after five minutes; exact Decimal amounts are JSON strings. At most 60 requests per token per minute. See docs/product-audit/16-read-only-api.md for schema and examples.','muted',True))
        self.status=text('','muted',True);layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);layout.addWidget(buttons);self.reload()

    def reload(self):
        service=self.owner.local_api;self.address.setText(service.url or 'Stopped — no listening socket')
        rows=service.token_info();self.tokens.setRowCount(len(rows))
        for row,record in enumerate(rows):
            expires=datetime.fromtimestamp(record['expires'],timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            for col,value in enumerate([record['name'],', '.join(record['scopes']),expires]):
                item=QTableWidgetItem(value);item.setToolTip(value);self.tokens.setItem(row,col,item)
            self.tokens.item(row,0).setData(Qt.ItemDataRole.UserRole,record['id'])
        self.status.setText(f'{len(rows)} scoped session tokens · '+('API is running.' if service.running else 'API is disabled.'))
        with service.lock:activity=list(reversed(service.audit))
        self.activity.setRowCount(len(activity))
        for row,record in enumerate(activity):
            for col,value in enumerate([record['at'],record['tokenId'] or 'Unauthenticated',record['dataset'] or 'Metadata / rejected',str(record['status'])]):
                item=QTableWidgetItem(value);item.setToolTip(value);self.activity.setItem(row,col,item)

    def start(self):
        try:
            if getattr(self.owner,'is_demo',False):raise ValueError('API access is disabled in the disposable demo.')
            if self.owner.local_api.running:self.reload();return
            if QMessageBox.question(self,'Enable local read access?','Open a loopback-only read-only service for this desktop session? You must separately issue a scoped token before records can be read. No token or API configuration is persisted.')!=QMessageBox.StandardButton.Yes:return
            self.owner.local_api.start();self.reload()
        except (ValueError,OSError) as exc:self.status.setText(str(exc))

    def stop(self):self.owner.local_api.stop();self.reload()

    def issue(self):
        if not self.owner.local_api.running:self.status.setText('Start the reviewed API first.');return
        APITokenDialog(self.owner).exec();self.reload()

    def revoke(self):
        row=self.tokens.currentRow()
        if row<0:self.status.setText('Select a token to revoke.');return
        self.owner.local_api.revoke(self.tokens.item(row,0).data(Qt.ItemDataRole.UserRole));self.reload()
