"""Opt-in desktop review of authenticated household and institution services."""
from copy import deepcopy
import json
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
from PySide6.QtCore import QObject,QThread,Signal,Qt,QUrl,QDate
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,
    QPushButton,QCheckBox,QDialogButtonBox,QTabWidget,QTableWidgetItem,QTextEdit,
    QMessageBox,QInputDialog)
from .ui import FitDialog,text as ui_text
from .planning_ui import choices,dated
from .planning import money_value
from .import_ui import grid
from .connected_client import ConnectedClient,ConnectionError
from .sharing import manual_snapshot,goal_snapshot,investment_snapshot,validate_snapshot


def text(*args,**kwargs):
    label=ui_text(*args,**kwargs);label.setTextFormat(Qt.TextFormat.PlainText);return label


def reset_private(*fields):
    # QLineEdit.clear() can retain undo history when a key was revealed.
    # setText resets that history as well as the visible value.
    for field in fields:field.setText('')


class ServiceWorker(QThread):
    outcome=Signal(str,object,str)
    def __init__(self,owner,tag,operation):super().__init__(owner);self.tag=tag;self.operation=operation
    def run(self):
        try:
            if self.isInterruptionRequested():return
            result=self.operation();self.outcome.emit(self.tag,result,'')
        except (ValueError,OSError,KeyError,TypeError) as exc:self.outcome.emit(self.tag,None,str(exc))


class ConnectedManager(QObject):
    changed=Signal(str,object,str)
    idle=Signal()
    progress=Signal(str)
    def __init__(self,owner):super().__init__(owner);self.owner=owner;self.client=None;self.worker=None;self.stopping=False
    @property
    def busy(self):return self.worker is not None
    def submit(self,tag,operation):
        if self.busy or self.stopping:raise ValueError('Wait for the current service request to finish.')
        if getattr(self.owner,'is_demo',False):raise ValueError('Connected services are disabled in the disposable demo.')
        self.worker=ServiceWorker(self.owner,tag,operation);self.worker.outcome.connect(self.result);self.worker.finished.connect(self.finished);self.worker.start()
    def result(self,tag,value,error):
        if not self.stopping:self.changed.emit(tag,value,error)
    def finished(self):
        worker=self.worker;self.worker=None;worker.operation=None;worker.deleteLater()
        self.idle.emit()
        if self.stopping:
            if self.client:self.client.forget()
            self.owner.close()
    def shutdown(self):
        self.stopping=True
        if self.busy:self.worker.requestInterruption();return False
        if self.client:self.client.forget()
        return True


class ShareReviewDialog(FitDialog):
    def __init__(self,owner,kind,payload,destination):
        super().__init__(owner);self.setWindowTitle('Review selected records before upload');self.resize(920,700)
        layout=QVBoxLayout(self)
        layout.addWidget(text(f'Upload {payload["name"]} to {destination}. This sends only the displayed selected account/goal snapshot. Household members with access can read it; editors/owners can edit shared snapshots. It does not upload your other accounts, dashboards, journal, tax profile or credentials.','muted',True))
        if kind=='investment_account':
            workspace=payload['workspace'];records=workspace['performanceRecords']
            overview=f"{sum(len(rows) for rows in workspace['trades'].values())} trades · {len(workspace['dividendEvents'])} dividends · {len(records['valuations'])} recorded values · {len(records['flows'])} funding flows. This includes dated transaction amounts and source notes."
        else:
            overview=f"Recorded {payload['currency']} {Decimal(payload['amountCents'])/100:,.2f} · dated {payload['asOf']} · source: {payload['source']}."
            if kind=='goal':overview+=f" Goal target: {payload['currency']} {Decimal(payload['targetCents'])/100:,.2f} by {payload['targetDate']}."
        layout.addWidget(text(overview,'muted',True))
        detail=QTextEdit();detail.setReadOnly(True);detail.setPlainText(json.dumps(payload,indent=2,ensure_ascii=False));layout.addWidget(detail,1)
        layout.addWidget(text('Review source notes and personal text included in these records. Investment snapshots contain trades, dividends and recorded account values/flows; they do not include quotes or certified tax lots. This snapshot is separate from local records; there is no automatic two-way overwrite.','muted',True))
        self.reviewed=QCheckBox('I reviewed these exact records and their sharing destination');layout.addWidget(self.reviewed)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.button(QDialogButtonBox.StandardButton.Save).setText('Upload reviewed snapshot');buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False);self.reviewed.toggled.connect(buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)


class SharedValueEditor(FitDialog):
    def __init__(self,owner,record):
        super().__init__(owner);self.record=deepcopy(record);self.payload=None
        self.setWindowTitle('Review shared balance / goal update');self.resize(750,700)
        value=record['payload'];layout=QVBoxLayout(self)
        layout.addWidget(text(f"Edit {value['name']} · service version {record['version']}. This changes the selected service snapshot only. Local records are unchanged. Another device's update will stop the save until you reload and review it.",'muted',True))
        form=QFormLayout();self.name=QLineEdit(value['name']);self.name.setMaxLength(120)
        self.amount=QLineEdit(str(Decimal(value['amountCents'])/100));self.as_of=dated(value['asOf']);self.as_of.setMaximumDate(QDate.currentDate())
        self.source=QLineEdit(value['source']);self.source.setMaxLength(120)
        form.addRow('Name',self.name);form.addRow('Currency / recorded type',text(value['currency']+' · '+value.get('accountType',value.get('goalType','')),'muted',True));form.addRow('Balance / amount set aside',self.amount);form.addRow('Recorded as of',self.as_of);form.addRow('Source',self.source)
        if record['kind']=='manual_account':
            self.owner_name=QLineEdit(value['owner']);self.owner_name.setMaxLength(120);form.addRow('Recorded owner label',self.owner_name)
        else:
            self.target=QLineEdit(str(Decimal(value['targetCents'])/100));self.target_date=dated(value['targetDate']);form.addRow('Goal target',self.target);form.addRow('Target date',self.target_date)
        layout.addLayout(form)
        layout.addWidget(text('Currency/type and debt assumptions retain the reviewed snapshot values. Owner labels describe the account; they do not change household permissions. Recorded values are user-reviewed statements/estimates, not connected bank verification.','muted',True))
        self.reviewed=QCheckBox('I reviewed the amounts, dates and source before updating this shared snapshot');layout.addWidget(self.reviewed)
        self.error=text('','muted',True);layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.confirm);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    @staticmethod
    def amount_cents(value):
        entered=Decimal(value)
        if not entered.is_finite() or entered!=entered.quantize(Decimal('.01')):raise ValueError('Use a finite amount with at most two decimal places.')
        return money_value(entered)
    def confirm(self):
        try:
            if not self.reviewed.isChecked():raise ValueError('Review the shared values and source before saving.')
            value=deepcopy(self.record['payload']);value.update(name=self.name.text().strip(),amountCents=self.amount_cents(self.amount.text()),asOf=self.as_of.date().toString('yyyy-MM-dd'),source=self.source.text().strip())
            if self.record['kind']=='manual_account':value['owner']=self.owner_name.text().strip()
            else:value.update(targetCents=self.amount_cents(self.target.text()),targetDate=self.target_date.date().toString('yyyy-MM-dd'))
            validate_snapshot(self.record['kind'],value);self.payload=value;self.accept()
        except InvalidOperation:self.payload=None;self.error.setText('Enter valid decimal amounts with at most two decimal places.')
        except ValueError as exc:self.payload=None;self.error.setText(str(exc))


class EnrollmentDialog(FitDialog):
    """Invitation-bound enrollment; setup material never enters the profile."""
    def __init__(self,owner,manager,client,name=''):
        super().__init__(owner);self.manager=manager;self.client=client;self.challenge='';self.expected_name='';self.completed=False;self._subscribed=True;self._forgetting=False;self._cancel_close=False
        self.setWindowTitle('Join with a private invitation');self.resize(790,700)
        layout=QVBoxLayout(self)
        layout.addWidget(text('Create a service identity at '+client.url+'. This requires an invitation addressed to your username and an authenticator app. No local financial records are uploaded. Review the service address before sending your passphrase.','muted',True))
        self.steps=QTabWidget();layout.addWidget(self.steps,1)
        first=QWidget();first_layout=QVBoxLayout(first);form=QFormLayout()
        self.name=QLineEdit(name);self.name.setMaxLength(80)
        self.invitation=QLineEdit();self.invitation.setMaxLength(100);self.invitation.setEchoMode(QLineEdit.EchoMode.Password)
        self.password=QLineEdit();self.password.setMaxLength(256);self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_password=QLineEdit();self.confirm_password.setMaxLength(256);self.confirm_password.setEchoMode(QLineEdit.EchoMode.Password)
        for title,field in [('Invited username',self.name),('Private one-use invitation',self.invitation),('New passphrase (14–256 characters)',self.password),('Confirm passphrase',self.confirm_password)]:form.addRow(title,field)
        first_layout.addLayout(form);self.reviewed=QCheckBox('I reviewed this service address and intend to create my invited identity');first_layout.addWidget(self.reviewed)
        self.begin_button=QPushButton('Start authenticator enrollment');self.begin_button.clicked.connect(self.begin);first_layout.addWidget(self.begin_button);first_layout.addStretch();self.steps.addTab(first,'Invitation')
        second=QWidget();second_layout=QVBoxLayout(second)
        second_layout.addWidget(text('Add this setup key manually in your authenticator app: time-based (TOTP), SHA1, six digits, 30 seconds. Keep the key private. The service identity is created only after you confirm a current code; this setup expires after ten minutes.','muted',True))
        self.seed=QLineEdit();self.seed.setReadOnly(True);self.seed.setEchoMode(QLineEdit.EchoMode.Password);second_layout.addWidget(self.seed)
        self.reveal_seed=QCheckBox('Reveal private authenticator setup key');self.reveal_seed.toggled.connect(lambda visible:self.seed.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password));second_layout.addWidget(self.reveal_seed)
        self.code=QLineEdit();self.code.setMaxLength(6);self.code.setEchoMode(QLineEdit.EchoMode.Password);self.code.setPlaceholderText('Current six-digit authenticator code');second_layout.addWidget(self.code)
        self.confirm_button=QPushButton('Confirm authenticator and join household');self.confirm_button.clicked.connect(self.confirm);second_layout.addWidget(self.confirm_button);second_layout.addStretch();self.steps.addTab(second,'Authenticator');self.steps.setTabEnabled(1,False)
        third=QWidget();third_layout=QVBoxLayout(third)
        third_layout.addWidget(text('Your identity is created and signed in. These ten recovery codes are shown once and each can be used for one sign-in if your authenticator is unavailable. Store them privately outside this app. They are never saved in your desktop profile.','muted',True))
        self.recovery=QTextEdit();self.recovery.setReadOnly(True);self.recovery.hide();third_layout.addWidget(self.recovery,1)
        self.reveal_recovery=QCheckBox('Reveal my private one-use recovery codes');self.reveal_recovery.toggled.connect(self.recovery.setVisible);third_layout.addWidget(self.reveal_recovery)
        self.saved_recovery=QCheckBox('I stored my recovery codes privately');third_layout.addWidget(self.saved_recovery);third_layout.addStretch();self.steps.addTab(third,'Recovery codes');self.steps.setTabEnabled(2,False)
        self.status=text('No enrollment request sent.','muted',True);layout.addWidget(self.status)
        self.forget_button=QPushButton('Forget local setup / close without recovery codes…');self.forget_button.clicked.connect(self.forget_local);layout.addWidget(self.forget_button)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.manager.changed.connect(self.result)
        self.manager.idle.connect(self.on_idle)

    def submit(self,tag,operation):
        try:self.manager.submit(tag,operation);self.status.setText('Contacting the reviewed service…')
        except ValueError as exc:self.status.setText(str(exc))
    def begin(self):
        try:
            if self.manager.busy:raise ValueError('Wait for the current service request.')
            if self.manager.client and self.manager.client.signed_in:raise ValueError('Sign out before enrolling another identity.')
            if self.challenge or self.completed:raise ValueError('Complete or cancel this setup before starting another.')
            if not self.reviewed.isChecked():raise ValueError('Review the service address before starting enrollment.')
            name=self.name.text().strip();invitation=self.invitation.text().strip();password=self.password.text()
            if not 3<=len(name)<=80 or not 20<=len(invitation)<=100:raise ValueError('Enter the invited username and private invitation.')
            if not 14<=len(password)<=256 or password!=self.confirm_password.text():raise ValueError('Use matching passphrases of 14–256 characters.')
            reset_private(self.password,self.confirm_password,self.invitation);self.manager.client=self.client
            self.submit('enrollment_begin',lambda:self.client.begin_enrollment(invitation,name,password))
        except ValueError as exc:self.status.setText(str(exc))
    def confirm(self):
        try:
            if self.manager.busy:raise ValueError('Wait for the current service request.')
            if not self.challenge or self.completed:raise ValueError('Start an invited authenticator setup first.')
            code=self.code.text().strip()
            if len(code)!=6 or not code.isascii() or not code.isdigit():raise ValueError('Enter the current six-digit authenticator code.')
            challenge=self.challenge;name=self.expected_name;reset_private(self.code);self.submit('enrollment_confirm',lambda:self.client.finish_enrollment(challenge,code,expected_name=name))
        except ValueError as exc:self.status.setText(str(exc))
    def result(self,tag,value,error):
        if not tag.startswith('enrollment_'):return
        if error:self.status.setText(error);return
        if tag=='enrollment_begin':
            self.challenge=value['challenge'];self.expected_name=value['username'];self.seed.setText(value['mfaSecret']);self.steps.setTabEnabled(0,False);self.steps.setTabEnabled(1,True);self.steps.setCurrentIndex(1);self.status.setText('Setup ready for '+value['username']+'. Confirm a current code within ten minutes.');return
        if tag=='enrollment_confirm':
            self.completed=True;self.challenge='';reset_private(self.seed);self.reveal_seed.setChecked(False);self.steps.setTabEnabled(1,False);self.steps.setTabEnabled(2,True);self.steps.setCurrentIndex(2);self.recovery.setPlainText('\n'.join(value['recoveryCodes']));self.status.setText('Joined and signed in as '+value['user']['username']+'. Store the recovery codes privately before closing.');return
        if tag=='enrollment_cancel':
            self.challenge='';self.status.setText('Pending enrollment canceled. The invitation remains usable.');self._forgetting=True;self._cancel_close=True
    def on_idle(self):
        if self._cancel_close:self.done(self.DialogCode.Rejected)
    def forget_local(self):
        if self.manager.busy:self.status.setText('Wait for the current service request before closing.');return
        message='Close without retaining these one-use recovery codes? Your identity remains created; keep your enrolled authenticator.' if self.completed else 'Forget the private setup key and challenge locally? This does not cancel server enrollment or consume the invitation. An unfinished server setup expires after ten minutes. If confirmation failed after a network error, an identity may already exist; use the enrolled authenticator to sign in.'
        if QMessageBox.question(self,'Forget private setup?',message)==QMessageBox.StandardButton.Yes:self._forgetting=True;self.done(self.DialogCode.Accepted if self.completed else self.DialogCode.Rejected)
    def done(self,result):
        if self.manager.busy:self.status.setText('Wait for the current service request before closing.');return
        if not self._forgetting:
            if self.completed and not self.saved_recovery.isChecked():self.status.setText('Store your recovery codes and confirm, or explicitly close without retaining them.');return
            if self.challenge:
                challenge=self.challenge;self.submit('enrollment_cancel',lambda:self.client.cancel_enrollment(challenge));return
        reset_private(self.password,self.confirm_password,self.invitation,self.seed,self.code)
        self.challenge='';self.expected_name='';self.recovery.clear();self.recovery.hide();self.reveal_recovery.setChecked(False)
        if self._subscribed:self.manager.changed.disconnect(self.result);self.manager.idle.disconnect(self.on_idle);self._subscribed=False
        super().done(self.DialogCode.Accepted if self.completed else result)
    def closeEvent(self,event):
        event.ignore();self.reject()


class ConnectedServicesDialog(FitDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner
        if not hasattr(owner,'connected_manager'):owner.connected_manager=ConnectedManager(owner)
        self.manager=owner.connected_manager;self.households=[];self.snapshots=[];self.connections=[];self.pending_link=None;self._subscribed=True;self._pending_copy=None;self._copy_review=None
        self.setWindowTitle('Optional services and household sharing');self.resize(1080,740)
        layout=QVBoxLayout(self)
        layout.addWidget(text('Your desktop remains usable without this service. Configure a service you trust, then sign in. Only an explicit reviewed publication uploads local records. Institution consent uses Plaid in your browser; access tokens remain on the service.','muted',True))
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        access=QWidget();access_layout=QVBoxLayout(access);form=QFormLayout()
        self.url=QLineEdit(self.manager.client.url if self.manager.client else '');self.url.setPlaceholderText('https://your-reviewed-service.example')
        self.local_http=QCheckBox('Allow loopback HTTP for a development service only')
        self.username=QLineEdit();self.username.setMaxLength(80)
        self.password=QLineEdit();self.password.setEchoMode(QLineEdit.EchoMode.Password);self.password.setMaxLength(256)
        self.code=QLineEdit();self.code.setEchoMode(QLineEdit.EchoMode.Password);self.code.setMaxLength(20);self.code.setPlaceholderText('Authenticator code or one-use recovery code')
        for title,field in [('Reviewed service address',self.url),('',self.local_http),('Username',self.username),('Passphrase',self.password),('Second factor',self.code)]:form.addRow(title,field)
        access_layout.addLayout(form)
        row=QHBoxLayout()
        for label,handler in [('Sign in',self.sign_in),('Sign out',self.sign_out),('Refresh service state',self.refresh)]:
            button=QPushButton(label);button.clicked.connect(handler);row.addWidget(button)
        access_layout.addLayout(row);enroll=QPushButton('Join with an invitation and enroll an authenticator…');enroll.clicked.connect(self.enroll);access_layout.addWidget(enroll)
        privacy=QPushButton('Service account and privacy controls…');privacy.clicked.connect(self.privacy_controls);access_layout.addWidget(privacy)
        access_layout.addWidget(text('Sessions remain in memory for up to 12 hours. A used authenticator code cannot be replayed; wait for its next code after signing out. New identities can join below with an invitation and confirmed authenticator enrollment; operator setup is separate. Closing the desktop forgets its token; use Sign out to revoke the server session immediately.','muted',True));access_layout.addStretch();self.tabs.addTab(access,'Service access')
        household_page=QWidget();household_layout=QVBoxLayout(household_page)
        self.household=choices([(None,'My created records (new uploads private)')]);household_layout.addWidget(self.household)
        self.members=grid(['Identity','Role']);household_layout.addWidget(self.members,1)
        row=QHBoxLayout()
        for label,handler in [('Create household…',self.create_household),('Invite identity…',self.invite),('Accept invitation…',self.accept_invite),('Change selected role…',self.change_role)]:
            button=QPushButton(label);button.setToolTip(label);button.clicked.connect(handler);row.addWidget(button)
        household_layout.addLayout(row);self.tabs.addTab(household_page,'Household access')
        sharing_page=QWidget();sharing_layout=QVBoxLayout(sharing_page)
        self.local_record=choices(self.local_choices());sharing_layout.addWidget(self.local_record)
        publish=QPushButton('Review selected local records for publication…');publish.clicked.connect(self.publish);sharing_layout.addWidget(publish)
        self.shared=grid(['Recorded snapshot','Type','Version','Updated (UTC)','Visibility']);sharing_layout.addWidget(self.shared,1)
        self.shared_details=QTextEdit();self.shared_details.setReadOnly(True)
        row=QHBoxLayout()
        for label,handler in [('Refresh selected scope',self.refresh_scope),('Edit shared balance / goal…',self.edit_shared),('Replace from selected local records…',self.replace_shared)]:
            button=QPushButton(label);button.setToolTip(label);button.clicked.connect(handler);row.addWidget(button)
        sharing_layout.addLayout(row);row=QHBoxLayout()
        for label,handler in [('Review download to desktop…',self.download_snapshot),('Detach local copy mapping…',self.detach_copy)]:
            button=QPushButton(label);button.setToolTip(label);button.clicked.connect(handler);row.addWidget(button)
        sharing_layout.addLayout(row);row=QHBoxLayout()
        for label,handler in [('View selected snapshot details',lambda:self.tabs.setCurrentWidget(self.shared_details)),('Withdraw my selected snapshot…',self.withdraw),('Delete my selected snapshot…',self.delete_snapshot)]:
            button=QPushButton(label);button.setToolTip(label);button.clicked.connect(handler);row.addWidget(button)
        sharing_layout.addLayout(row);self.tabs.addTab(sharing_page,'Shared records and privacy')
        institutions=QWidget();institution_outer=QVBoxLayout(institutions);self.institution_tabs=QTabWidget();institution_outer.addWidget(self.institution_tabs,1)
        consent=QWidget();institutions_layout=QVBoxLayout(consent)
        self.transactions=QCheckBox('Read bank transaction history');self.investments=QCheckBox('Read investment holdings');institutions_layout.addWidget(self.transactions);institutions_layout.addWidget(self.investments)
        self.pending_sessions=choices([(None,'Select an owned pending consent session after refreshing service state')]);institutions_layout.addWidget(self.pending_sessions);self.pending_sessions.currentIndexChanged.connect(lambda *_:setattr(self,'pending_link',self.pending_sessions.currentData()))
        row=QHBoxLayout()
        for label,handler in [('Start reviewed Plaid consent…',self.start_link),('Check completed browser session',self.complete_link)]:
            button=QPushButton(label);button.setToolTip(label);button.clicked.connect(handler);row.addWidget(button)
        institutions_layout.addLayout(row)
        row=QHBoxLayout()
        for label,handler in [('Cancel pending browser session…',self.cancel_link),('Reopen selected browser session',self.reopen_link)]:
            button=QPushButton(label);button.setToolTip(label);button.clicked.connect(handler);row.addWidget(button)
        institutions_layout.addLayout(row)
        institutions_layout.addWidget(text('pending: finish browser consent; expired_pending/canceled: retained for external consent review; exchanged: retry storage recovery; exchanging: wait; uncertain: ask the operator to review/revoke consent. Cancel alone does not prove bank revocation.','muted',True))
        institutions_layout.addStretch()
        connections_page=QWidget();connections_layout=QVBoxLayout(connections_page)
        self.institutions=grid(['Connection ID','Environment','State','Last success','Last attempt']);connections_layout.addWidget(self.institutions,1)
        row=QHBoxLayout()
        for label,handler in [('Refresh selected institution',self.refresh_connection),('Review saved provider data',self.review_connection),('Disconnect / delete…',self.disconnect_connection)]:
            button=QPushButton(label);button.setToolTip(label);button.clicked.connect(handler);row.addWidget(button)
        connections_layout.addLayout(row)
        balances=QPushButton('Review selected institution balance for local import…');balances.clicked.connect(self.import_balance);connections_layout.addWidget(balances)
        self.institution_tabs.addTab(connections_page,'Saved connections');self.institution_tabs.addTab(consent,'Consent sessions')
        self.provider_details=QTextEdit();self.provider_details.setReadOnly(True)
        institution_outer.addWidget(text('Sandbox is synthetic provider data. Saved data is not a live balance; check source dates and currencies. Holdings cannot reconstruct historical tax lots. Review provider records before using a balance in your local Accounts page.','muted',True));self.tabs.addTab(institutions,'Institutions');self.tabs.addTab(self.provider_details,'Provider records');self.tabs.addTab(self.shared_details,'Snapshot details')
        self.tabs.setTabText(0,'Access');self.tabs.setTabText(1,'Household');self.tabs.setTabText(2,'Shared records')
        self.status=text('Signed in to the reviewed service.' if self.client and self.client.signed_in else 'No optional service session. Local records remain available.','muted',True);layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.manager.changed.connect(self.result);self.manager.idle.connect(self.resume_copy);self.household.currentIndexChanged.connect(self.refresh_scope);self.shared.itemSelectionChanged.connect(self.show_shared)

    @property
    def client(self):return self.manager.client
    def local_choices(self):
        return [(f'manual:{r["id"]}','Account: '+r['name']) for r in self.owner.planning_store.accounts()]+[(f'goal:{r["id"]}','Goal: '+r['name']) for r in self.owner.planning_store.goals()]+[(f'investment:{r["id"]}','Investments: '+r['name']) for r in self.owner.investment_store.state['investmentAccounts']]
    def selected_local(self):
        if not self.local_record.currentData():raise ValueError('Record an account or goal before selecting it.')
        kind,ident=self.local_record.currentData().split(':',1)
        if kind=='investment':return 'investment_account',investment_snapshot(self.owner.investment_store.state,ident)
        rows=self.owner.planning_store.goals() if kind=='goal' else self.owner.planning_store.accounts()
        row=next((r for r in rows if str(r['id'])==ident),None)
        if not row:raise ValueError('The local record changed; reopen and select again.')
        return ('goal',goal_snapshot(row)) if kind=='goal' else ('manual_account',manual_snapshot(row))
    def request(self,tag,operation):
        try:self.manager.submit(tag,operation);self.status.setText('Contacting the explicitly selected service…')
        except ValueError as exc:self.status.setText(str(exc))
    def require_session(self):
        if not self.client or not self.client.signed_in:raise ValueError('Sign in to the reviewed optional service first.')
        if self.manager.busy:raise ValueError('Wait for the current service request.')
        return self.client
    def sign_in(self):
        try:
            if self.manager.busy:raise ValueError('Wait for the current service request.')
            if self.client and self.client.signed_in:raise ValueError('Sign out before changing identity or service address.')
            self.clear_session_data()
            client=ConnectedClient(self.url.text(),self.local_http.isChecked());name=self.username.text();password=self.password.text();code=self.code.text();reset_private(self.password,self.code);self.manager.client=client
            self.request('login',lambda:client.login(name,password,code))
        except ValueError as exc:self.status.setText(str(exc))
    def sign_out(self):
        try:client=self.require_session();self.request('logout',client.logout)
        except ValueError as exc:self.status.setText(str(exc))
    def privacy_controls(self):
        try:
            self.require_session()
            from .connected_privacy_ui import ServicePrivacyDialog
            dialog=ServicePrivacyDialog(self);dialog.exec()
            if not self.client.signed_in:self.clear_session_data();self.status.setText('No signed-in service identity. Local finances remain available.')
        except ValueError as exc:self.status.setText(str(exc))
    def enroll(self):
        try:
            if self.manager.busy:raise ValueError('Wait for the current service request.')
            if self.client and self.client.signed_in:raise ValueError('Sign out before enrolling another identity.')
            if getattr(self.owner,'is_demo',False):raise ValueError('Connected services are disabled in the disposable demo.')
            self.clear_session_data();reset_private(self.password,self.code)
            client=ConnectedClient(self.url.text(),self.local_http.isChecked());dialog=EnrollmentDialog(self,self.manager,client,self.username.text())
            dialog.exec()
            self.status.setText('Enrollment finished. Refresh service state to see your household.' if client.signed_in else 'No signed-in service identity. You can sign in or restart an invited enrollment.')
        except ValueError as exc:self.status.setText(str(exc))
    def refresh(self):
        try:
            client=self.require_session()
            self.request('refresh',lambda:dict(households=client.call('GET','/service/v1/households')['households'],connections=client.call('GET','/service/v1/connections')['connections'],pending=client.call('GET','/service/v1/connections/link')['pending'],capabilities=client.call('GET','/service/v1/capabilities')))
        except ValueError as exc:self.status.setText(str(exc))
    def refresh_scope(self,*_):
        self.shared.setRowCount(0);self.shared_details.clear();self.members.setRowCount(0)
        try:
            client=self.require_session();household=self.household.currentData()
            self.request('scope',lambda:dict(scope=household,snapshots=client.call('GET','/service/v1/snapshots',query={'household':household} if household else None)['snapshots'],members=client.call('GET',f'/service/v1/households/{household}/members')['members'] if household else []))
        except ValueError as exc:self.status.setText(str(exc))
    def create_household(self):
        try:
            client=self.require_session();name,ok=QInputDialog.getText(self,'Create household','Household name')
            if ok and name:self.request('mutation',lambda:client.call('POST','/service/v1/households',dict(name=name)))
        except ValueError as exc:self.status.setText(str(exc))
    def invite(self):
        try:
            client=self.require_session();household=self.household.currentData()
            if not household:raise ValueError('Select a household you own first.')
            name,ok=QInputDialog.getText(self,'Invite identity','Intended username (existing or newly invited)')
            if not ok:return
            role,ok=QInputDialog.getItem(self,'Invite identity','Role',['read_only','editor','owner'],0,False)
            if ok:self.request('invite',lambda:client.call('POST',f'/service/v1/households/{household}/invitations',dict(username=name,role=role)))
        except ValueError as exc:self.status.setText(str(exc))
    def accept_invite(self):
        try:
            client=self.require_session();value,ok=QInputDialog.getText(self,'Accept invitation','Private one-use invitation',QLineEdit.EchoMode.Password)
            if ok and value:self.request('mutation',lambda:client.call('POST','/service/v1/invitations/accept',dict(invitation=value)))
        except ValueError as exc:self.status.setText(str(exc))
    def change_role(self):
        try:
            client=self.require_session();household=self.household.currentData();row=self.members.currentRow()
            if not household or row<0:raise ValueError('Select a household and member first.')
            member=self.members.item(row,0).data(Qt.ItemDataRole.UserRole)
            role,ok=QInputDialog.getItem(self,'Review member access','New role / remove',['read_only','editor','owner','Remove member'],0,False)
            if ok and QMessageBox.question(self,'Change access?',f'Apply {role} to this identity? Removed members lose household access and their publications become private.')==QMessageBox.StandardButton.Yes:self.request('mutation',lambda:client.call('PATCH',f'/service/v1/households/{household}/members/{member}',dict(role=None if role=='Remove member' else role)))
        except ValueError as exc:self.status.setText(str(exc))
    def publish(self):
        try:
            client=self.require_session();kind,payload=self.selected_local();household=self.household.currentData()
            review=ShareReviewDialog(self.owner,kind,payload,self.household.currentText()+' at '+client.url+' as '+client.identity['username'])
            if review.exec()==review.DialogCode.Accepted and review.reviewed.isChecked():self.request('published',lambda:client.call('POST','/service/v1/snapshots',dict(kind=kind,payload=payload,householdId=household)))
        except ValueError as exc:self.status.setText(str(exc))
    def selected_snapshot(self):
        row=self.shared.currentRow()
        if row<0:raise ValueError('Select a service snapshot first.')
        return deepcopy(self.snapshots[row])
    def download_snapshot(self):
        try:
            client=self.require_session();record=self.selected_snapshot();scope=self.household.currentData()
            if self._copy_review or self._pending_copy:raise ValueError('Wait for the reviewed copy to finish.')
            self.request('copy_fetched',lambda:dict(record=client.call('GET','/service/v1/snapshots/'+record['id']),origin=client.url,identity=client.identity['id'],scope=scope,selected=record['id']))
        except ValueError as exc:self.status.setText(str(exc))
    def copy_context_valid(self,value):
        return self.client and self.client.signed_in and self.client.url==value['origin'] and self.client.identity['id']==value['identity'] and self.household.currentData()==value['scope'] and self.selected_snapshot()['id']==value['selected']
    def resume_copy(self):
        if self._pending_copy and self._subscribed and not self.manager.stopping:
            pending=self._pending_copy;self._pending_copy=None
            try:
                client=self.require_session()
                if not self.copy_context_valid(pending):raise ValueError('Service identity or selection changed. No local copy was saved; review the intended snapshot again.')
                self._copy_review=pending
                self.request('copy_verified',lambda:dict(record=client.call('GET','/service/v1/snapshots/'+pending['selected'])))
            except ValueError as exc:self._copy_review=None;self.status.setText(str(exc))
    def detach_copy(self):
        try:
            from .shared_sync import PlanningCopies,detach_investment
            client=self.require_session();record=self.selected_snapshot()
            if QMessageBox.question(self,'Detach local copy mapping?','Remove the version/update mapping for this selected service record and identity? Local amounts/history remain and the service is unchanged. A later copy needs a newly reviewed destination; creating another copy could double count these records. Investment mapping metadata is removed; keep a backup if you need its previous source/version.')!=QMessageBox.StandardButton.Yes:return
            if record['kind']=='investment_account':
                if not self.owner.stocks_page.layout_store.flush():raise ValueError('Save pending workspace edits successfully before detaching.')
                detach_investment(self.owner.investment_store,client.url,client.identity['id'],record['id']);self.owner.stocks_page.reload()
            else:PlanningCopies(self.owner.planning_store).detach(client.url,client.identity['id'],record['id'])
            self.status.setText('Local mapping detached. Financial records and service snapshots are unchanged.')
        except (ValueError,OSError) as exc:self.status.setText(str(exc))
    def show_shared(self):
        try:self.shared_details.setPlainText(json.dumps(self.selected_snapshot(),indent=2,ensure_ascii=False))
        except ValueError:self.shared_details.clear()
    def withdraw(self):
        try:
            client=self.require_session();record=self.selected_snapshot()
            if record['creator']!=client.identity['id']:raise ValueError('Only the creator can withdraw this snapshot.')
            if record['householdId'] is None:raise ValueError('This service snapshot is already private to your identity.')
            if QMessageBox.question(self,'Withdraw publication?','Make this exact snapshot private to your identity? Local records are unchanged.')==QMessageBox.StandardButton.Yes:self.request('mutation',lambda:client.call('PUT','/service/v1/snapshots/'+record['id'],dict(kind=record['kind'],payload=record['payload'],householdId=None,expectedVersion=record['version'])))
        except ValueError as exc:self.status.setText(str(exc))
    def edit_shared(self):
        try:
            client=self.require_session();record=self.selected_snapshot()
            if record['kind']=='investment_account':raise ValueError('Update investment records through a reviewed replacement from the selected local account; holdings do not infer trades or lots.')
            editor=SharedValueEditor(self.owner,record)
            if editor.exec()==editor.DialogCode.Accepted and editor.payload:
                value=deepcopy(editor.payload);self.request('mutation',lambda:client.call('PUT','/service/v1/snapshots/'+record['id'],dict(kind=record['kind'],payload=value,householdId=record['householdId'],expectedVersion=record['version'])))
        except ValueError as exc:self.status.setText(str(exc))
    def replace_shared(self):
        try:
            client=self.require_session();record=self.selected_snapshot();kind,payload=self.selected_local()
            if kind!=record['kind']:raise ValueError('Choose a local record of the same account/goal type as the selected service snapshot.')
            if QMessageBox.question(self,'Replace remote records?',f"Replace all records in {record['payload']['name']} (version {record['version']}) using {payload['name']}? This is a full reviewed replacement; omitted history is removed from the service copy. Local records are unchanged.")!=QMessageBox.StandardButton.Yes:return
            review=ShareReviewDialog(self.owner,kind,payload,'the selected existing snapshot at '+client.url+' as '+client.identity['username'])
            if review.exec()==review.DialogCode.Accepted and review.reviewed.isChecked():self.request('mutation',lambda:client.call('PUT','/service/v1/snapshots/'+record['id'],dict(kind=kind,payload=payload,householdId=record['householdId'],expectedVersion=record['version'])))
        except ValueError as exc:self.status.setText(str(exc))
    def delete_snapshot(self):
        try:
            client=self.require_session();record=self.selected_snapshot()
            if record['creator']!=client.identity['id']:raise ValueError('Only the creator can delete this snapshot.')
            if QMessageBox.question(self,'Delete service snapshot?','Delete this service copy? Local records remain. Household readers lose access.')==QMessageBox.StandardButton.Yes:self.request('mutation',lambda:client.call('DELETE','/service/v1/snapshots/'+record['id'],dict(expectedVersion=record['version'])))
        except ValueError as exc:self.status.setText(str(exc))
    def selected_connection(self):
        row=self.institutions.currentRow()
        if row<0:raise ValueError('Select an institution connection first.')
        return self.connections[row]['id']
    def start_link(self):
        try:
            client=self.require_session();products=(['transactions'] if self.transactions.isChecked() else [])+(['investments'] if self.investments.isChecked() else [])
            if not products:raise ValueError('Select the read-only products you want before starting consent.')
            if QMessageBox.question(self,'Review institution consent?','Open Plaid Hosted Link in your browser for '+', '.join(products)+'? Institution data and credentials are handled by Plaid and your reviewed service; local profiles are not uploaded.')==QMessageBox.StandardButton.Yes:self.request('link',lambda:client.call('POST','/service/v1/connections/link',dict(products=products)))
        except ValueError as exc:self.status.setText(str(exc))
    def complete_link(self):
        try:
            client=self.require_session();ident=self.pending_link
            if not ident:raise ValueError('Start and finish a reviewed browser session first.')
            self.request('link_complete',lambda:client.call('POST',f'/service/v1/connections/link/{ident}/complete'))
        except ValueError as exc:self.status.setText(str(exc))
    def cancel_link(self):
        try:
            client=self.require_session();ident=self.pending_link
            if not ident:raise ValueError('No pending browser session is selected.')
            if QMessageBox.question(self,'Cancel pending session?','Stop accepting this pending Link result? Existing connected Items are unchanged; this does not claim bank-side revocation.')==QMessageBox.StandardButton.Yes:self.request('link_canceled',lambda:client.call('DELETE',f'/service/v1/connections/link/{ident}'))
        except ValueError as exc:self.status.setText(str(exc))
    def reopen_link(self):
        try:
            self.require_session();metadata=self.pending_sessions.currentData(Qt.ItemDataRole.UserRole.value+1)
            if not metadata or metadata.get('status','pending')!='pending' or not isinstance(metadata.get('url'),str) or metadata['expires']<=datetime.now(timezone.utc).timestamp():raise ValueError('Refresh and select an unexpired pending browser session first. Retained exchanges are completed without reopening the browser; uncertain outcomes require operator review.')
            parsed=QUrl(metadata.get('url') or '')
            if parsed.scheme()!='https' or parsed.host()!='secure.plaid.com' or parsed.userName() or parsed.password():raise ValueError('This pending session has no validated browser URL; start a new consent flow.')
            if not QDesktopServices.openUrl(parsed):raise ValueError('The configured browser could not open the consent session.')
        except ValueError as exc:self.status.setText(str(exc))
    def refresh_connection(self):
        try:client=self.require_session();ident=self.selected_connection();self.request('connection_refreshed',lambda:client.call('POST',f'/service/v1/connections/{ident}/refresh',dict(freshBalances=False)))
        except ValueError as exc:self.status.setText(str(exc))
    def review_connection(self):
        try:client=self.require_session();ident=self.selected_connection();self.request('provider_snapshot',lambda:client.call('GET',f'/service/v1/connections/{ident}/snapshot'))
        except ValueError as exc:self.status.setText(str(exc))
    def import_balance(self):
        try:
            client=self.require_session();ident=self.selected_connection();origin=client.url;identity=client.identity['id'];token=client.token
            self.request('institution_balance_fetched',lambda:dict(snapshot=client.call('GET',f'/service/v1/connections/{ident}/balances'),origin=origin,identity=identity,token=token,selected=ident))
        except ValueError as exc:self.status.setText(str(exc))
    def disconnect_connection(self):
        try:
            client=self.require_session();ident=self.selected_connection()
            if QMessageBox.question(self,'Revoke institution connection?','Ask Plaid to revoke this Item and then delete its service data? A failed provider removal will preserve the connection for retry. Already imported local records remain.')==QMessageBox.StandardButton.Yes:self.request('mutation',lambda:client.call('DELETE',f'/service/v1/connections/{ident}'))
        except ValueError as exc:self.status.setText(str(exc))
    def result(self,tag,value,error):
        if tag.startswith(('enrollment_','privacy_')):return
        if tag=='institution_balance_verified':return
        if error:
            if tag=='copy_verified':self._copy_review=None
            if not self.client or not self.client.signed_in:self.clear_session_data()
            self.status.setText(error);return
        if tag=='institution_balance_fetched':
            try:
                from .institution_balances_ui import InstitutionBalanceReview
                if not self.client or not self.client.signed_in or (self.client.url,self.client.identity['id'],self.client.token)!=(value['origin'],value['identity'],value['token']) or value['selected']!=self.selected_connection() or value['snapshot']['id']!=value['selected']:raise ValueError('Institution selection/service session changed; no balance was imported.')
                review=InstitutionBalanceReview(self,value['snapshot']);review.exec()
                self.status.setText('Reviewed local institution balance saved. Transactions/holdings were not imported.' if review.saved_result else 'No new local balance saved. Review current source and destination before retrying.')
            except (ValueError,KeyError,OSError) as exc:self.status.setText(str(exc))
            return
        if tag=='copy_fetched':
            try:
                from .shared_sync_ui import SharedCopyReview
                if not self.copy_context_valid(value):raise ValueError('Service identity or selection changed; no local copy was saved.')
                if value['record'].get('id')!=value['selected']:raise ValueError('The service returned a different snapshot from the selected record. No local copy was saved.')
                if value['record']['kind']=='investment_account' and not self.owner.stocks_page.layout_store.flush():raise ValueError('Save pending investment workspace edits before building this review.')
                review=SharedCopyReview(self.owner,value['origin'],value['identity'],self.client.identity['username'],value['record'])
                if review.exec()==review.DialogCode.Accepted and review.confirmed:
                    preview,replace,duplicates=review.confirmed;value['review']=(preview,replace,duplicates);self._pending_copy=value
                    if not self.manager.busy:self.resume_copy()
                else:self.status.setText('Copy canceled. No local financial records were changed.')
            except (ValueError,OSError) as exc:self.status.setText(str(exc))
            return
        if tag=='copy_verified':
            pending=self._copy_review;self._copy_review=None
            try:
                from .shared_sync import fingerprint,PlanningCopies,investment_commit
                if not pending or not self.copy_context_valid(pending) or getattr(self.owner,'is_demo',False):raise ValueError('The service session/selection is no longer the reviewed context. No local copy was saved.')
                preview,replace,duplicates=pending['review']
                if fingerprint(value['record'])!=fingerprint(preview.value['record']):raise ValueError('The service snapshot changed after review. No local copy was saved; refresh and review its current version.')
                if value['record']['kind']=='investment_account':
                    if not self.owner.stocks_page.layout_store.flush():raise ValueError('Save pending workspace edits before copying.')
                    result=investment_commit(self.owner.investment_store,preview,reviewed=True,replace_local=replace,duplicates_reviewed=duplicates);self.owner.stocks_page.reload()
                else:
                    result=PlanningCopies(self.owner.planning_store).commit(preview,reviewed=True,replace_local=replace,duplicates_reviewed=duplicates);self.owner.refresh_planning()
                self.local_record.blockSignals(True);self.local_record.clear()
                for ident,label in self.local_choices():self.local_record.addItem(label,ident)
                self.local_record.blockSignals(False)
                self.status.setText('Already copied; local records preserved.' if result['status']=='unchanged' else 'Reviewed local copy saved with service version/source. Accounts/goals preserve other dates; selected investment history follows the reviewed full replacement. No service data was changed.')
            except (ValueError,OSError) as exc:self.status.setText(str(exc))
            return
        if tag=='login':self.status.setText('Signed in as '+self.client.identity['username']+' at '+self.client.url+'. Refresh service state to choose a household or connection.');return
        if tag=='logout':self.clear_session_data();self.status.setText('Signed out; server session revoked and local credentials cleared.');return
        if tag=='refresh':
            selected=self.household.currentData();self.households=value['households'];self.connections=value['connections'];self.household.blockSignals(True);self.household.clear();self.household.addItem('My created records (new uploads private)',None)
            for row in self.households:self.household.addItem(row['name']+' · '+row['role'],row['id'])
            self.household.setCurrentIndex(max(0,self.household.findData(selected)));self.household.blockSignals(False)
            pending=self.pending_link;self.pending_sessions.blockSignals(True);self.pending_sessions.clear();self.pending_sessions.addItem('Select a pending consent session',None)
            for item in value['pending']:
                self.pending_sessions.addItem(item['id']+' · '+item.get('status','pending')+' · '+', '.join(item['products']),item['id']);self.pending_sessions.setItemData(self.pending_sessions.count()-1,item,Qt.ItemDataRole.UserRole.value+1)
            self.pending_sessions.setCurrentIndex(max(0,self.pending_sessions.findData(pending)));self.pending_sessions.blockSignals(False);self.pending_link=self.pending_sessions.currentData()
            self.institutions.setRowCount(len(self.connections))
            for index,row in enumerate(self.connections):
                for col,field in enumerate(('id','environment','status','lastSuccess','lastAttempt')):self.institutions.setItem(index,col,QTableWidgetItem(row[field]))
            self.status.setText('Service state refreshed. Choose a household/private scope and refresh its selected records. Provider: '+str(value['capabilities']['provider']));return
        if tag=='scope':
            if value['scope']!=self.household.currentData():self.status.setText('Scope changed during the request; refresh the newly selected scope.');return
            self.snapshots=value['snapshots'];self.shared.setRowCount(len(self.snapshots));self.members.setRowCount(len(value['members']))
            for row,record in enumerate(self.snapshots):
                for col,field in enumerate((record['payload']['name'],record['kind'],str(record['version']),record['updated'],'Household members' if record['householdId'] else 'Private to creator')):self.shared.setItem(row,col,QTableWidgetItem(field))
            for row,record in enumerate(value['members']):
                self.members.setItem(row,0,QTableWidgetItem(record['username']));self.members.item(row,0).setData(Qt.ItemDataRole.UserRole,record['id']);self.members.setItem(row,1,QTableWidgetItem(record['role']))
            self.status.setText(f'{len(self.snapshots)} accessible selected snapshots. Private records from other identities are excluded.');return
        if tag=='invite':
            dialog=FitDialog(self);dialog.setWindowTitle('Private one-use invitation');layout=QVBoxLayout(dialog);layout.addWidget(text('Give this invitation only to the intended identity: '+value['username']+' · '+value['role'],'muted',True));field=QLineEdit(value['invite']);field.setReadOnly(True);layout.addWidget(field);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons);dialog.exec();reset_private(field);self.status.setText('Invitation created. It expires in 24 hours; access starts only after acceptance.');return
        if tag=='link':
            parsed=QUrl(value['url'])
            if parsed.scheme()!='https' or parsed.host()!='secure.plaid.com' or parsed.userName() or parsed.password():self.status.setText('The service returned an invalid Plaid browser URL; it was not opened.');return
            self.pending_link=value['id'];opened=QDesktopServices.openUrl(parsed);self.status.setText(('Complete the Plaid browser session, then check completion. ' if opened else 'The browser could not open. Retry from your configured browser. ')+value['environment']+' data; no connection is assumed yet.');return
        if tag=='link_complete' and value['status']=='waiting':self.status.setText(value.get('message','Browser consent is not complete.'));return
        if tag in ('link_complete','link_canceled'):self.pending_link=None
        if tag=='provider_snapshot':
            preview=deepcopy(value);total=len(preview.get('transactions',[]));preview['transactions']=preview.get('transactions',[])[:100]
            if total>100:preview['previewNote']=f'Showing the first 100 of {total} saved transactions; no missing records are assumed zero.'
            self.provider_details.setPlainText(json.dumps(preview,indent=2,ensure_ascii=False));self.tabs.setCurrentWidget(self.provider_details);self.status.setText('Saved provider snapshot loaded; no local financial record was changed.');return
        self.status.setText('Service change confirmed. Refresh service state / selected scope to see the latest versions; local records are unchanged.')
    def clear_session_data(self):
        self._pending_copy=None;self._copy_review=None
        self.households=[];self.snapshots=[];self.connections=[];self.pending_link=None
        for table in (self.shared,self.members,self.institutions):table.setRowCount(0)
        self.shared_details.clear();self.provider_details.clear()
        for combo,label in ((self.household,'My created records (new uploads private)'),(self.pending_sessions,'Select a pending consent session')):
            combo.blockSignals(True);combo.clear();combo.addItem(label,None);combo.blockSignals(False)
    def disconnect_ui(self):
        self._pending_copy=None;self._copy_review=None
        reset_private(self.password,self.code)
        if self._subscribed:
            try:self.manager.changed.disconnect(self.result);self.manager.idle.disconnect(self.resume_copy)
            except RuntimeError:pass
            self._subscribed=False
    def closeEvent(self,event):
        self.disconnect_ui();super().closeEvent(event)
    def done(self,result):
        self.disconnect_ui()
        super().done(result)
