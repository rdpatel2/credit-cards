"""Audit native dialog text rectangles, wrapping and small-screen reachability."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtCore import Qt, QSize, QTimer, QDate, QEvent
from PySide6.QtWidgets import (QApplication, QLineEdit, QComboBox, QLabel, QStyle, QStyleOptionFrame,
    QStyleOptionComboBox, QDialogButtonBox, QVBoxLayout, QFormLayout, QMessageBox, QInputDialog, QFileDialog)
from PySide6.QtTest import QTest
from card_compass.app import MainWindow, CardSettings, CustomCard
from card_compass.finance_ui import EntryDialog, QuickStartDialog, dollars
from card_compass.stocks_ui import TradeDialog, ViewSettingsDialog
from card_compass.import_ui import CsvMappingDialog, ImportDialog
from card_compass.dashboard.tools import DashboardSettings
from card_compass.dashboard.gallery import WidgetGallery
from card_compass.dashboard.widget_registry import ConfigPanel, REGISTRY
from card_compass.ui import FitDialog
from tests.statement_examples import BOFA
from tests.test_usability import add_statement


@pytest.fixture
def owner(tmp_path):
    app=QApplication.instance() or QApplication([])
    # Earlier UI tests can retain closed windows through signal closures. Clear
    # them before changing the application theme for this isolated audit.
    for previous in app.topLevelWidgets():
        if isinstance(previous,MainWindow) and not previous.isVisible():previous.deleteLater()
    app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    window=MainWindow(tmp_path,auto_refresh=False)
    yield window
    window.close();window.deleteLater();app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def assert_text_fits(dialog):
    for edit in dialog.findChildren(QLineEdit):
        if not edit.isVisibleTo(dialog):continue
        option=QStyleOptionFrame();option.initFrom(edit)
        area=edit.style().subElementRect(QStyle.SubElement.SE_LineEditContents,option,edit)
        assert area.height()>=edit.fontMetrics().height()+2,(dialog.windowTitle(),edit.text(),area.height(),edit.fontMetrics().height())
    for combo in dialog.findChildren(QComboBox):
        if not combo.isVisibleTo(dialog) or combo.isEditable():continue
        option=QStyleOptionComboBox();combo.initStyleOption(option)
        area=combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,combo)
        assert area.height()>=combo.fontMetrics().height()+2,(dialog.windowTitle(),combo.currentText(),area.height())
    for label in dialog.findChildren(QLabel):
        if not label.isVisibleTo(dialog) or not label.text() or not label.wordWrap():continue
        if label.maximumHeight()<16777215:continue
        required=label.heightForWidth(label.width())
        assert label.height()>=required,(dialog.windowTitle(),label.text(),label.size().toTuple(),required)


FAMILIES=['entry','income','edit-series','quickstart','card-settings','custom-card','csv','import','views','trade','sale','dashboard','gallery','details','input','message','file']


def build_dialog(owner,kind):
    card=owner.by_id['bank-of-america-customized-cash-rewards']
    p=owner.stocks_page
    if kind=='entry':return EntryDialog(owner,'2026-10')
    if kind=='income':return EntryDialog(owner,'2026-10',monthly_income=True)
    if kind=='edit-series':return EntryDialog(owner,'2026-10',dict(name='Recurring income with descenders: gypqj',kind='income',category='salary',amount_cents=100000,recurring=True,start_month='2026-01',end_month=None))
    if kind=='quickstart':return QuickStartDialog(owner.overview_page)
    if kind=='card-settings':return CardSettings(card,owner.profile,owner)
    if kind=='custom-card':return CustomCard(owner,owner.imports_page.categories())
    if kind=='csv':return CsvMappingDialog('Date,Description,Amount\n2026-10-01,Groceries,45.50\n',owner)
    if kind=='import':
        dialog=ImportDialog('/synthetic.pdf',owner.imports_page.ledger,[card],owner.imports_page.categories(),owner,extracted_text=BOFA)
        dialog.card.setCurrentIndex(1)
        return dialog
    if kind=='views':return ViewSettingsDialog(p)
    if kind in ('trade','sale'):return TradeDialog(p,'AAPL',scenario=kind=='sale')
    if kind=='dashboard':return DashboardSettings(p.current_page()['settings'],p)
    if kind=='gallery':return WidgetGallery(p.data_layer,p)
    if kind=='details':owner.show_card(card,reveal=False);return owner.details_dialog
    if kind=='input':
        dialog=QInputDialog(owner);dialog.setLabelText('Name this dashboard');dialog.setTextValue('Descenders: gypqj résumé');return dialog
    if kind=='message':return QMessageBox(QMessageBox.Icon.Information,'Saved locally','Your saved data includes budgets, income, trades and notes.\nYou can change these settings later.',QMessageBox.StandardButton.Ok,owner)
    if kind=='file':
        dialog=QFileDialog(owner);dialog.setOption(QFileDialog.Option.DontUseNativeDialog);dialog.setDirectory(str(owner.directory));return dialog
    raise AssertionError(kind)


@pytest.mark.parametrize('dark',[False,True])
@pytest.mark.parametrize('kind',FAMILIES)
def test_dialog_family_text_is_not_clipped(owner,kind,dark):
    owner.apply_theme(dark,persist=False)
    dialog=build_dialog(owner,kind);dialog.show();QTest.qWait(10)
    assert_text_fits(dialog)
    if isinstance(dialog,FitDialog):
        limit=dialog.available_dialog_size()
        assert dialog.size().width()<=limit.width() and dialog.size().height()<=limit.height()
    dialog.close()
    if dialog is not owner.details_dialog:dialog.deleteLater()


@pytest.mark.parametrize('dark',[False,True])
@pytest.mark.parametrize('kind',[kind for kind in REGISTRY.definitions if kind!='unknown'])
def test_every_widget_configuration_has_readable_fields(owner,kind,dark):
    owner.apply_theme(dark,persist=False)
    dialog=ConfigPanel(REGISTRY.get(kind),dict(symbol='AAPL',watchlistId='core',range='1mo',earningsDate='2026-10-09'),owner.stocks_page.data_layer,owner)
    dialog.show();QTest.qWait(5);assert_text_fits(dialog)
    dialog.close();dialog.deleteLater()


def test_small_screen_scrolls_long_forms_and_keeps_footer_visible(owner,monkeypatch):
    dialog=FitDialog(owner);monkeypatch.setattr(dialog,'available_dialog_size',lambda:QSize(480,300))
    layout=QVBoxLayout(dialog);form=QFormLayout();layout.addLayout(form)
    for index in range(18):form.addRow('Long amount label '+str(index),dollars(index))
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
    layout.addWidget(buttons);dialog.resize(600,1000);dialog.show();QTest.qWait(15)
    assert dialog.size()==QSize(480,300)
    assert dialog.text_scroll.verticalScrollBar().maximum()>0
    assert dialog.rect().contains(buttons.geometry())
    assert buttons.parentWidget() is dialog
    assert_text_fits(dialog)
    dialog.text_scroll.verticalScrollBar().setValue(dialog.text_scroll.verticalScrollBar().maximum())
    assert_text_fits(dialog);dialog.close();dialog.deleteLater()


def test_validation_text_can_grow_without_clipping_or_hiding_actions(owner,monkeypatch):
    dialog=QuickStartDialog(owner.overview_page);monkeypatch.setattr(dialog,'available_dialog_size',lambda:QSize(500,440))
    dialog.show();QTest.qWait(10)
    dialog.error.setText('Check the monthly amounts before saving. ' * 12);QTest.qWait(15)
    assert_text_fits(dialog)
    assert dialog.width()<=500 and dialog.height()<=440
    buttons=dialog.findChild(QDialogButtonBox)
    assert buttons.parentWidget() is dialog and dialog.rect().contains(buttons.geometry())
    dialog.close();dialog.deleteLater()


def test_import_tables_keep_description_and_merchant_fields_readable(owner):
    dialog=build_dialog(owner,'import');dialog.show();QTest.qWait(15)
    assert dialog.merchants.columnWidth(0)>=350
    assert dialog.transactions.columnWidth(0)>=240
    title=dialog.merchants.horizontalHeaderItem(0)
    assert dialog.merchants.fontMetrics().horizontalAdvance(title.text())+32<dialog.merchants.columnWidth(0)
    assert title.toolTip()
    assert dialog.merchants.rowCount()>0 and dialog.transactions.rowCount()>0
    dialog.tabs.setCurrentWidget(dialog.transactions);QTest.qWait(5)
    assert dialog.transactions.horizontalScrollBar().maximum()>0
    assert_text_fits(dialog)
    dialog.close();dialog.deleteLater()


@pytest.mark.parametrize('action',['assumptions','add-category','tax','manage-income','copy-budget','purchase','merchant-category','account','spending-preview','expand','new-dashboard'])
def test_anonymous_popup_workflows_have_readable_text(owner,action):
    add_statement(owner)
    page=owner.imports_page
    owner.profile['wallet']=['bank-of-america-customized-cash-rewards'];owner.reload_cards()
    owner.overview_page.month.setDate(QDate(2026,10,1))
    owner.finance_store.set_target('2026-09','groceries',400)
    page.transactions.selectRow(0)
    handlers={'assumptions':owner.show_assumptions,'add-category':owner.add_category,'tax':owner.stocks_page.tax_settings,
        'manage-income':owner.overview_page.show_income,'copy-budget':owner.budget_page.copy_targets,
        'purchase':page.show_transaction,'merchant-category':lambda:page.choose_category('Edit remembered category','dining',True),
        'account':page.edit_account,'spending-preview':page.apply_spending,
        'expand':lambda:owner.stocks_page.expand_widget(owner.stocks_page.current_page()['widgets'][0]['id']),
        'new-dashboard':owner.stocks_page.new_dashboard}
    page.accounts.selectRow(0)
    failures=[];seen=[]
    def inspect():
        dialog=QApplication.activeModalWidget();seen.append(dialog)
        try:
            assert dialog is not None
            assert_text_fits(dialog)
        except Exception as exc:failures.append(exc)
        finally:
            if dialog:dialog.reject()
    timer=QTimer(owner);timer.setSingleShot(True);timer.timeout.connect(inspect);timer.start(0)
    try:handlers[action]()
    finally:timer.stop();timer.deleteLater()
    assert seen and not failures,failures
