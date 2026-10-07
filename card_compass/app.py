"""Native Qt interface. All user state is local; refresh runs off the UI thread."""
from __future__ import annotations

import html
import sys
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QCheckBox, QGridLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QTextBrowser,
    QListWidget, QListWidgetItem, QDialog, QDialogButtonBox, QFormLayout,
    QMessageBox, QProgressBar, QAbstractItemView, QScrollArea, QStackedWidget, QAbstractSpinBox,
)

from .catalog import CatalogStore, data_dir, load_profile, write_json, read_json, SOURCE
from .engine import DEFAULT_CATEGORIES, canonical, label, recommend, available_offers, point_value, rules_for
from .import_ui import ImportsPage
from .breakdown_ui import BreakdownPage
from .search import matches_card
from .discovery import GROUPS, discoverable, card_groups
from .artwork import ArtworkCache
from .source_audit import source_kind

from .ui import STYLE, Metric, CardArt, CardDelegate, CARD_ROLE, icon, text, surface, light_palette, ScrollSafeComboBox, ScrollSafeDoubleSpinBox, SearchableCategoryBox

SOURCE_REVIEWS = Path(__file__).parent / "data" / "source_reviews.json"


def money(value):
    return f"{'−' if value < 0 else ''}${abs(value):,.0f}"


def spin(value=0, maximum=100000, prefix="", suffix=""):
    widget = ScrollSafeDoubleSpinBox()
    widget.setRange(0, maximum)
    widget.setDecimals(2)
    widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    widget.setAlignment(Qt.AlignmentFlag.AlignLeft)
    widget.setMinimumHeight(40)
    widget.setPrefix(prefix)
    widget.setSuffix(suffix)
    widget.setValue(value)
    return widget


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.setAlternatingRowColors(False)
    widget.setShowGrid(False)
    widget.verticalHeader().setDefaultSectionSize(76)
    widget.setWordWrap(False)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    return widget


class RefreshThread(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, store):
        super().__init__()
        self.store = store

    def run(self):
        try:
            def progress(done, total):
                if self.isInterruptionRequested():
                    raise InterruptedError("Refresh cancelled")
                self.progress.emit(done, total)
            self.result.emit(self.store.refresh(progress, self.isInterruptionRequested))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class CardSettings(QDialog):
    def __init__(self, card, profile, parent):
        super().__init__(parent)
        self.setWindowTitle(f"Settings · {card['name']}")
        self.resize(530, 500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Point values and category selections change your estimates."))
        self.value = spin(point_value(card, profile), 20, suffix=" cents / point")
        layout.addWidget(self.value)
        settings = profile.get("card_settings", {}).get(card["id"], {})
        self.active = QCheckBox("I have activated the categories that require activation")
        self.active.setChecked(settings.get("activated", False))
        needs_activation = any(
            b.get('bonusCategoryDetail', {}).get('requiresActivation')
            for b in card.get('benefits', [])
        ) or any(r.get('requiresActivation') for r in card.get('earningRates', []))
        if card['id'] == 'bank-of-america-customized-cash-rewards':
            needs_activation = False
        self.active.setVisible(bool(needs_activation))
        layout.addWidget(self.active)
        self.choices = {}
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        inner = QVBoxLayout(content)
        for benefit in card.get("benefits", []):
            selection = benefit.get("bonusCategoryDetail", {}).get("categorySelection")
            if not selection or selection.get("mode") == "issuer_rotating":
                continue
            count = selection.get("selectCount", 1)
            caption = QLabel(f"{benefit['name']} · choose up to {count}")
            caption.setWordWrap(True)
            inner.addWidget(caption)
            if selection.get("mode") == "automatic_top_category":
                note = QLabel("Choose the categories that will have the highest spending on this card each cycle.")
                note.setWordWrap(True)
                inner.addWidget(note)
            if count == 1:
                options = SearchableCategoryBox()
                options.addItem('Select your current rewards category…', None)
                for category in selection.get('options', []):
                    options.addItem(label(category), category)
                selected = settings.get('selections', {}).get(benefit['id'], [])
                if selected:
                    options.setCurrentIndex(max(0, options.findData(selected[0])))
                inner.addWidget(options)
                self.choices[benefit['id']] = (options, count)
                continue
            options = QListWidget()
            for category in selection.get("options", []):
                item = QListWidgetItem(label(category))
                item.setData(Qt.ItemDataRole.UserRole, category)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if category in settings.get("selections", {}).get(benefit["id"], []) else Qt.CheckState.Unchecked)
                options.addItem(item)
            search = QLineEdit()
            search.setPlaceholderText('Search these categories…')
            search.textChanged.connect(lambda query, listing=options: [
                listing.item(i).setHidden(query.casefold() not in listing.item(i).text().casefold())
                for i in range(listing.count())
            ])
            inner.addWidget(search)
            inner.addWidget(options)
            self.choices[benefit["id"]] = (options, count)
        inner.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.error = text('', 'muted', True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate(self):
        for options, count in self.choices.values():
            if isinstance(options, SearchableCategoryBox):
                if not options.currentData():
                    self.error.setText('Choose your current rewards category before saving.')
                    return
                continue
            if sum(options.item(i).checkState() == Qt.CheckState.Checked for i in range(options.count())) > count:
                QMessageBox.information(self, "Too many categories", f"Choose at most {count} categories for each benefit.")
                return
        self.accept()

    def settings(self):
        selections = {}
        for ident, (options, _) in self.choices.items():
            if isinstance(options, SearchableCategoryBox):
                value = options.currentData()
                selections[ident] = [value] if value else []
            else:
                selections[ident] = [options.item(i).data(Qt.ItemDataRole.UserRole) for i in range(options.count())
                                    if options.item(i).checkState() == Qt.CheckState.Checked]
        return {"activated": self.active.isChecked(), "selections": selections}


class CustomCard(QDialog):
    def __init__(self, parent, categories):
        super().__init__(parent)
        self.setWindowTitle("Add an existing card manually")
        self.resize(500, 550)
        layout = QVBoxLayout(self)
        hint = QLabel("Enter cash-equivalent rates for a card missing from the catalog. These are your own assumptions; no offers are fetched for custom cards.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.name = QLineEdit()
        self.base = spin(1, 100, suffix=" %")
        self.fee = spin(0, 10000, prefix="$")
        form.addRow("Card name", self.name)
        form.addRow("Base rewards", self.base)
        form.addRow("Annual fee", self.fee)
        layout.addLayout(form)
        self.rates = {}
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        rates = QFormLayout(content)
        for category in categories:
            if category == "everything_else":
                continue
            entry = spin(0, 100, suffix=" %")
            entry.setSpecialValueText("Use base rate")
            rates.addRow(label(category), entry)
            self.rates[category] = entry
        scroll.setWidget(content)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate(self):
        if self.name.text().strip():
            self.accept()

    def card(self):
        rules = [{"multiplier": self.base.value(), "rewardUnit": "cashback_percent", "eligibleCategories": ["everything_else"]}]
        rules += [{"multiplier": w.value(), "rewardUnit": "cashback_percent", "eligibleCategories": [c]}
                  for c, w in self.rates.items() if w.value() > 0]
        return {"id": f"custom-{uuid.uuid4().hex}", "name": self.name.text().strip(), "issuerId": "custom",
                "variant": "personal", "status": "custom", "annualFee": {"amount": self.fee.value()},
                "earningRates": rules, "benefits": [], "offers": []}


class MainWindow(QMainWindow):
    def __init__(self, directory: Path | None = None, auto_refresh=True):
        super().__init__()
        self.setPalette(light_palette())
        self.setWindowTitle("Card Compass")
        self.resize(1380, 900)
        self.setMinimumSize(1040, 720)
        self.directory = directory or data_dir()
        self.store = CatalogStore(self.directory)
        self.profile = load_profile(self.directory)
        self.catalog = self.store.load()
        self.worker = None
        self.closing = False
        self.current_card = None
        self.artwork = ArtworkCache(self.directory, self, network_enabled=auto_refresh)
        self.build_ui()
        self.reload_cards()
        self.update_freshness()
        self.recalculate()
        if auto_refresh:
            QTimer.singleShot(0, self.refresh)

    def build_ui(self):
        self.setStyleSheet(STYLE)
        root = QWidget()
        root.setObjectName('canvas')
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setMinimumWidth(200)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 28, 16, 24)
        side.setSpacing(8)
        brand = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(icon('compass', '#c7e5c4', 32).pixmap(32, 32))
        brand.addWidget(mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        brand_text.addWidget(text('Card Compass', 'brand'))
        brand_text.addWidget(text('MAKE ROOM FOR BETTER.', 'brandTag'))
        brand.addLayout(brand_text)
        side.addLayout(brand)
        side.addSpacing(34)
        side.addWidget(text('YOUR WORKSPACE', 'navSection'))
        side.addSpacing(8)
        self.navigation = {}
        for key, caption, glyph in [('explore', 'Dashboard', 'explore'), ('matches', 'Your matches', 'matches'), ('wallet', 'My wallet', 'wallet'),
                                    ('spending', 'Spending', 'spending'), ('imports', 'Statements', 'statement'),
                                    ('breakdown', 'Breakdown', 'spending'), ('coverage', 'Coverage', 'coverage')]:
            button = QPushButton(caption)
            button.setObjectName('nav')
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setIcon(icon(glyph))
            button.setIconSize(QSize(19, 19))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, page=key: self.navigate(page))
            side.addWidget(button)
            self.navigation[key] = button
        side.addStretch()
        prefs = QPushButton('Preferences')
        prefs.setObjectName('nav')
        prefs.setCheckable(True)
        prefs.setAutoExclusive(True)
        prefs.setIcon(icon('settings'))
        prefs.clicked.connect(lambda: self.navigate('preferences'))
        self.navigation['preferences'] = prefs
        side.addWidget(prefs)
        side.addSpacing(20)
        side.addWidget(text('CARD DATA', 'navSection'))
        self.freshness = text('', 'sideStatus', True)
        self.freshness.setMinimumHeight(40)
        side.addWidget(self.freshness)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        side.addWidget(self.progress)
        self.refresh_button = QPushButton('Refresh data')
        self.refresh_button.setObjectName('sideRefresh')
        self.refresh_button.setIcon(icon('refresh', '#b4cdc4', 16))
        self.refresh_button.clicked.connect(self.refresh)
        side.addWidget(self.refresh_button)
        side.addSpacing(18)
        side.addWidget(text('LOCAL PROFILE', 'navSection'))
        side.addWidget(text('Saved on this device', 'sideStatus'))
        side_scroll = QScrollArea()
        side_scroll.setFixedWidth(220)
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_scroll.setWidget(sidebar)
        shell.addWidget(side_scroll)

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(30, 26, 30, 16)
        workspace_layout.setSpacing(18)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(6)
        self.page_title = text('Your next great card.', 'pageTitle')
        self.page_subtitle = text('More rewards for the way you already spend.', 'muted')
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        heading.addLayout(titles, 1)
        self.tune_button = QPushButton('Tune your matches')
        self.tune_button.setIcon(icon('settings', '#236347', 17))
        self.tune_button.clicked.connect(lambda: self.navigate('preferences'))
        heading.addWidget(self.tune_button)
        workspace_layout.addLayout(heading)
        self.pages = QStackedWidget()
        self.page_indexes = {}
        workspace_layout.addWidget(self.pages, 1)
        footer = QHBoxLayout()
        footer.addWidget(text('US cards  ·  Estimates assume purchases are paid in full.', 'muted'))
        footer.addStretch()
        footer.addWidget(text('Your financial details stay on this device.', 'muted'))
        workspace_layout.addLayout(footer)
        shell.addWidget(workspace, 1)

        def page(key):
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(16)
            self.page_indexes[key] = self.pages.addWidget(content)
            return layout

        matches = page('matches')
        metrics = QHBoxLayout()
        metrics.setSpacing(14)
        self.monthly_metric = Metric('Monthly spending', 'Across your spending categories')
        self.rewards_metric = Metric('Current annual rewards', 'Estimated from your existing wallet')
        self.gaps_metric = Metric('Categories to improve', 'Below your coverage target')
        for metric in (self.monthly_metric, self.rewards_metric, self.gaps_metric):
            metrics.addWidget(metric, 1)
        matches.addLayout(metrics)
        self.hero = surface('hero')
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(20, 18, 20, 18)
        hero_layout.setSpacing(24)
        self.hero_art = CardArt(width=210, height=128, artwork=self.artwork)
        hero_layout.addWidget(self.hero_art)
        hero_info = QVBoxLayout()
        hero_info.setSpacing(6)
        hero_info.addWidget(text('YOUR STRONGEST MATCH', 'eyebrow'))
        self.hero_name = text('', 'heroTitle', True)
        self.hero_issuer = text('', 'muted', True)
        hero_info.addWidget(self.hero_name)
        hero_info.addWidget(self.hero_issuer)
        self.hero_reason = text('', 'muted', True)
        hero_info.addWidget(self.hero_reason)
        hero_info.addStretch()
        hero_layout.addLayout(hero_info, 1)
        value_layout = QVBoxLayout()
        value_layout.setSpacing(6)
        self.hero_value = text('', 'heroValue')
        value_layout.addWidget(self.hero_value)
        self.hero_value_caption = text('extra per year', 'muted')
        value_layout.addWidget(self.hero_value_caption)
        self.hero_button = QPushButton('View this card  →')
        self.hero_button.setObjectName('primary')
        self.hero_button.clicked.connect(lambda: self.show_card(self.matches[0]['card']) if self.matches else None)
        value_layout.addWidget(self.hero_button)
        hero_layout.addLayout(value_layout)
        matches.addWidget(self.hero)
        self.setup = surface('empty')
        setup_layout = QHBoxLayout(self.setup)
        setup_layout.setContentsMargins(24, 24, 24, 24)
        setup_layout.setSpacing(26)
        setup_layout.addWidget(CardArt(width=210, height=128))
        setup_copy = QVBoxLayout()
        setup_copy.addWidget(text('A better match starts with you.', 'heroTitle'))
        setup_copy.addWidget(text('Add the cards you own and your monthly spending.\nWe’ll find the rewards your wallet is missing.', 'muted', True))
        setup_actions = QHBoxLayout()
        add = QPushButton('Add your cards')
        add.setObjectName('primary')
        add.clicked.connect(lambda: self.navigate('explore'))
        spend = QPushButton('Set your spending')
        spend.clicked.connect(lambda: self.navigate('spending'))
        setup_actions.addWidget(add)
        setup_actions.addWidget(spend)
        setup_actions.addStretch()
        setup_copy.addLayout(setup_actions)
        setup_layout.addLayout(setup_copy, 1)
        matches.addWidget(self.setup)
        results_heading = QHBoxLayout()
        self.results_heading = text('Recommended for you', 'sectionTitle')
        self.result_count = text('', 'pill')
        results_heading.addWidget(self.results_heading)
        results_heading.addWidget(self.result_count)
        results_heading.addStretch()
        results_heading.addWidget(text('SORT BY', 'eyebrow'))
        self.sort = ScrollSafeComboBox()
        self.sort.addItems(['Ongoing value', 'First-year value'])
        self.sort.setCurrentText(self.profile.get('sort', 'Ongoing value'))
        self.sort.setMinimumWidth(165)
        results_heading.addWidget(self.sort)
        matches.addLayout(results_heading)
        self.recommendations = table(['CARD', 'ANNUAL FEE', 'ONGOING VALUE', 'YEAR ONE'])
        self.recommendations.setItemDelegateForColumn(0, CardDelegate(self.recommendations, artwork=self.artwork))
        self.recommendations.setColumnWidth(1, 130)
        self.recommendations.setColumnWidth(2, 160)
        self.recommendations.setColumnWidth(3, 140)
        self.recommendations.itemSelectionChanged.connect(self.select_recommendation)
        self.recommendations.cellClicked.connect(lambda row, col: self.select_recommendation())
        self.recommendations.itemActivated.connect(lambda item: self.select_recommendation())
        matches.addWidget(self.recommendations, 1)
        self.match_empty = text('Enter monthly spending to get personalized recommendations.', 'muted', True)
        self.match_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        matches.addWidget(self.match_empty, 1)
        self.notes = text('', 'muted', True)
        matches.addWidget(self.notes)

        wallet_layout = page('wallet')
        wallet_header = QHBoxLayout()
        self.wallet_count = text('Your cards', 'sectionTitle')
        wallet_header.addWidget(self.wallet_count, 1)
        custom = QPushButton('Add manually')
        custom.clicked.connect(self.add_custom_card)
        wallet_header.addWidget(custom)
        add = QPushButton('Add a card')
        add.setObjectName('primary')
        add.setIcon(icon('plus', '#ffffff', 16))
        add.clicked.connect(lambda: self.navigate('explore'))
        wallet_header.addWidget(add)
        wallet_layout.addLayout(wallet_header)
        self.wallet_empty = surface('empty')
        empty = QVBoxLayout(self.wallet_empty)
        empty.setContentsMargins(36, 50, 36, 50)
        empty.addWidget(text('Your wallet, all in one place.', 'heroTitle'))
        empty.addWidget(text('Add your current cards to see where you’re covered and where a new card can help.', 'muted', True))
        empty.addSpacing(12)
        empty.addWidget(CardArt())
        wallet_layout.addWidget(self.wallet_empty)
        self.wallet = QListWidget()
        self.wallet.setItemDelegate(CardDelegate(self.wallet, artwork=self.artwork))
        self.wallet.setSpacing(3)
        self.wallet.itemClicked.connect(self.select_wallet)
        self.wallet.itemActivated.connect(self.select_wallet)
        self.wallet.currentItemChanged.connect(lambda current, previous: self.remove_button.setEnabled(current is not None))
        wallet_layout.addWidget(self.wallet, 1)
        wallet_footer = QHBoxLayout()
        wallet_footer.addWidget(text('Select a card to review its benefits or change its reward settings.', 'muted'), 1)
        self.remove_button = QPushButton('Remove selected card')
        self.remove_button.clicked.connect(self.remove_wallet_card)
        wallet_footer.addWidget(self.remove_button)
        wallet_layout.addLayout(wallet_footer)

        spending_layout = page('spending')
        spend_intro = surface()
        intro = QHBoxLayout(spend_intro)
        intro.setContentsMargins(20, 18, 20, 18)
        intro_copy = QVBoxLayout()
        intro_copy.addWidget(text('Where does your money go?', 'sectionTitle'))
        intro_copy.addWidget(text('Use separate buckets for monthly purchases. Auto uses your wallet’s rates; enter 0% to mark a gap.', 'muted', True))
        intro.addLayout(intro_copy, 1)
        self.spend_total = text('', 'statValue')
        intro.addWidget(self.spend_total)
        spending_layout.addWidget(spend_intro)
        self.spending = table(['SPENDING CATEGORY', 'MONTHLY SPEND', 'CURRENT COVERAGE'])
        self.spending.setColumnWidth(1, 230)
        self.spending.setColumnWidth(2, 230)
        self.spend_widgets = {}
        self.coverage_widgets = {}
        categories = list(DEFAULT_CATEGORIES)
        categories += sorted(set(self.profile.get('spend', {})) - set(categories))
        for category in categories:
            self.add_spend_row(category)
        self.spending.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        spending_layout.addWidget(self.spending, 1)
        spending_footer = QHBoxLayout()
        spending_footer.addWidget(text('Changes save automatically. Move portal and merchant purchases out of other buckets.', 'muted', True), 1)
        add_category = QPushButton('Add a category')
        add_category.setIcon(icon('plus', '#236347', 16))
        add_category.clicked.connect(self.add_category)
        spending_footer.addWidget(add_category)
        spending_layout.addLayout(spending_footer)

        coverage_layout = page('coverage')
        self.coverage_summary = text('', 'sectionTitle')
        coverage_layout.addWidget(self.coverage_summary)
        coverage_layout.addWidget(text('Highest spending first. Rates account for caps and the best way to use your existing cards.', 'muted', True))
        self.coverage = table(['CATEGORY', 'MONTHLY SPEND', 'EFFECTIVE RATE', 'COVERAGE'])
        self.coverage.setColumnWidth(1, 170)
        self.coverage.setColumnWidth(2, 170)
        self.coverage.setColumnWidth(3, 140)
        coverage_layout.addWidget(self.coverage, 1)
        coverage_actions = QHBoxLayout()
        edit = QPushButton('Edit spending')
        edit.clicked.connect(lambda: self.navigate('spending'))
        target = QPushButton('Change coverage target')
        target.clicked.connect(lambda: self.navigate('preferences'))
        coverage_actions.addWidget(edit)
        coverage_actions.addWidget(target)
        coverage_actions.addStretch()
        coverage_layout.addLayout(coverage_actions)

        explore_layout = page('explore')
        self.discovery_group = None
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.addAction(icon('explore', '#69857a', 19), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setPlaceholderText('Search cards, issuers, benefits, or categories…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_catalog)
        search_row.addWidget(self.search, 1)
        self.issuer_filter = ScrollSafeComboBox()
        self.issuer_filter.addItem('All issuers', '')
        self.issuer_filter.setMinimumWidth(200)
        self.issuer_filter.currentIndexChanged.connect(self.filter_catalog)
        search_row.addWidget(self.issuer_filter)
        explore_layout.addLayout(search_row)
        self.dashboard_scroll = QScrollArea()
        self.dashboard_scroll.setWidgetResizable(True)
        dashboard = QWidget()
        dashboard.setObjectName('canvas')
        dashboard_layout = QVBoxLayout(dashboard)
        dashboard_layout.setContentsMargins(0, 0, 8, 0)
        dashboard_layout.setSpacing(20)
        dashboard_header = QHBoxLayout()
        dashboard_header.addWidget(text('What kind of rewards are you looking for?', 'sectionTitle'), 1)
        self.view_all_cards = QPushButton('View all active cards')
        self.view_all_cards.setObjectName('quiet')
        self.view_all_cards.clicked.connect(lambda: self.choose_discovery_group('all'))
        dashboard_header.addWidget(self.view_all_cards)
        dashboard_layout.addLayout(dashboard_header)
        grid = QGridLayout()
        grid.setSpacing(16)
        self.category_tiles = {}
        self.category_counts = {}
        for index, (key, title, description, glyph) in enumerate(GROUPS):
            tile = QPushButton()
            tile.setObjectName('categoryTile')
            tile.setAccessibleName('Browse ' + title + ' cards')
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            tile.setMinimumHeight(166)
            inner = QVBoxLayout(tile)
            inner.setContentsMargins(20, 18, 20, 18)
            inner.setSpacing(8)
            mark = QLabel()
            mark.setPixmap(icon(glyph, '#236347', 25).pixmap(25, 25))
            inner.addWidget(mark)
            inner.addWidget(text(title, 'sectionTitle'))
            inner.addWidget(text(description, 'muted', True), 1)
            count = text('', 'pill')
            inner.addWidget(count, 0, Qt.AlignmentFlag.AlignLeft)
            for child in tile.findChildren(QLabel):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            tile.clicked.connect(lambda checked=False, group=key: self.choose_discovery_group(group))
            self.category_tiles[key] = tile
            self.category_counts[key] = count
            grid.addWidget(tile, index // 3, index % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        dashboard_layout.addLayout(grid)
        dashboard_layout.addWidget(text('Cards can appear in more than one category. Search above to find any active card.', 'muted', True))
        dashboard_layout.addStretch()
        self.dashboard_scroll.setWidget(dashboard)
        explore_layout.addWidget(self.dashboard_scroll, 1)
        self.browse_header = QWidget()
        browse_header = QHBoxLayout(self.browse_header)
        browse_header.setContentsMargins(0, 0, 0, 0)
        self.back_to_dashboard = QPushButton('← Dashboard')
        self.back_to_dashboard.clicked.connect(self.reset_discovery)
        browse_header.addWidget(self.back_to_dashboard)
        self.explore_count = text('', 'muted')
        browse_header.addWidget(self.explore_count, 1)
        explore_layout.addWidget(self.browse_header)
        self.catalog_table = table(['CARD', 'ANNUAL FEE', 'AVAILABILITY'])
        self.catalog_table.setItemDelegateForColumn(0, CardDelegate(self.catalog_table, artwork=self.artwork))
        self.catalog_table.setColumnWidth(1, 180)
        self.catalog_table.setColumnWidth(2, 180)
        self.catalog_table.itemSelectionChanged.connect(self.select_catalog)
        self.catalog_table.cellClicked.connect(lambda row, col: self.select_catalog())
        self.catalog_table.itemActivated.connect(lambda item: self.select_catalog())
        explore_layout.addWidget(self.catalog_table, 1)
        self.search_empty = text('No cards found. Try a different name, benefit, or issuer.', 'muted')
        self.search_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.search_empty.hide()
        explore_layout.addWidget(self.search_empty)

        preferences_page = page('preferences')
        preferences_scroll = QScrollArea()
        preferences_scroll.setWidgetResizable(True)
        preferences_body = QWidget()
        preferences_body.setObjectName('canvas')
        preferences = QVBoxLayout(preferences_body)
        preferences.setContentsMargins(0, 0, 8, 0)
        preferences.setSpacing(16)
        preferences_scroll.setWidget(preferences_body)
        preferences_page.addWidget(preferences_scroll)
        settings_surface = surface()
        settings_layout = QVBoxLayout(settings_surface)
        settings_layout.setContentsMargins(28, 24, 28, 24)
        settings_layout.setSpacing(16)
        settings_layout.addWidget(text('Make the estimates yours.', 'sectionTitle'))
        settings_layout.addWidget(text('These preferences apply to all matches. Override individual point values and category selections in a card’s settings.', 'muted', True))
        settings_form = QFormLayout()
        settings_form.setVerticalSpacing(18)
        settings_form.setHorizontalSpacing(40)
        self.max_fee = spin(self.profile.get('max_fee', 1000), 10000, prefix='$ ')
        self.points = spin(self.profile.get('point_value', 1), 20, suffix=' ¢ / point')
        self.target = spin(self.profile.get('target_rate', 2), 100, suffix=' %')
        for widget in (self.max_fee, self.points, self.target):
            widget.setMaximumWidth(250)
        settings_form.addRow('Maximum annual fee', self.max_fee)
        settings_form.addRow('Default point / mile value', self.points)
        settings_form.addRow('Category coverage target', self.target)
        settings_layout.addLayout(settings_form)
        settings_layout.addWidget(text('Point currencies differ. A hotel point and an airline mile may have very different values.', 'muted', True))
        self.business = QCheckBox('Include business cards in recommendations')
        self.business.setChecked(self.profile.get('include_business', False))
        self.bonus = QCheckBox('Count public signup bonuses in year one — I expect to qualify')
        self.bonus.setChecked(self.profile.get('eligible_bonus', False))
        settings_layout.addWidget(self.business)
        settings_layout.addWidget(self.bonus)
        settings_layout.addWidget(text('Signup bonuses count only when your spending pace meets the published requirement. Alternative offers are compared separately.', 'muted', True))
        preferences.addWidget(settings_surface)
        explanation = surface()
        explanation_layout = QVBoxLayout(explanation)
        explanation_layout.setContentsMargins(28, 24, 28, 24)
        explanation_layout.addWidget(text('How to read your matches', 'sectionTitle'))
        explanation_layout.addWidget(text('Ongoing value is the extra annual rewards a card adds to your wallet, after its fee. Year one also includes a first-year fee waiver and any counted signup bonus. Credits, interest, taxes, and approval odds are excluded.', 'muted', True))
        explanation_layout.addSpacing(10)
        explanation_layout.addWidget(text('Card data refreshes on startup. Download time and issuer review dates are different; check the published source before applying.', 'muted', True))
        preferences.addWidget(explanation)
        preferences.addStretch()
        self.imports_page = ImportsPage(self)
        self.page_indexes['imports'] = self.pages.addWidget(self.imports_page)
        self.breakdown_page = BreakdownPage(self)
        self.page_indexes['breakdown'] = self.pages.addWidget(self.breakdown_page)
        self.build_details()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.recalculate)
        for widget in (self.points, self.target, self.max_fee):
            widget.valueChanged.connect(self.schedule_update)
        self.sort.currentTextChanged.connect(self.schedule_update)
        self.business.toggled.connect(self.schedule_update)
        self.bonus.toggled.connect(self.schedule_update)
        self.navigate('explore')

    def build_details(self):
        self.details_dialog = QDialog(self)
        self.details_dialog.setWindowTitle('Card details')
        self.details_dialog.resize(650, 800)
        self.details_dialog.setMinimumSize(540, 640)
        layout = QVBoxLayout(self.details_dialog)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        header = QHBoxLayout()
        header.addWidget(text('A closer look', 'sectionTitle'), 1)
        close = QPushButton()
        close.setIcon(icon('close', '#69857a', 18))
        close.setObjectName('quiet')
        close.setAccessibleName('Close card details')
        close.clicked.connect(self.details_dialog.close)
        header.addWidget(close)
        layout.addLayout(header)
        identity = QHBoxLayout()
        self.detail_art = CardArt(width=194, height=122, artwork=self.artwork)
        identity.addWidget(self.detail_art)
        info = QVBoxLayout()
        self.detail_name = text('', 'heroTitle', True)
        self.detail_issuer = text('', 'muted', True)
        self.detail_fee = text('', 'muted', True)
        info.addWidget(self.detail_name)
        info.addWidget(self.detail_issuer)
        info.addWidget(self.detail_fee)
        identity.addLayout(info, 1)
        layout.addLayout(identity)
        self.detail_tabs = QTabWidget()
        self.detail_browsers = {}
        for name in ('Overview', 'Offers', 'Benefits', 'Sources'):
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.document().setDefaultStyleSheet('body { color: #334e40; } h3 { font-size: 17px; color: #183a2d; margin-top: 20px; } p { line-height: 150%; margin: 10px 0 16px; } a { color: #236347; } li { margin: 7px 0; }')
            self.detail_browsers[name] = browser
            self.detail_tabs.addTab(browser, name)
        self.details = self.detail_browsers['Overview']
        self.offer_details = self.detail_browsers['Offers']
        layout.addWidget(self.detail_tabs, 1)
        actions = QHBoxLayout()
        self.settings_button = QPushButton('Card settings')
        self.settings_button.setIcon(icon('settings', '#236347', 16))
        self.settings_button.clicked.connect(self.edit_card_settings)
        actions.addWidget(self.settings_button)
        self.add_wallet = QPushButton('Add to wallet')
        self.add_wallet.setObjectName('primary')
        self.add_wallet.clicked.connect(self.add_wallet_card)
        actions.addWidget(self.add_wallet, 1)
        layout.addLayout(actions)
        self.add_wallet.setEnabled(False)
        self.settings_button.setEnabled(False)

    def navigate(self, key):
        titles = {
            'matches': ('Your next great card.', 'More rewards for the way you already spend.'),
            'wallet': ('Your wallet.', 'The cards you have. The benefits you should know.'),
            'spending': ('Make your spending count.', 'A little context goes a long way toward a better match.'),
            'imports': ('Teach your wallet once.', 'Import statements, remember merchants, and build your purchase history.'),
            'breakdown': ('Where your money goes.', 'Your largest spending categories and merchants, based on imported purchases.'),
            'coverage': ('Find the gaps.', 'See where your wallet works hard — and where it could do more.'),
            'explore': ('Find your next card.', 'Choose a reward category or search across active US credit cards.'),
            'preferences': ('Your rules. Your rewards.', 'Fine-tune the assumptions behind your recommendations.'),
        }
        self.active_page = key
        self.pages.setCurrentIndex(self.page_indexes[key])
        self.navigation[key].setChecked(True)
        self.page_title.setText(titles[key][0])
        self.page_subtitle.setText(titles[key][1])
        self.tune_button.setVisible(key == 'matches')
        if key == 'explore':
            self.search.setFocus()
        if key == 'breakdown':
            self.breakdown_page.reload()

    def add_spend_row(self, category):
        row = self.spending.rowCount()
        self.spending.insertRow(row)
        self.spending.setItem(row, 0, QTableWidgetItem(label(category)))
        amount = spin(self.profile.get("spend", {}).get(category, 0), prefix="$ ")
        covered = spin(0, 100, suffix=" %")
        covered.setRange(-1, 100)
        covered.setSpecialValueText("Auto")
        covered.setValue(self.profile.get("coverage", {}).get(category, -1))
        amount.valueChanged.connect(self.schedule_update)
        covered.valueChanged.connect(self.schedule_update)
        self.spending.setCellWidget(row, 1, amount)
        self.spending.setCellWidget(row, 2, covered)
        self.spend_widgets[category] = amount
        self.coverage_widgets[category] = covered
        self.spending.setRowHeight(row, 64)

    def schedule_update(self, *args):
        if hasattr(self, "timer"):
            self.timer.start()

    def save_profile(self):
        self.profile.update({
            "spend": {k: w.value() for k, w in self.spend_widgets.items()},
            "coverage": {k: w.value() for k, w in self.coverage_widgets.items() if w.value() >= 0},
            "sort": self.sort.currentText(), "max_fee": self.max_fee.value(), "point_value": self.points.value(),
            "target_rate": self.target.value(), "include_business": self.business.isChecked(), "eligible_bonus": self.bonus.isChecked(),
        })
        try:
            write_json(self.directory / "profile.json", self.profile)
        except OSError as exc:
            self.statusBar().showMessage(f"Could not save profile: {exc}")

    def reload_cards(self):
        self.cards = self.catalog['cards'] + self.profile.get('custom_cards', [])
        self.by_id = {c['id']: c for c in self.cards}
        self.wallet.clear()
        for ident in self.profile.get('wallet', []):
            card = self.by_id.get(ident)
            item = QListWidgetItem(card['name'] if card else f'Unavailable card · {ident}')
            item.setData(Qt.ItemDataRole.UserRole, ident)
            item.setData(CARD_ROLE, card)
            item.setSizeHint(QSize(0, 100))
            self.wallet.addItem(item)
        count = self.wallet.count()
        self.wallet_count.setText(f'{count} card' + ('' if count == 1 else 's') + ' in your wallet')
        self.wallet_empty.setVisible(not count)
        self.remove_button.setEnabled(self.wallet.currentItem() is not None)
        current = self.issuer_filter.currentData()
        self.issuer_filter.blockSignals(True)
        self.issuer_filter.clear()
        self.issuer_filter.addItem('All issuers', '')
        for issuer in sorted({c['issuerId'] for c in self.cards if discoverable(c)}):
            self.issuer_filter.addItem(issuer.replace('-', ' ').title(), issuer)
        index = self.issuer_filter.findData(current)
        self.issuer_filter.setCurrentIndex(max(0, index))
        self.issuer_filter.blockSignals(False)
        for key, count_label in self.category_counts.items():
            count = sum(key in card_groups(c) for c in self.cards)
            count_label.setText(f'{count} card' + ('' if count == 1 else 's'))
        self.filter_catalog()
        self.imports_page.reload()

    def recalculate(self):
        self.save_profile()
        self.matches, baseline, warnings = recommend(self.cards, self.profile)
        monthly = sum(self.profile['spend'].values())
        self.monthly_metric.value.setText(money(monthly))
        self.rewards_metric.value.setText(money(baseline.total))
        self.spend_total.setText(money(monthly) + ' / month')
        self.recommendations.blockSignals(True)
        self.recommendations.setRowCount(len(self.matches))
        for row, match in enumerate(self.matches):
            c = match['card']
            values = [c['name'], money(c['annualFee']['amount']), money(match['ongoing']), money(match['first_year'])]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, c['id'])
                if col == 0:
                    item.setData(CARD_ROLE, c)
                item.setToolTip('\n'.join(match['warnings']) or 'Additional value compared with your existing wallet')
                if col >= 2:
                    font = QFont(self.font())
                    font.setPixelSize(17)
                    font.setWeight(QFont.Weight.DemiBold)
                    item.setFont(font)
                    item.setForeground(QColor('#236347' if (match['ongoing'] if col == 2 else match['first_year']) > 0 else '#52665a'))
                self.recommendations.setItem(row, col, item)
            self.recommendations.setRowHeight(row, 88)
        self.recommendations.blockSignals(False)
        categories = sorted(self.profile['spend'], key=lambda k: -self.profile['spend'][k])
        self.coverage.setRowCount(len(categories))
        gaps = 0
        for row, cat in enumerate(categories):
            spend = self.profile['spend'][cat]
            reward = baseline.category_rewards.get(cat, 0)
            effective = reward / (spend * 12) * 100 if spend else 0
            state = 'No spend' if not spend else 'Covered' if effective >= self.target.value() else 'Gap'
            gaps += state == 'Gap'
            values = [label(cat), money(spend), f'{effective:.2f}%' if spend else '—', state]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                providers = baseline.category_cards.get(cat, [])
                item.setToolTip(' / '.join('Manual coverage' if i == 'manual' else self.by_id[i]['name'] for i in providers))
                if col == 3:
                    item.setForeground(QColor('#805014' if state == 'Gap' else '#236347' if state == 'Covered' else '#52665a'))
                self.coverage.setItem(row, col, item)
            self.coverage.setRowHeight(row, 62)
        self.gaps_metric.value.setText(str(gaps))
        self.coverage_summary.setText(f'{gaps} categor' + ('y' if gaps == 1 else 'ies') + f' below your {self.target.value():g}% target')
        self.result_count.setText(f'{len(self.matches)} matches')
        self.result_count.setVisible(bool(monthly))
        self.setup.setVisible(not monthly)
        self.hero.setVisible(bool(monthly and self.matches))
        self.recommendations.setVisible(bool(monthly and self.matches))
        self.match_empty.setVisible(not monthly or not self.matches)
        self.match_empty.setText('Your recommendations will appear here once you add spending.' if not monthly else 'No cards fit your preferences. Try raising your annual fee limit or including business cards.')
        self.sort.setEnabled(bool(monthly))
        if self.matches:
            best = self.matches[0]
            card = best['card']
            self.hero_art.set_card(card)
            self.hero_name.setText(card['name'])
            self.hero_issuer.setText(card['issuerId'].replace('-', ' ').title() + f"  ·  {money(card['annualFee']['amount'])} annual fee")
            value = best['first_year'] if self.sort.currentText() == 'First-year value' else best['ongoing']
            self.hero_value.setText(money(value))
            self.hero_value_caption.setText('extra in year one' if self.sort.currentText() == 'First-year value' else 'extra per year')
            gains = sorted(best['category_gains'].items(), key=lambda item: -item[1])
            top = [label(cat).split(' (')[0].lower() for cat, gain in gains if gain > .01][:2]
            self.hero_reason.setText('Adds value in ' + ' and '.join(top) + '.' if top else 'Compare its benefits and offer against your wallet.')
        self.notes.setText('Values show additional rewards after the new card’s annual fee.' + (f' {len(warnings)} wallet assumptions need review in Card settings.' if warnings else '') if monthly else 'Start with your wallet or spending. You can change either at any time.')
        if self.current_card and self.current_card['id'] in self.by_id:
            self.show_card(self.by_id[self.current_card['id']], reveal=False)
        elif self.current_card:
            self.current_card = None
            self.details_dialog.close()

    def choose_discovery_group(self, group):
        self.discovery_group = group
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.issuer_filter.blockSignals(True)
        self.issuer_filter.setCurrentIndex(0)
        self.issuer_filter.blockSignals(False)
        self.filter_catalog()

    def reset_discovery(self):
        self.choose_discovery_group(None)

    def filter_catalog(self, *args):
        query = self.search.text().strip().lower()
        issuer = self.issuer_filter.currentData() or ''
        # A typed search always spans reward categories, even after opening a tile.
        if query:
            self.discovery_group = None
        browsing = bool(query or issuer or self.discovery_group)
        self.dashboard_scroll.setVisible(not browsing)
        self.browse_header.setVisible(browsing)
        self.catalog_table.setVisible(browsing)
        self.explored = [c for c in self.cards if browsing and discoverable(c)
                         and (not issuer or c['issuerId'] == issuer) and matches_card(c, query)
                         and (not self.discovery_group or self.discovery_group == 'all' or self.discovery_group in card_groups(c))]
        self.catalog_table.blockSignals(True)
        self.catalog_table.setRowCount(len(self.explored))
        for row, card in enumerate(self.explored):
            status = 'In your wallet' if card['id'] in self.profile['wallet'] else card['status'].title()
            for col, value in enumerate([card['name'], money(card['annualFee']['amount']), status]):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, card['id'])
                if col == 0:
                    item.setData(CARD_ROLE, card)
                self.catalog_table.setItem(row, col, item)
            self.catalog_table.setRowHeight(row, 84)
        self.catalog_table.blockSignals(False)
        group_title = dict((key, title) for key, title, _, _ in GROUPS).get(self.discovery_group, 'All active cards')
        caption = 'Search results' if query else group_title
        self.explore_count.setText(f'{caption} · {len(self.explored)} cards')
        self.search_empty.setVisible(browsing and not self.explored)

    def select_recommendation(self):
        row = self.recommendations.currentRow()
        if 0 <= row < len(self.matches):
            self.show_card(self.matches[row]["card"])

    def select_catalog(self):
        row = self.catalog_table.currentRow()
        if 0 <= row < len(self.explored):
            self.show_card(self.explored[row])

    def show_card(self, card, reveal=True):
        new_card = self.current_card is None or self.current_card['id'] != card['id']
        self.current_card = card
        self.detail_art.set_card(card)
        self.detail_name.setText(card['name'])
        self.detail_issuer.setText(card['issuerId'].replace('-', ' ').title())
        self.detail_fee.setText(money(card['annualFee']['amount']) + ' annual fee' + (' · waived in year one' if card['annualFee'].get('waivedFirstYear') else ''))
        self.details_dialog.setWindowTitle(card['name'] + ' · Card Compass')
        esc = lambda value: html.escape(str(value))
        def source_links(urls):
            from urllib.parse import urlsplit
            return '<br>'.join(f'<a href="{esc(url)}">{esc(source_kind(url, card['issuerId']))} · {esc(urlsplit(url).hostname)}</a>' for url in urls if isinstance(url, str) and url.startswith('https://'))
        overview, offers_html, benefits_html, sources = [], [], [], []
        review = read_json(SOURCE_REVIEWS, {}).get(card['id'])
        if review:
            checked = '<h3>Issuer terms checked ' + esc(review['checkedOn']) + '</h3>'
            checked += '<ul>' + ''.join('<li>' + esc(note) + '</li>' for note in review['confirmed'] + review['limits']) + '</ul>'
            overview.append(checked)
            sources.append(checked + '<p>' + source_links(review['sources']) + '</p>')
        match = next((m for m in self.matches if m['card']['id'] == card['id']), None)
        if match and sum(self.profile['spend'].values()):
            overview.append(f"<h3>Your wallet + this card</h3><p><b>{money(match['ongoing'])}</b> additional value each year<br><b>{money(match['first_year'])}</b> additional value in year one</p>")
            overview.append(f"<p>Extra rewards before the new card’s fee: {money(match['gain'])}/year.<br>Signup value counted: {money(match['bonus'])}.</p>")
            gains = sorted(match['category_gains'].items(), key=lambda pair: -pair[1])
            overview.append('<h3>Where it helps</h3><p>' + '<br>'.join(f'{esc(label(c))} · {money(value)} change/year' for c, value in gains if abs(value) > .01) + '</p>')
        elif card['id'] in self.profile['wallet']:
            overview.append('<h3>Part of your wallet</h3><p>Its earning rates are included in your current coverage. Review Card settings to confirm activation and selected categories.</p>')
        else:
            overview.append('<h3>A closer look at your next card</h3><p>Add your spending to see how much this card could contribute to your wallet.</p>')
        overview.append('<h3>Earning rates</h3>')
        units = {'cashback_percent': '% cash back', 'points': '× points', 'miles': '× miles'}
        for rate in card.get('earningRates', []):
            multiplier = f"{rate.get('multiplier', 0):g}"
            categories = ', '.join(label(c) for c in rate.get('eligibleCategories', []))
            overview.append(f"<p><b>{esc(multiplier)}{esc(units.get(rate.get('rewardUnit'), ' rewards'))}</b> · {esc(categories)}<br>{esc(rate.get('terms', ''))}</p>")
            if rate.get('spendCap'):
                cap = rate['spendCap']
                period = cap.get('period', {})
                caption = 'Cashback cap' if card['id'] == 'comenity-aaa-daily-advantage' else 'Spend cap'
                overview.append(f"<p>{caption}: {money(cap['amount'])} per {esc(period.get('count', 1))} {esc(period.get('unit', 'unknown'))}. See terms for shared limits.</p>")
        if not card.get('earningRates'):
            overview.append('<p>Earning rates are not available in this source.</p>')
        _, warnings = rules_for(card, self.profile)
        if warnings:
            overview.append('<h3>Assumptions to review</h3><ul>' + ''.join(f'<li>{esc(w)}</li>' for w in warnings) + '</ul>')
        overview.append(f'<p>Estimated point / mile value: {point_value(card, self.profile):g}¢ each. Change it in Card settings.</p>')

        offers_html.append('<h3>Published signup offers</h3>')
        from .offers import reward_components, reward_text, limited_time
        issuer_check = card.get('issuerOfferCheck', {})
        if issuer_check:
            state = 'Checked against issuer' if issuer_check.get('status') == 'verified' else 'Current issuer offer unconfirmed · last available terms'
            offers_html.append(f"<p><b>{esc(state)}</b><br>Last successful check: {esc(issuer_check.get('checkedAt', 'never'))}<br>{source_links([issuer_check.get('sourceUrl', '')])}</p>")
        published = available_offers(card, self.profile)
        if not published:
            offers_html.append('<p>No unexpired published offers in this source.</p>')
        for offer in published:
            value = money(offer['value']) + ' estimated value' if offer['value'] is not None else 'Value unresolved'
            spend = money(offer['spendRequirement']) if offer.get('spendRequirement') is not None else 'Unknown spending requirement'
            reward = ' + '.join(reward_text(c) for c in reward_components(offer))
            if limited_time(offer):
                offers_html.append('<p><span style="background-color:#fff1c2; color:#594000;"><b>&nbsp; Limited-time offer &nbsp;</b></span></p>')
            offers_html.append(f"<h3>{esc(reward)}</h3><p>{value}<br>Spend {spend} in {esc(offer.get('timeframeDays', 'unknown'))} days · {esc(offer.get('offerType', 'unknown'))} offer</p>")
            for component in reward_components(offer):
                if component.get('terms'):
                    offers_html.append(f"<p>{esc(component['terms'])}</p>")
            if offer['excludedComponents']:
                excluded = ', '.join(reward_text(c) for c in offer['excludedComponents'])
                offers_html.append(f"<p>{esc(excluded)} is shown separately and excluded from estimated value and rankings.</p>")
            for perk in offer.get('additionalPerks', []):
                offers_html.append(f"<p>{esc(perk)}</p>")
            offers_html.append('<p>' + ('Your stated spending pace meets the requirement.' if offer['feasible'] else 'The requirement exceeds your spending pace or is unresolved.') + (' Included in year one.' if offer['counted'] else ' Not counted in year one.') + '</p>')
            if offer.get('terms'):
                offers_html.append(f"<p>{esc(offer['terms'])}</p>")
            if offer.get('expirationDate'):
                offers_html.append(f"<p>Expires: {esc(offer['expirationDate'])}</p>")
            elif limited_time(offer):
                offers_html.append('<p>No offer deadline is published in this record. The promotion may change.</p>')
        offer_meta = card.get('offerSourceMeta', {})
        offers_html.append(f"<p>Offers reviewed by source: {esc(offer_meta.get('lastReviewedDate', 'unknown'))}<br>{source_links(offer_meta.get('sourceUrls', []))}</p>")
        offers_html.append('<p>Offers may vary by applicant. Confirm eligibility and current terms with the issuer.</p>')
        benefits_html.append('<h3>Benefits & conditions</h3>')
        if not card.get('benefits'):
            benefits_html.append('<p>No benefits recorded in this source.</p>')
        for benefit in card.get('benefits', []):
            benefits_html.append(f"<h3>{esc(benefit['name'])}</h3><p>{esc(benefit.get('description', ''))}</p>")
            if benefit.get('terms'):
                benefits_html.append(f"<p>{esc(benefit['terms'])}</p>")
            detail = benefit.get('bonusCategoryDetail')
            if detail:
                selection = detail.get('categorySelection', {})
                cats = ', '.join(label(c) for c in detail.get('eligibleCategories', selection.get('options', [])))
                benefits_html.append(f"<p>{esc(detail.get('multiplier'))}{esc(units.get(detail.get('rewardUnit'), ' rewards'))} · {esc(selection.get('mode', 'fixed categories').replace('_', ' '))}<br>{esc(cats)}<br>Cycle: {esc(selection.get('currentCycle', 'standing terms'))}</p>")
            benefits_html.append(f"<p>Verification date reported by community source: {esc(benefit.get('lastVerified', 'unknown'))}<br>{source_links([benefit.get('sourceUrl', '')])}</p>")
        meta = card.get('sourceMeta', {})
        sources.append(f"<h3>Card & benefit sources</h3><p>Reviewed by source: {esc(meta.get('lastReviewedDate', 'user supplied' if card['status'] == 'custom' else 'unknown'))}<br>{source_links(meta.get('sourceUrls', []))}</p>")
        sources.append(f"<h3>Offer sources</h3><p>Reviewed by source: {esc(offer_meta.get('lastReviewedDate', 'unknown'))}<br>{source_links(offer_meta.get('sourceUrls', []))}</p>")
        if issuer_check:
            sources.append(f"<h3>Issuer offer check</h3><p>{esc(issuer_check.get('status', 'unconfirmed'))} · Last successful check: {esc(issuer_check.get('checkedAt', 'never'))}<br>{source_links([issuer_check.get('sourceUrl', '')])}</p>")
        sources.append(f"<h3>Catalog freshness</h3><p>Downloaded: {esc(self.catalog.get('fetched_at', 'unknown'))}<br><a href='{SOURCE}'>Community catalog</a></p><p>A download refreshes published records; it does not independently verify every issuer’s terms. Review dates may be older than the download date.</p>")
        sources.append('<p>Issuer-domain labels identify the link’s domain; they do not certify current terms. Third-party references require confirmation against issuer terms.</p>')
        from .artwork import artwork_url
        image_url = artwork_url(card)
        if image_url:
            sources.append(f'<h3>Card artwork</h3><p>Image supplied by the catalog; cached locally when viewed.<br>{source_links([image_url])}</p>')
        sources.append('<p>A generated illustration is used when card artwork is unavailable. Artwork may depict an older card design.</p>')
        for name, content in [('Overview', overview), ('Offers', offers_html), ('Benefits', benefits_html), ('Sources', sources)]:
            browser = self.detail_browsers[name]
            position = browser.verticalScrollBar().value()
            browser.setHtml(''.join(content))
            if not new_card:
                browser.verticalScrollBar().setValue(position)
        if new_card:
            self.detail_tabs.setCurrentIndex(0)
        owned = card['id'] in self.profile['wallet']
        self.add_wallet.setText('Already in wallet' if owned else 'Add to wallet')
        self.add_wallet.setEnabled(not owned)
        self.settings_button.setEnabled(True)
        if reveal:
            self.details_dialog.show()
            self.details_dialog.raise_()
            self.details_dialog.activateWindow()

    def add_wallet_card(self):
        if self.current_card and self.current_card["id"] not in self.profile["wallet"]:
            self.profile["wallet"].append(self.current_card["id"])
            self.reload_cards()
            self.recalculate()

    def remove_wallet_card(self):
        item = self.wallet.currentItem()
        if item:
            self.profile["wallet"].remove(item.data(Qt.ItemDataRole.UserRole))
            self.reload_cards()
            self.recalculate()

    def select_wallet(self, item):
        card = self.by_id.get(item.data(Qt.ItemDataRole.UserRole))
        if card:
            self.show_card(card)
        else:
            self.statusBar().showMessage("This card is absent from the source. Add it manually to restore its coverage.")

    def add_custom_card(self):
        dialog = CustomCard(self, self.spend_widgets)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            card = dialog.card()
            self.profile.setdefault("custom_cards", []).append(card)
            self.profile["wallet"].append(card["id"])
            self.reload_cards()
            self.recalculate()
            self.show_card(card)

    def edit_card_settings(self):
        if not self.current_card:
            return
        dialog = CardSettings(self.current_card, self.profile, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            ident = self.current_card["id"]
            self.profile.setdefault("card_point_values", {})[ident] = dialog.value.value()
            self.profile.setdefault("card_settings", {})[ident] = dialog.settings()
            self.recalculate()

    def add_category(self):
        categories = {canonical(c) for card in self.cards for rate in card.get("earningRates", []) for c in rate.get("eligibleCategories", [])}
        dialog = QDialog(self)
        dialog.setWindowTitle("Add a spending category")
        layout = QVBoxLayout(dialog)
        note = QLabel("Split this spending out of existing categories to avoid counting it twice. Portal and merchant categories apply only to those purchases.")
        note.setWordWrap(True)
        layout.addWidget(note)
        choice = SearchableCategoryBox()
        for category in sorted(categories - set(self.spend_widgets)):
            choice.addItem(label(category), category)
        layout.addWidget(choice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        accept = buttons.button(QDialogButtonBox.StandardButton.Ok)
        accept.setEnabled(bool(choice.currentData()))
        choice.selectionChanged.connect(lambda: accept.setEnabled(bool(choice.currentData())))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and choice.currentData():
            self.add_spend_row(choice.currentData())
            self.recalculate()

    def update_freshness(self):
        when = self.catalog.get("fetched_at") or "never"
        cached = (self.directory / "catalog.json").exists()
        active = sum(c.get('status') == 'active' for c in self.catalog['cards'])
        self.freshness.setText(f"{active} active cards\n{'Local cache' if cached else 'Bundled snapshot'} · {when[:10]}")

    def refresh(self):
        if self.worker and self.worker.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.freshness.setText("Checking cards and offers…")
        self.worker = RefreshThread(self.store)
        self.worker.progress.connect(self.refresh_progress)
        self.worker.result.connect(self.refreshed)
        self.worker.failed.connect(self.refresh_failed)
        self.worker.finished.connect(self.refresh_finished)
        self.worker.start()

    def refresh_progress(self, done, total):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.freshness.setText(f"Updating cards…\n{done} / {total} downloaded")

    def refreshed(self, value):
        self.catalog = value
        self.artwork.refresh()
        self.reload_cards()
        self.recalculate()
        self.update_freshness()

    def refresh_failed(self, error):
        self.update_freshness()
        self.freshness.setText(self.freshness.text() + "\nRefresh failed · using saved data")
        self.freshness.setToolTip(error)

    def refresh_finished(self):
        self.refresh_button.setEnabled(True)
        self.progress.hide()
        if self.closing:
            self.close()

    def closeEvent(self, event):
        self.artwork.close()
        self.timer.stop()
        self.details_dialog.close()
        self.save_profile()
        if self.worker and self.worker.isRunning():
            self.closing = True
            self.worker.requestInterruption()
            self.hide()
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Card Compass")
    app.setOrganizationName("CardCompass")
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    families = set(QFontDatabase.families())
    family = next((name for name in ("Inter", "SF Pro Text", "Segoe UI", "DejaVu Sans") if name in families), app.font().family())
    app.setFont(QFont(family, 10))
    app.setStyleSheet(STYLE)
    smoke = "--smoke-test" in sys.argv
    temporary = tempfile.TemporaryDirectory(prefix="card-compass-smoke-") if smoke else None
    window = MainWindow(Path(temporary.name) if temporary else None, auto_refresh=not smoke)
    window.show()
    if smoke:
        if window.active_page != 'explore' or window.dashboard_scroll.isHidden() or not window.catalog_table.isHidden():
            raise RuntimeError("Packaged discovery dashboard unavailable")
        window.category_tiles['cashback'].click()
        if not window.explored or any(not discoverable(c) for c in window.explored):
            raise RuntimeError("Packaged active-card discovery unavailable")
        window.reset_discovery()
        window.spend_widgets["groceries"].setValue(600)
        window.spend_widgets["dining"].setValue(400)
        window.recalculate()
        if not window.cards or not window.matches:
            raise RuntimeError("Packaged catalog or recommendation engine unavailable")
        from .offers import VENTURE_ID
        from .artwork import artwork_url
        from PySide6.QtGui import QImage
        image_path, _ = window.artwork.paths(artwork_url(window.by_id[VENTURE_ID]))
        image_path.parent.mkdir(parents=True, exist_ok=True)
        sample_image = QImage(120, 75, QImage.Format.Format_ARGB32)
        sample_image.fill(QColor('#246842'))
        if not sample_image.save(str(image_path), 'PNG'):
            raise RuntimeError("Packaged image encoder unavailable")
        if window.artwork.pixmap(window.by_id[VENTURE_ID]) is None:
            raise RuntimeError("Packaged cached card artwork unavailable")
        window.show_card(window.by_id[VENTURE_ID])
        offer_text = window.offer_details.toPlainText()
        if '75,000 miles + $300 Capital One Travel Stays credit' not in offer_text or 'Limited-time offer' not in offer_text:
            raise RuntimeError("Packaged compound signup offer unavailable")
        import pymupdf
        from .statements import parse_pdf
        pdf_path = Path(temporary.name) / 'smoke.pdf'
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((30, 40), 'American Express Closing Date 09/21/26', fontsize=10)
            page.insert_text((30, 60), 'Account Ending 1-00000', fontsize=10)
            page.insert_text((30, 90), '09/06/26 FOOD LION #0001 EXAMPLE NC $1.00', fontsize=10)
            document.save(pdf_path)
        statement = parse_pdf(pdf_path)
        ledger = window.imports_page.ledger
        rows = ledger.preview(statement, 'smoke-card')
        count = ledger.commit(statement, 'smoke-card', rows, {'FOOD LION': {'name': 'Food Lion', 'category': 'groceries'}})
        remembered = ledger.preview(statement, 'smoke-card')[0]
        if count != 1 or not remembered['duplicate'] or remembered['category'] != 'groceries':
            raise RuntimeError('Packaged PDF extraction, merchant memory, or duplicate checks unavailable')
        from .csv_statements import parse_csv_text
        csv_statement = parse_csv_text('Date,Description,Amount\n2026-09-07,FOOD LION #0001 EXAMPLE NC,2.00\n',
            {'date': 'Date', 'description': 'Description', 'amount': 'Amount'}, 'positive',
            issuer='amex', closing_date='2026-09-21', account_ending='00000')
        csv_rows = ledger.preview(csv_statement, 'smoke-card')
        if csv_rows[0]['category'] != 'groceries' or ledger.commit(csv_statement, 'smoke-card', csv_rows, {}) != 1:
            raise RuntimeError('Packaged CSV parsing or merchant memory unavailable')
        window.breakdown_page.reload()
        if window.breakdown_page.report['net_cents'] != 300 or window.breakdown_page.report['count'] != 2:
            raise RuntimeError('Packaged spending breakdown unavailable')
        print(f"Desktop smoke test: {len(window.cards)} cards, {len(window.matches)} matches", flush=True)
        print('Dashboard smoke test: category tiles open active cards; dashboard is the startup page', flush=True)
        print('Artwork smoke test: cached card image decoded for native display', flush=True)
        print('Offer smoke test: Venture miles, stays credit, and limited-time label displayed', flush=True)
        print('Statement smoke test: PDF parsed, merchant remembered, duplicate skipped', flush=True)
        print('CSV smoke test: CSV parsed and saved with remembered merchant category', flush=True)
        print('Breakdown smoke test: imported purchases total $3.00', flush=True)
        QTimer.singleShot(100, window.close)
    code = app.exec()
    if temporary:
        temporary.cleanup()
    sys.exit(code)
