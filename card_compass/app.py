"""Native Qt interface. All user state is local; refresh runs off the UI thread."""
from __future__ import annotations

import html
import sys
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QDoubleSpinBox, QCheckBox, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QTextBrowser,
    QListWidget, QListWidgetItem, QDialog, QDialogButtonBox, QFormLayout,
    QMessageBox, QProgressBar, QAbstractItemView, QScrollArea,
)

from .catalog import CatalogStore, data_dir, load_profile, write_json, SOURCE
from .engine import DEFAULT_CATEGORIES, canonical, label, recommend, available_offers, point_value, rules_for


STYLE = """
QWidget { background: #f4f6fa; color: #24324a; font-size: 13px; }
QLabel#title { font-size: 28px; font-weight: 700; color: #162b45; }
QLabel#subtitle { color: #68758a; }
QLabel#metric { font-size: 18px; font-weight: 600; padding: 12px; background: #e5edf8; border-radius: 8px; }
QPushButton { background: #ffffff; border: 1px solid #ced6e3; border-radius: 6px; padding: 8px 14px; }
QPushButton:hover { background: #e7eef8; }
QPushButton#primary { background: #2358be; color: white; border: none; }
QPushButton:disabled { color: #8894a4; background: #eef0f4; }
QLineEdit, QComboBox, QDoubleSpinBox { background: white; border: 1px solid #ced6e3; border-radius: 5px; padding: 5px; }
QTableWidget, QListWidget, QTextBrowser { background: white; border: 1px solid #dce2ec; border-radius: 6px; }
QHeaderView::section { background: #eaf0f8; color: #485871; padding: 8px; border: none; font-weight: 600; }
QTableWidget::item:selected, QListWidget::item:selected { background: #dfeaff; color: #152f5c; }
QTabBar::tab { padding: 10px 20px; background: #e5eaf2; }
QTabBar::tab:selected { background: white; color: #2358be; }
QProgressBar { max-height: 6px; border: none; background: #e5eaf2; }
QProgressBar::chunk { background: #2358be; }
"""


def money(value):
    return f"{'−' if value < 0 else ''}${abs(value):,.0f}"


def spin(value=0, maximum=100000, prefix="", suffix=""):
    widget = QDoubleSpinBox()
    widget.setRange(0, maximum)
    widget.setDecimals(2)
    widget.setPrefix(prefix)
    widget.setSuffix(suffix)
    widget.setValue(value)
    return widget


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.setAlternatingRowColors(True)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
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
            options = QListWidget()
            for category in selection.get("options", []):
                item = QListWidgetItem(label(category))
                item.setData(Qt.ItemDataRole.UserRole, category)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if category in settings.get("selections", {}).get(benefit["id"], []) else Qt.CheckState.Unchecked)
                options.addItem(item)
            inner.addWidget(options)
            self.choices[benefit["id"]] = (options, count)
        inner.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate(self):
        for options, count in self.choices.values():
            if sum(options.item(i).checkState() == Qt.CheckState.Checked for i in range(options.count())) > count:
                QMessageBox.information(self, "Too many categories", f"Choose at most {count} categories for each benefit.")
                return
        self.accept()

    def settings(self):
        return {"activated": self.active.isChecked(), "selections": {
            ident: [options.item(i).data(Qt.ItemDataRole.UserRole) for i in range(options.count())
                    if options.item(i).checkState() == Qt.CheckState.Checked]
            for ident, (options, _) in self.choices.items()}}


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
        self.setWindowTitle("Card Compass")
        self.resize(1440, 930)
        self.setMinimumSize(1050, 700)
        self.directory = directory or data_dir()
        self.store = CatalogStore(self.directory)
        self.profile = load_profile(self.directory)
        self.catalog = self.store.load()
        self.worker = None
        self.closing = False
        self.current_card = None
        self.build_ui()
        self.reload_cards()
        self.update_freshness()
        self.recalculate()
        if auto_refresh:
            QTimer.singleShot(0, self.refresh)

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 16)
        header = QHBoxLayout()
        title_group = QVBoxLayout()
        title = QLabel("Card Compass")
        title.setObjectName("title")
        title_group.addWidget(title)
        subtitle = QLabel("Find the next card that earns its place in your wallet · US cards")
        subtitle.setObjectName("subtitle")
        title_group.addWidget(subtitle)
        header.addLayout(title_group)
        header.addStretch()
        self.refresh_button = QPushButton("Refresh card data")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        self.freshness = QLabel()
        self.freshness.setWordWrap(True)
        layout.addWidget(self.freshness)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        body = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(body, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 12, 12, 0)
        left_layout.addWidget(QLabel("MY WALLET"))
        self.wallet = QListWidget()
        self.wallet.setMaximumHeight(180)
        self.wallet.itemClicked.connect(self.select_wallet)
        left_layout.addWidget(self.wallet)
        wallet_actions = QHBoxLayout()
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_wallet_card)
        custom = QPushButton("Add manually")
        custom.clicked.connect(self.add_custom_card)
        wallet_actions.addWidget(remove)
        wallet_actions.addWidget(custom)
        left_layout.addLayout(wallet_actions)
        tip = QLabel("Find your cards in Explore, then select Add to wallet.")
        tip.setWordWrap(True)
        left_layout.addWidget(tip)
        left_layout.addWidget(QLabel("MONTHLY SPENDING & COVERAGE"))
        tip = QLabel("Use separate spending buckets. Auto derives coverage from your wallet; 0% marks a gap. Largest spending appears first in Coverage.")
        tip.setWordWrap(True)
        left_layout.addWidget(tip)
        self.spending = table(["Category", "Monthly $", "Covered %"])
        self.spend_widgets = {}
        self.coverage_widgets = {}
        categories = list(DEFAULT_CATEGORIES)
        categories += sorted(set(self.profile.get("spend", {})) - set(categories))
        for category in categories:
            self.add_spend_row(category)
        self.spending.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        left_layout.addWidget(self.spending, 1)
        add_category = QPushButton("Add spending category…")
        add_category.clicked.connect(self.add_category)
        left_layout.addWidget(add_category)
        body.addWidget(left)

        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(8, 12, 8, 0)
        self.metrics = QLabel()
        self.metrics.setObjectName("metric")
        self.metrics.setWordWrap(True)
        middle_layout.addWidget(self.metrics)
        options = QHBoxLayout()
        self.sort = QComboBox()
        self.sort.addItems(["Ongoing value", "First-year value"])
        self.sort.setCurrentText(self.profile.get("sort", "Ongoing value"))
        options.addWidget(self.sort)
        options.addWidget(QLabel("Max annual fee"))
        self.max_fee = spin(self.profile.get("max_fee", 1000), 10000, prefix="$")
        options.addWidget(self.max_fee)
        middle_layout.addLayout(options)
        assumptions = QHBoxLayout()
        assumptions.addWidget(QLabel("Points / miles"))
        self.points = spin(self.profile.get("point_value", 1), 20, suffix=" ¢ each")
        self.points.setToolTip("Assumed value for every point/mile unless overridden in Card settings. Hotel and airline currencies differ.")
        assumptions.addWidget(self.points)
        assumptions.addWidget(QLabel("Coverage target"))
        self.target = spin(self.profile.get("target_rate", 2), 100, suffix=" %")
        assumptions.addWidget(self.target)
        middle_layout.addLayout(assumptions)
        self.business = QCheckBox("Include business cards")
        self.business.setChecked(self.profile.get("include_business", False))
        self.bonus = QCheckBox("Count public signup bonuses (assume I qualify)")
        self.bonus.setChecked(self.profile.get("eligible_bonus", False))
        middle_layout.addWidget(self.business)
        middle_layout.addWidget(self.bonus)
        self.tabs = QTabWidget()
        self.recommendations = table(["Next card", "Annual fee", "Ongoing +$", "Year one +$"])
        self.recommendations.itemSelectionChanged.connect(self.select_recommendation)
        self.tabs.addTab(self.recommendations, "Matches")
        self.coverage = table(["Category", "Monthly spend", "Effective rate", "Coverage"])
        self.tabs.addTab(self.coverage, "Coverage")
        explore = QWidget()
        explore_layout = QVBoxLayout(explore)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search card, issuer, benefit, or category…")
        self.search.textChanged.connect(self.filter_catalog)
        explore_layout.addWidget(self.search)
        self.catalog_table = table(["Card", "Annual fee", "Status"])
        self.catalog_table.itemSelectionChanged.connect(self.select_catalog)
        explore_layout.addWidget(self.catalog_table)
        self.tabs.addTab(explore, "Explore")
        middle_layout.addWidget(self.tabs, 1)
        self.notes = QLabel()
        self.notes.setWordWrap(True)
        middle_layout.addWidget(self.notes)
        body.addWidget(middle)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 0, 0)
        right_layout.addWidget(QLabel("CARD DETAILS"))
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.details.setHtml("<h2>Your next card, explained.</h2><p>Select a match or explore the catalog to compare rewards, benefits, and published signup offers.</p>")
        right_layout.addWidget(self.details, 1)
        self.add_wallet = QPushButton("Add to wallet")
        self.add_wallet.setObjectName("primary")
        self.add_wallet.setEnabled(False)
        self.add_wallet.clicked.connect(self.add_wallet_card)
        right_layout.addWidget(self.add_wallet)
        self.settings_button = QPushButton("Card settings")
        self.settings_button.setEnabled(False)
        self.settings_button.clicked.connect(self.edit_card_settings)
        right_layout.addWidget(self.settings_button)
        body.addWidget(right)
        body.setSizes([350, 650, 390])
        footer = QLabel("Estimates assume purchases are paid in full and monthly spending is steady. Point values are assumptions; credits, interest, taxes, and eligibility rules are excluded. Source review dates appear in details.")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.recalculate)
        for widget in (self.points, self.target, self.max_fee):
            widget.valueChanged.connect(self.schedule_update)
        self.sort.currentTextChanged.connect(self.schedule_update)
        self.business.toggled.connect(self.schedule_update)
        self.bonus.toggled.connect(self.schedule_update)

    def add_spend_row(self, category):
        row = self.spending.rowCount()
        self.spending.insertRow(row)
        self.spending.setItem(row, 0, QTableWidgetItem(label(category)))
        amount = spin(self.profile.get("spend", {}).get(category, 0))
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
        self.spending.setRowHeight(row, 38)

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
        self.cards = self.catalog["cards"] + self.profile.get("custom_cards", [])
        self.by_id = {c["id"]: c for c in self.cards}
        self.wallet.clear()
        for ident in self.profile.get("wallet", []):
            card = self.by_id.get(ident)
            if card:
                item = QListWidgetItem(f"{card['name']} · {card['issuerId'].replace('-', ' ').title()}")
                item.setData(Qt.ItemDataRole.UserRole, ident)
                self.wallet.addItem(item)
            else:
                item = QListWidgetItem(f"Unavailable card · {ident}")
                item.setData(Qt.ItemDataRole.UserRole, ident)
                self.wallet.addItem(item)
        self.filter_catalog()

    def recalculate(self):
        self.save_profile()
        self.matches, baseline, warnings = recommend(self.cards, self.profile)
        monthly = sum(self.profile["spend"].values())
        self.metrics.setText(f"{money(monthly)} / month  ·  {money(baseline.total)} current rewards / year")
        self.recommendations.blockSignals(True)
        self.recommendations.setRowCount(len(self.matches))
        for row, match in enumerate(self.matches):
            c = match["card"]
            values = [f"{c['name']} · {c['issuerId'].replace('-', ' ').title()}", money(c["annualFee"]["amount"]), money(match["ongoing"]), money(match["first_year"])]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, c["id"])
                item.setToolTip("\n".join(match["warnings"]) or "Incremental value compared with your existing wallet")
                self.recommendations.setItem(row, col, item)
        self.recommendations.blockSignals(False)
        categories = sorted(self.profile["spend"], key=lambda k: -self.profile["spend"][k])
        self.coverage.setRowCount(len(categories))
        for row, cat in enumerate(categories):
            spend = self.profile["spend"][cat]
            reward = baseline.category_rewards.get(cat, 0)
            effective = reward / (spend * 12) * 100 if spend else 0
            state = "No spend" if not spend else "Covered" if effective >= self.target.value() else "Gap"
            values = [label(cat), money(spend), f"{effective:.2f}%" if spend else "—", state]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                providers = baseline.category_cards.get(cat, [])
                item.setToolTip(" / ".join("Manual coverage" if i == "manual" else self.by_id[i]["name"] for i in providers))
                self.coverage.setItem(row, col, item)
        self.notes.setText("Enter monthly spending to rank your matches." if not monthly else f"{len(self.matches)} matches · figures show additional value after the new card's fee. " + (f"{len(warnings)} wallet assumptions need review; see Card settings." if warnings else ""))
        if self.current_card and self.current_card["id"] in self.by_id:
            self.show_card(self.by_id[self.current_card["id"]])

    def filter_catalog(self):
        query = self.search.text().strip().lower()
        self.explored = [c for c in self.cards if query in (c["name"] + " " + c["issuerId"] + " " + str(c.get("benefits", [])) + str(c.get("earningRates", []))).lower()]
        self.catalog_table.blockSignals(True)
        self.catalog_table.setRowCount(len(self.explored))
        for row, card in enumerate(self.explored):
            for col, value in enumerate([f"{card['name']} · {card['issuerId'].replace('-', ' ').title()}", money(card["annualFee"]["amount"]), card["status"].title()]):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, card["id"])
                self.catalog_table.setItem(row, col, item)
        self.catalog_table.blockSignals(False)

    def select_recommendation(self):
        row = self.recommendations.currentRow()
        if 0 <= row < len(self.matches):
            self.show_card(self.matches[row]["card"])

    def select_catalog(self):
        row = self.catalog_table.currentRow()
        if 0 <= row < len(self.explored):
            self.show_card(self.explored[row])

    def show_card(self, card):
        self.current_card = card
        esc = lambda v: html.escape(str(v))
        def source_links(urls):
            return "<br>".join(f'<a href="{esc(u)}">{esc(u)}</a>' for u in urls if isinstance(u, str) and u.startswith("https://"))
        parts = [f"<h2>{esc(card['name'])}</h2><p>{esc(card['issuerId'].replace('-', ' ').title())} · {esc(card['status'])}<br>Annual fee: {money(card['annualFee']['amount'])}" + (" · waived first year" if card["annualFee"].get("waivedFirstYear") else "") + "</p>"]
        match = next((m for m in self.matches if m["card"]["id"] == card["id"]), None)
        if match:
            parts.append(f"<h3>{money(match['ongoing'])} ongoing · {money(match['first_year'])} year one</h3><p>Extra rewards: {money(match['gain'])}/year. Counted signup value: {money(match['bonus'])}.</p>")
            gains = sorted(match["category_gains"].items(), key=lambda p: -p[1])
            parts.append("<p>" + "<br>".join(f"{esc(label(c))}: {money(v)} change/year" for c, v in gains if abs(v) > .01) + "</p>")
        _, warnings = rules_for(card, self.profile)
        if warnings:
            parts.append("<h3>Assumptions to review</h3><ul>" + "".join(f"<li>{esc(w)}</li>" for w in warnings) + "</ul>")
        parts.append(f"<p>Point / mile estimate: {point_value(card, self.profile):g}¢ each. Change in Card settings.</p><h3>Published signup offers</h3>")
        offers = available_offers(card, self.profile)
        if not offers:
            parts.append("<p>No unexpired published offers in this source.</p>")
        for offer in offers:
            value = money(offer["value"]) + " estimated" if offer["value"] is not None else "Value unresolved"
            spend = money(offer["spendRequirement"]) if offer.get("spendRequirement") is not None else "unknown spend"
            days = offer.get("timeframeDays", "unknown")
            parts.append(f"<p><b>{esc(offer.get('rewardAmount', 0))} {esc(offer.get('rewardUnit', ''))}</b> · {value}<br>{spend} in {esc(days)} days · {esc(offer.get('offerType', 'unknown'))}<br>" + ("Spending pace meets requirement" if offer["feasible"] else "Requirement exceeds spending pace or is unresolved") + (" · counted in year one" if offer["counted"] else " · not counted") + "</p>")
            if offer.get("terms"):
                parts.append(f"<p>{esc(offer['terms'])}</p>")
            if offer.get("expirationDate"):
                parts.append(f"<p>Offer expires: {esc(offer['expirationDate'])}</p>")
        offer_meta = card.get("offerSourceMeta", {})
        parts.append(f"<p>Offers reviewed by source: {esc(offer_meta.get('lastReviewedDate', 'unknown'))}<br>{source_links(offer_meta.get('sourceUrls', []))}</p>")
        parts.append("<h3>Earning rates</h3>")
        for rate in card.get("earningRates", []):
            parts.append(f"<p><b>{esc(rate.get('multiplier'))} {esc(rate.get('rewardUnit'))}</b> · {esc(', '.join(label(c) for c in rate.get('eligibleCategories', [])))}<br>{esc(rate.get('terms', ''))}</p>")
            if rate.get("spendCap"):
                cap = rate["spendCap"]
                period = cap.get("period", {})
                parts.append(f"<p>Shared cap: {money(cap['amount'])} per {esc(period.get('count', 1))} {esc(period.get('unit', 'unknown'))}.</p>")
        parts.append("<h3>Benefits & conditions</h3>")
        if not card.get("benefits"):
            parts.append("<p>No benefits recorded in this source.</p>")
        for benefit in card.get("benefits", []):
            parts.append(f"<p><b>{esc(benefit['name'])}</b><br>{esc(benefit.get('description', ''))}<br>{esc(benefit.get('terms', ''))}<br>Verified: {esc(benefit.get('lastVerified', 'unknown'))}<br>{source_links([benefit.get('sourceUrl', '')])}</p>")
            detail = benefit.get("bonusCategoryDetail")
            if detail:
                selection = detail.get("categorySelection", {})
                parts.append(f"<p>{esc(detail.get('multiplier'))} {esc(detail.get('rewardUnit'))} · {esc(selection.get('mode', 'fixed categories'))}<br>Categories: {esc(', '.join(detail.get('eligibleCategories', selection.get('options', []))))}<br>Cycle: {esc(selection.get('currentCycle', 'standing terms'))}</p>")
        meta = card.get("sourceMeta", {})
        parts.append(f"<h3>Sources</h3><p>Card reviewed by source: {esc(meta.get('lastReviewedDate', 'user supplied' if card['status'] == 'custom' else 'unknown'))}<br>{source_links(meta.get('sourceUrls', []))}</p>")
        self.details.setHtml("".join(parts))
        owned = card["id"] in self.profile["wallet"]
        self.add_wallet.setText("Already in wallet" if owned else "Add to wallet")
        self.add_wallet.setEnabled(not owned)
        self.settings_button.setEnabled(True)

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
        choice = QComboBox()
        for category in sorted(categories - set(self.spend_widgets)):
            choice.addItem(label(category), category)
        layout.addWidget(choice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and choice.currentData():
            self.add_spend_row(choice.currentData())
            self.recalculate()

    def update_freshness(self):
        when = self.catalog.get("fetched_at") or "never"
        cached = (self.directory / "catalog.json").exists()
        self.freshness.setText(f"{len(self.catalog['cards'])} cards · {'Local cache' if cached else 'Bundled snapshot'} downloaded {when}. Source review dates may be older; confirm terms on issuer links.")

    def refresh(self):
        if self.worker and self.worker.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.freshness.setText("Checking published cards, benefits, and offers… Your wallet remains available.")
        self.worker = RefreshThread(self.store)
        self.worker.progress.connect(self.refresh_progress)
        self.worker.result.connect(self.refreshed)
        self.worker.failed.connect(self.refresh_failed)
        self.worker.finished.connect(self.refresh_finished)
        self.worker.start()

    def refresh_progress(self, done, total):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.freshness.setText(f"Refreshing catalog · {done} / {total} cards")

    def refreshed(self, value):
        self.catalog = value
        self.reload_cards()
        self.recalculate()
        self.update_freshness()

    def refresh_failed(self, error):
        self.update_freshness()
        self.freshness.setText(self.freshness.text() + " Refresh failed; retained previous data.")
        self.freshness.setToolTip(error)

    def refresh_finished(self):
        self.refresh_button.setEnabled(True)
        self.progress.hide()
        if self.closing:
            self.close()

    def closeEvent(self, event):
        self.timer.stop()
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
    app.setStyleSheet(STYLE)
    smoke = "--smoke-test" in sys.argv
    temporary = tempfile.TemporaryDirectory(prefix="card-compass-smoke-") if smoke else None
    window = MainWindow(Path(temporary.name) if temporary else None, auto_refresh=not smoke)
    window.show()
    if smoke:
        window.spend_widgets["groceries"].setValue(600)
        window.spend_widgets["dining"].setValue(400)
        window.recalculate()
        if not window.cards or not window.matches:
            raise RuntimeError("Packaged catalog or recommendation engine unavailable")
        print(f"Desktop smoke test: {len(window.cards)} cards, {len(window.matches)} matches", flush=True)
        QTimer.singleShot(100, window.close)
    code = app.exec()
    if temporary:
        temporary.cleanup()
    sys.exit(code)
