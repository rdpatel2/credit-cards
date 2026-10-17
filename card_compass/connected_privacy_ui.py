"""Explicit desktop controls for the signed-in optional service identity."""
from datetime import datetime,timezone
import re
import time
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,
    QPushButton,QCheckBox,QDialogButtonBox,QTabWidget,QTextEdit,QMessageBox,QFileDialog)
from .ui import FitDialog
from .import_ui import grid
from PySide6.QtWidgets import QTableWidgetItem
from .connected_ui import text,reset_private
from .privacy_export import download_export,validate_manifest


def utc(value):
    return datetime.fromtimestamp(value,timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def buttons(layout,items):
    row=QHBoxLayout()
    for label,handler in items:
        button=QPushButton(label);button.clicked.connect(handler);row.addWidget(button)
    layout.addLayout(row)


def populate(table,rows,columns):
    table.setRowCount(len(rows))
    for index,row in enumerate(rows):
        for col,key in enumerate(columns):table.setItem(index,col,QTableWidgetItem(str(row[key])))


class ProofDialog(FitDialog):
    """Fresh proof is only retained by the explicit operation, never on disk."""
    def __init__(self,parent,purpose,new_password=False):
        super().__init__(parent);self.setWindowTitle(purpose);self.resize(650,470)
        layout=QVBoxLayout(self);layout.addWidget(text('Confirm with your current service passphrase and a fresh authenticator code or unused recovery code. A successful proof consumes that code.','muted',True))
        self.password=QLineEdit();self.code=QLineEdit();self.new_password=QLineEdit();self.confirm_password=QLineEdit()
        form=QFormLayout()
        for field in (self.password,self.code,self.new_password,self.confirm_password):field.setEchoMode(QLineEdit.EchoMode.Password);field.setMaxLength(256)
        self.code.setMaxLength(20);form.addRow('Current passphrase',self.password);form.addRow('Fresh second factor',self.code)
        if new_password:form.addRow('New passphrase (14–256 characters)',self.new_password);form.addRow('Confirm new passphrase',self.confirm_password)
        self.changing_password=new_password;layout.addLayout(form);self.status=text('','muted',True);layout.addWidget(self.status)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);self.buttons.accepted.connect(self.confirm);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.payload=None

    def confirm(self):
        if not self.password.text() or len(self.code.text()) not in (6,20):self.status.setText('Enter your current passphrase and a six-digit authenticator or 20-character recovery code.');return
        if self.changing_password and (not 14<=len(self.new_password.text())<=256 or self.new_password.text()!=self.confirm_password.text()):self.status.setText('Use a 14–256 character new passphrase and matching confirmation.');return
        self.payload=dict(password=self.password.text(),code=self.code.text())
        if self.changing_password:self.payload['newPassword']=self.new_password.text()
        self.accept()

    def done(self,result):
        reset_private(self.password,self.code,self.new_password,self.confirm_password)
        if result!=self.DialogCode.Accepted:self.payload=None
        super().done(result)

    def closeEvent(self,event):
        reset_private(self.password,self.code,self.new_password,self.confirm_password);self.payload=None
        super().closeEvent(event)


class ServicePrivacyDialog(FitDialog):
    def __init__(self,parent):
        super().__init__(parent);self.service_page=parent;self.owner=parent.owner;self.manager=parent.manager;self.client=parent.require_session()
        self.context=(self.client.url,self.client.identity['id'],self.client.token);self._subscribed=True
        self.sessions=[];self.setups=[];self.exports=[];self.invitations=[];self.pending=None;self.plan=None
        self.household=parent.household.currentData()
        self.setWindowTitle('Service account and privacy');self.resize(970,700)
        layout=QVBoxLayout(self);layout.addWidget(text(f"Service identity: {self.client.identity['username']} at {self.client.url}. These controls affect the optional service. Your desktop finances and downloaded copies remain separate.",'muted',True))
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        page=QWidget();box=QVBoxLayout(page);self.session_table=grid(['Session','Created','Expires','This desktop']);box.addWidget(self.session_table,1)
        buttons(box,[('Refresh sessions',self.refresh_sessions),('Revoke selected…',self.revoke_session),('Sign out all sessions…',self.logout_all)]);self.tabs.addTab(page,'Sessions')
        page=QWidget();box=QVBoxLayout(page)
        box.addWidget(text('Changing a passphrase or activating new security factors revokes other sessions. The current session stays active. Existing factors remain usable until activation; a pending setup expires after ten minutes.','muted',True))
        buttons(box,[('Change passphrase…',self.change_password),('Replace authenticator…',lambda:self.begin_security('mfa')),('Rotate recovery codes…',lambda:self.begin_security('recovery'))])
        self.security_tabs=QTabWidget();box.addWidget(self.security_tabs,1)
        staged=QWidget();staged_box=QVBoxLayout(staged);form=QFormLayout();self.seed=QLineEdit();self.seed.setReadOnly(True);self.seed.setEchoMode(QLineEdit.EchoMode.Password);self.activation=QLineEdit();self.activation.setReadOnly(True);self.activation.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('New authenticator seed',self.seed);form.addRow('Separate recovery activation code',self.activation);staged_box.addLayout(form)
        self.recovery=QTextEdit();self.recovery.setReadOnly(True);self.recovery.setVisible(False);staged_box.addWidget(self.recovery,1)
        self.reveal=QCheckBox('Reveal private setup material');self.reveal.toggled.connect(self.reveal_material);staged_box.addWidget(self.reveal)
        self.saved=QCheckBox('I stored the new recovery codes privately; for authenticator replacement I enrolled the new seed');staged_box.addWidget(self.saved)
        self.confirm_code=QLineEdit();self.confirm_code.setEchoMode(QLineEdit.EchoMode.Password);self.confirm_code.setMaxLength(20);self.confirm_code.setPlaceholderText('New authenticator code, or separate recovery activation code');staged_box.addWidget(self.confirm_code)
        buttons(staged_box,[('Activate reviewed setup',self.confirm_security),('Cancel this setup',self.cancel_staged)])
        self.security_tabs.addTab(staged,'New setup')
        pending_page=QWidget();pending_box=QVBoxLayout(pending_page);self.setup_table=grid(['Pending setup','Expires','This session']);pending_box.addWidget(self.setup_table,1)
        buttons(pending_box,[('Refresh pending setups',self.refresh_setups),('Cancel selected setup…',self.cancel_selected_setup)]);self.security_tabs.addTab(pending_page,'Pending setups');self.tabs.addTab(page,'Security')
        page=QWidget();box=QVBoxLayout(page);box.addWidget(text('Prepare a consistent copy of your created snapshots and saved institution records, then download ZIP/JSONL outside your desktop profile. Credentials and other identities’ private records are excluded. This is a data export, not a service-restoration backup. Prepared exports expire after 15 minutes.','muted',True))
        self.export_table=grid(['Prepared','Records','Chunks','Expires','This session']);box.addWidget(self.export_table,1)
        buttons(box,[('Prepare my export…',self.prepare_export),('Refresh prepared exports',self.refresh_exports)])
        buttons(box,[('Download selected ZIP…',self.download),('Claim for this session…',self.claim_export),('Remove selected export…',self.remove_export)])
        cancel_download=QPushButton('Cancel active export download');cancel_download.clicked.connect(self.cancel_download);box.addWidget(cancel_download);self.tabs.addTab(page,'Export')
        page=QWidget();box=QVBoxLayout(page);box.addWidget(text('Refresh a deletion review first. Resolve institution consent and transfer sole ownership of a household with other members before deleting your identity. Active service records are removed; local copies, external bank consent and operator backups have separate retention.','muted',True))
        self.deletion_details=QTextEdit();self.deletion_details.setReadOnly(True);box.addWidget(self.deletion_details,1)
        self.close_households=QCheckBox('Also close households where I am the only member');box.addWidget(self.close_households)
        self.confirmation=QLineEdit();self.confirmation.setPlaceholderText('Type your exact service username after reviewing');box.addWidget(self.confirmation)
        buttons(box,[('Refresh deletion review',self.deletion_plan),('Delete reviewed service identity…',self.delete_identity)]);self.tabs.addTab(page,'Delete identity')
        page=QWidget();box=QVBoxLayout(page);box.addWidget(text('Invitations for the household selected in the service window. Owners can revoke unused invitations, including any staged enrollment. Revocation does not remove an identity that already joined.','muted',True))
        self.invite_table=grid(['Intended username','Role','Expires']);box.addWidget(self.invite_table,1)
        buttons(box,[('Refresh invitations',self.refresh_invitations),('Revoke selected invitation…',self.revoke_invitation)]);self.tabs.addTab(page,'Invitations')
        self.status=text('Select a control to fetch current service data.','muted',True);layout.addWidget(self.status)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons);self.manager.changed.connect(self.result);self.manager.progress.connect(self.status.setText)

    def valid_context(self):
        return self.manager.client is self.client and self.client.signed_in and (self.client.url,self.client.identity['id'],self.client.token)==self.context

    def request(self,tag,operation):
        try:
            if not self.valid_context():
                self.clear_identity()
                raise ValueError('The reviewed service session changed; close these controls and sign in again.')
            self.manager.submit('privacy_'+tag,operation);self.status.setText('Contacting your selected service…')
        except ValueError as exc:self.status.setText(str(exc))

    def proof(self,purpose,new_password=False):
        if self.manager.busy:self.status.setText('Wait for the current service request.');return None
        if not self.valid_context():self.clear_identity();self.status.setText('Sign in to the reviewed service again.');return None
        dialog=ProofDialog(self,purpose,new_password)
        try:return dialog.payload if dialog.exec()==dialog.DialogCode.Accepted else None
        finally:dialog.payload=None

    def selected(self,table,rows):
        row=table.currentRow()
        if not 0<=row<len(rows):raise ValueError('Refresh and select a current row first.')
        return rows[row]

    def action(self,operation):
        try:operation()
        except (ValueError,KeyError,TypeError) as exc:self.status.setText(str(exc))

    def refresh_sessions(self):self.request('sessions',lambda:self.client.call('GET','/service/v1/auth/sessions'))
    def revoke_session(self):
        def run():
            row=self.selected(self.session_table,self.sessions);ident=row['id']
            if QMessageBox.question(self,'Revoke service session?','This session will lose service access immediately. Revoking this desktop signs it out; local finances remain available.')==QMessageBox.StandardButton.Yes:self.request('revoke_session',lambda:self.client.call('DELETE','/service/v1/auth/sessions/'+ident))
        self.action(run)
    def logout_all(self):
        if QMessageBox.question(self,'Sign out all service sessions?','Revoke all sessions for your service identity, including this desktop?')==QMessageBox.StandardButton.Yes:self.request('logout',lambda:self.client.logout(True))
    def change_password(self):
        payload=self.proof('Change service passphrase',True)
        if payload:self.request('password',lambda:self.client.call('POST','/service/v1/auth/password',payload))
    def refresh_setups(self):self.request('setups',lambda:self.client.call('GET','/service/v1/auth/security'))
    def begin_security(self,kind):
        if self.pending:self.status.setText('Activate or cancel the displayed setup first.');return
        payload=self.proof('Start '+('authenticator replacement' if kind=='mfa' else 'recovery-code rotation'))
        if payload:self.request('security_begin',lambda:self.client.call('POST',f'/service/v1/auth/security/{kind}/begin',payload))
    def reveal_material(self,revealed):
        mode=QLineEdit.EchoMode.Normal if revealed else QLineEdit.EchoMode.Password
        self.seed.setEchoMode(mode);self.activation.setEchoMode(mode);self.recovery.setVisible(revealed)
    def clear_material(self):
        self.pending=None;reset_private(self.seed,self.activation,self.confirm_code);self.recovery.setPlainText('');self.reveal.setChecked(False);self.saved.setChecked(False)
    def confirm_security(self):
        if not self.pending or self.pending['expires']<=time.time():self.status.setText('Setup is missing or expired. Refresh pending setups and cancel/restart; existing factors stay active.');return
        if not self.saved.isChecked():self.status.setText('Store the new recovery codes privately and review the new factor before activation.');return
        ident=self.pending['id'];code=self.confirm_code.text();reset_private(self.confirm_code)
        if len(code) not in (6,20):self.status.setText('Enter the new authenticator code or separate recovery activation code.');return
        self.request('security_confirm',lambda:self.client.call('POST',f'/service/v1/auth/security/{ident}/confirm',dict(code=code)))
    def cancel_staged(self):
        if not self.pending:self.status.setText('No displayed setup. Use Pending setups to cancel a lost-response setup.');return
        ident=self.pending['id'];self.request('security_cancel',lambda:self.client.call('DELETE','/service/v1/auth/security/'+ident))
    def cancel_selected_setup(self):
        def run():
            row=self.selected(self.setup_table,self.setups);ident=row['id']
            if QMessageBox.question(self,'Cancel staged security setup?','Keep existing security factors and discard this staged setup?')==QMessageBox.StandardButton.Yes:self.request('security_cancel',lambda:self.client.call('DELETE','/service/v1/auth/security/'+ident))
        self.action(run)
    def refresh_exports(self):self.request('exports',lambda:self.client.call('GET','/service/v1/privacy/exports'))
    def prepare_export(self):
        payload=self.proof('Prepare my service data export')
        if payload:self.request('export_prepared',lambda:self.client.call('POST','/service/v1/privacy/exports',payload))
    def download(self):
        def run():
            manifest=dict(self.selected(self.export_table,self.exports));validate_manifest(manifest)
            if not manifest.get('currentSession'):raise ValueError('Claim this export with fresh proof before downloading in this session.')
            path,_=QFileDialog.getSaveFileName(self,'Save private service data archive','finance-compass-service-export.zip','ZIP archive (*.zip)')
            if path:self.request('downloaded',lambda:download_export(self.client,manifest,path,self.owner.directory,progress=self.manager.progress.emit,canceled=lambda:self.manager.stopping or self.manager.worker.isInterruptionRequested()))
        self.action(run)
    def cancel_download(self):
        worker=self.manager.worker
        if worker and worker.tag=='privacy_downloaded':worker.requestInterruption();self.status.setText('Cancel requested. Waiting for the current chunk response; existing archive is preserved.')
        else:self.status.setText('No active export download. Other service changes must finish before closing.')
    def claim_export(self):
        def run():
            ident=self.selected(self.export_table,self.exports)['id'];payload=self.proof('Claim my prepared export for this session')
            if payload:self.request('claimed',lambda:self.client.call('POST',f'/service/v1/privacy/exports/{ident}/claim',payload))
        self.action(run)
    def remove_export(self):
        def run():
            ident=self.selected(self.export_table,self.exports)['id']
            if QMessageBox.question(self,'Remove prepared service export?','Delete this temporary service export? Existing downloaded archives remain.')==QMessageBox.StandardButton.Yes:self.request('export_removed',lambda:self.client.call('DELETE','/service/v1/privacy/exports/'+ident))
        self.action(run)
    def deletion_plan(self):
        self.plan=None;self.deletion_details.clear();reset_private(self.confirmation);self.close_households.setChecked(False)
        self.request('plan',lambda:self.client.call('GET','/service/v1/privacy/deletion-plan'))
    def delete_identity(self):
        if not self.plan:self.status.setText('Fetch and review current deletion details first.');return
        if self.plan['blockers']:self.status.setText('Resolve the listed ownership and institution-consent blockers first.');return
        if self.confirmation.text()!=self.plan['username']:self.status.setText('Type the exact reviewed service username.');return
        if any(home['closeIfSole'] for home in self.plan['households']) and not self.close_households.isChecked():self.status.setText('Explicitly review closing households where you are the only member.');return
        payload=self.proof('Confirm reviewed service identity deletion')
        if not payload:return
        payload.update(planHash=self.plan['planHash'],confirmation=self.confirmation.text(),closeSoleHouseholds=self.close_households.isChecked());reset_private(self.confirmation)
        self.request('deleted',lambda:self.client.call('POST','/service/v1/privacy/delete-identity',payload))
    def refresh_invitations(self):
        if not self.household:self.status.setText('Select an owned household in the service window, then reopen these controls.');return
        self.request('invitations',lambda:self.client.call('GET',f'/service/v1/households/{self.household}/invitations'))
    def revoke_invitation(self):
        def run():
            row=self.selected(self.invite_table,self.invitations);ident=row['digest'];home=self.household
            if QMessageBox.question(self,'Revoke unused invitation?',f"Revoke the invitation for {row['username']} and any pending enrollment? Accepted membership is unchanged.")==QMessageBox.StandardButton.Yes:self.request('invitation_revoked',lambda:self.client.call('DELETE',f'/service/v1/households/{home}/invitations/{ident}'))
        self.action(run)

    def clear_identity(self):
        self.client.forget();self.service_page.clear_session_data();self.clear_material();self.plan=None;self.deletion_details.clear();reset_private(self.confirmation)
        self.sessions=[];self.setups=[];self.exports=[];self.invitations=[]
        for table in (self.session_table,self.setup_table,self.export_table,self.invite_table):table.setRowCount(0)

    def result(self,tag,value,error):
        if not tag.startswith('privacy_'):return
        if error:
            if not self.valid_context():self.clear_identity()
            self.status.setText(error+' Refresh state before retrying an interrupted change.');return
        tag=tag.removeprefix('privacy_')
        if tag=='logout':self.clear_identity();self.status.setText('All service sessions revoked. Desktop finances remain available.');return
        if not self.valid_context():self.clear_identity();self.status.setText('Service session changed; the response was discarded.');return
        try:
            if tag=='sessions':
                self.sessions=value['sessions'];populate(self.session_table,[dict(label=r['label'],created=r['created'] or 'Unknown (legacy session)',expires=utc(r['expires']),current='Yes' if r['current'] else 'No') for r in self.sessions],['label','created','expires','current'])
            elif tag=='setups':
                self.setups=value['pending'];populate(self.setup_table,[dict(kind=r['kind'],expires=utc(r['expires']),current='Yes' if r['currentSession'] else 'No') for r in self.setups],['kind','expires','current'])
            elif tag=='security_begin':
                if value['kind'] not in ('mfa','recovery') or not re.fullmatch('[a-f0-9]{32}',value['id']) or not time.time()<value['expires']<=time.time()+660 or len(value['recoveryCodes'])!=10 or len(set(value['recoveryCodes']))!=10 or any(not re.fullmatch('[a-f0-9]{20}',item) for item in value['recoveryCodes']):raise ValueError('Invalid security setup response. Existing factors remain active; refresh pending setups and cancel/restart.')
                if value['kind']=='mfa' and not re.fullmatch('[A-Z2-7]{32}',value['mfaSecret']):raise ValueError('Invalid authenticator response. Cancel the pending setup.')
                if value['kind']=='recovery' and not re.fullmatch('[a-f0-9]{20}',value['activationCode']):raise ValueError('Invalid recovery activation response. Cancel the pending setup.')
                self.clear_material();self.pending=dict(id=value['id'],kind=value['kind'],expires=value['expires']);self.seed.setText(value['mfaSecret'] or '');self.activation.setText(value['activationCode'] or '');self.recovery.setPlainText('\n'.join(value['recoveryCodes']));self.security_tabs.setCurrentIndex(0)
                self.status.setText('Existing factors remain active. Store new recovery codes privately, then confirm the new factor before '+utc(value['expires'])+'.');return
            elif tag=='security_confirm':
                if value.get('changed') is not True:raise ValueError('Activation was not confirmed. Refresh state before retrying.')
                self.clear_material();self.setups=[];self.setup_table.setRowCount(0);self.sessions=[];self.session_table.setRowCount(0)
                self.status.setText('New security factors activated. Other sessions revoked; this session remains active.');return
            elif tag=='security_cancel':
                if value.get('canceled') is not True:raise ValueError('Cancellation was not confirmed; refresh pending setups.')
                self.clear_material();self.setups=[];self.setup_table.setRowCount(0);self.status.setText('Pending setup canceled. Existing security factors retained.');return
            elif tag in ('exports','export_prepared'):
                self.exports=value['exports'] if tag=='exports' else [dict(validate_manifest(value),currentSession=True)]
                populate(self.export_table,[dict(created=r['created'],records=sum(r['counts'].values()),chunks=r['chunkCount'],expires=utc(r['expires']),current='Yes' if r['currentSession'] else 'No') for r in self.exports],['created','records','chunks','expires','current'])
                if self.exports:self.export_table.selectRow(0)
            elif tag=='downloaded':
                self.status.setText('Private archive saved: '+value['saved']+'. '+('Temporary service export removed.' if value['serverSnapshotRemoved'] else 'Service cleanup was not confirmed. Remove the prepared export or let access expire at '+utc(value['expires'])+'.'));return
            elif tag=='plan':
                self.plan=value
                lines=[f"Identity: {value['username']}",f"Created snapshots: {value['createdSnapshots']}; sessions: {value['sessions']}",f"Temporary exports: {value['pendingExports']}; pending security changes: {value['pendingSecurityChanges']}",f"Institution connections: {value['institutionConnections']}; unresolved consent sessions: {len(value['unresolvedConsents'])}"]
                lines.extend(f"Household: {r['name']} · {r['role']} · {r['members']} members · {'will close only with explicit review' if r['closeIfSole'] else 'other members remain'}" for r in value['households'])
                lines.extend('BLOCKED: '+line for line in value['blockers']);lines.append(value['limitations']);self.deletion_details.setPlainText('\n\n'.join(lines))
            elif tag=='deleted':
                if value.get('identityDeleted') is not True:raise ValueError('Identity deletion was not confirmed. Refresh the deletion review.')
                self.clear_identity();self.status.setText('Service identity deleted. Receipt: '+value['receiptId']+'. Local copies remain. This does not claim external bank revocation or backup erasure.');return
            elif tag=='invitations':
                self.invitations=value['invitations'];populate(self.invite_table,[dict(username=r['username'],role=r['role'],expires=utc(r['expires'])) for r in self.invitations],['username','role','expires'])
            elif tag=='revoke_session':
                if value.get('revoked') is not True:raise ValueError('Session revocation was not confirmed; refresh sessions.')
                self.sessions=[];self.session_table.setRowCount(0)
                if value.get('current'):self.clear_identity();self.status.setText('This desktop service session revoked. Local finances remain available.');return
            elif tag=='password':
                if value.get('changed') is not True:raise ValueError('Passphrase change was not confirmed; refresh state before retrying.')
                self.clear_material();self.sessions=[];self.session_table.setRowCount(0);self.status.setText('Service passphrase changed. Other sessions revoked; this session remains active.');return
            elif tag=='claimed':
                self.exports=[];self.export_table.setRowCount(0);self.status.setText('Export claimed for this session. Refresh prepared exports, then download.');return
            elif tag=='export_removed':self.exports=[];self.export_table.setRowCount(0)
            elif tag=='invitation_revoked':self.invitations=[];self.invite_table.setRowCount(0)
            self.status.setText('Service response received. Refresh relevant state after a change.')
        except (ValueError,KeyError,TypeError,OverflowError) as exc:self.status.setText(str(exc))

    def clear_controls(self):
        self.clear_material();reset_private(self.confirmation);self.deletion_details.clear();self.plan=None
        self.sessions=[];self.setups=[];self.exports=[];self.invitations=[]
        for table in (self.session_table,self.setup_table,self.export_table,self.invite_table):table.setRowCount(0)
        self.context=()
        if self._subscribed:self.manager.changed.disconnect(self.result);self.manager.progress.disconnect(self.status.setText);self._subscribed=False

    def done(self,result):
        if self.manager.busy:self.status.setText('Wait for the current service request before closing.');return
        self.clear_controls()
        super().done(result)

    def closeEvent(self,event):
        if self.manager.busy:self.status.setText('Wait for the current service request before closing.');event.ignore();return
        self.clear_controls()
        super().closeEvent(event)
