"""Local text-based BOFA/Amex statement parsing. No category inference or uploads."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
import hashlib
from pathlib import Path
import re
import unicodedata


@dataclass
class Transaction:
    date: str
    posting_date: str
    description: str
    amount_cents: int
    kind: str = 'purchase'
    reference: str = ''
    account_ending: str = ''
    issuer_category: str = ''
    page: int = 1


@dataclass
class Statement:
    issuer: str
    account_ending: str
    closing_date: str
    transactions: list[Transaction]
    source_hash: str = ''
    filename: str = ''
    expected_purchases: int | None = None
    warnings: list[str] = field(default_factory=list)
    date_confirmed: bool = True

    @property
    def purchases_cents(self):
        return sum(t.amount_cents for t in self.transactions if t.kind in ('purchase', 'refund'))


MONEY = r'\(?\s*[+-]?\s*\$?\s*[+-]?\s*\d[\d,]*\.\d{2}\s*\)?(?:\s*CR)?'
STATES = r'(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)'


def cents(value):
    negative = '-' in value or '(' in value or value.strip().upper().endswith('CR')
    digits = re.sub(r'[^0-9.]', '', value)
    result = int(Decimal(digits) * 100)
    return -result if negative else result


def merchant_key(description):
    value = unicodedata.normalize('NFKD', description).encode('ascii', 'ignore').decode().upper()
    value = re.sub(r'^(?:APLPAY\s+|APPLE\s+PAY\s+|TST\s*\*\s*|SQ\s*\*\s*|SQUARE\s*\*\s*)', '', value)
    value = re.sub(r'\b(?:\+?1[- .]?)?\d{3}[- .]\d{3}[- .]\d{4}\b', '', value)
    value = re.sub(r'\b[\w.-]+\.(?:COM|NET|ORG)(?:/\S*)?', '', value)
    value = re.sub(r'[^A-Z0-9 ]', ' ', value)
    value = ' '.join(value.split())
    # Identity normalization only: these never assign a spending category.
    chains = [('CHIPOTLE', r'^CHIPOTLE\b'), ('FOOD LION', r'^FOOD LION\b'),
              ('HARRIS TEETER', r'^HARRIS TEETER\b'), ('TRADER JOES', r'^TRADER JOE\s*S?\b'),
              ('MCDONALDS', r'^MC\s*DONALD\s*S?\b'), ('TACO BELL', r'^TACO BELL\b'),
              ('TARGET', r'^TARGET STORES?\b'), ('DELTA AIR LINES', r'^DELTA AIR LINES\b'),
              ('DISNEYPLUS', r'^DISNEY\s*PLUS\b'), ('AMAZON MARKETPLACE', r'^AMAZON MARKETPLACE\b')]
    for name, pattern in chains:
        if re.match(pattern, value):
            return name
    # Avoid broad fuzzy matching: ambiguous merchants stay separate until linked.
    value = re.sub(r'\s+' + STATES + r'$', '', value)
    value = re.sub(r'\s+\d{3,}\b', '', value)
    return ' '.join(value.split()) or 'UNKNOWN MERCHANT'


def parse_date(value, closing=None):
    parts = value.split('/')
    if len(parts) == 3:
        year = int(parts[2])
        return date(year + 2000 if year < 100 else year, int(parts[0]), int(parts[1])).isoformat()
    if closing is None:
        raise ValueError('Confirm the statement closing date to resolve transaction years.')
    end = date.fromisoformat(closing)
    year = end.year - (int(parts[0]) > end.month)
    return date(year, int(parts[0]), int(parts[1])).isoformat()


def closing_from_text(text):
    match = re.search(r'(?:CLOSING\s+DATE|STATEMENT\s+(?:ENDING|END|DATE))\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})', text, re.I)
    if match:
        return parse_date(match[1])
    # BOFA commonly prints a month-name billing-period range.
    match = re.search(r'([A-Za-z]+\s+\d{1,2},?\s*(?:\d{4})?)\s*[-–]\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})', text)
    if match:
        for fmt in ('%B %d, %Y', '%B %d %Y', '%b %d, %Y', '%b %d %Y'):
            try:
                return datetime.strptime(match[2], fmt).date().isoformat()
            except ValueError:
                pass
    return ''


def section_kind(line, previous):
    upper = line.upper()
    if re.match(r'^(?:PAYMENTS?(?:\s+AND)?|CREDITS?|PAYMENTS AND OTHER CREDITS)', upper) and not re.match(r'^\d', line):
        return 'payment'
    if re.match(r'^(?:PURCHASES AND ADJUSTMENTS|NEW CHARGES|DETAIL(?: CONTINUED)?)$', upper):
        return 'purchase'
    if re.match(r'^INTEREST CHARGED$', upper):
        return 'interest'
    if re.match(r'^FEES(?: CHARGED)?$', upper):
        return 'fee'
    return previous


def kind_for(description, amount, section):
    upper = description.upper()
    if 'INTEREST CHARGED' in upper:
        return 'interest'
    if re.search(r'\b(?:BALANCE TRANSFER|CASH ADVANCE)\b', upper):
        return 'transfer'
    if re.search(r'\b(?:ANNUAL MEMBERSHIP FEE|LATE PAYMENT FEE)\b', upper):
        return 'fee'
    if re.search(r'\b(?:PAYMENT|AUTOPAY)\b', upper):
        return 'payment'
    if re.search(r'\b(?:REWARDS? CREDIT|CASH ?BACK|STATEMENT CREDIT)\b', upper):
        return 'credit'
    if section in ('fee', 'interest'):
        return section
    # A credit at a merchant is a refund, not a card payment.
    if amount < 0:
        return 'refund'
    return 'purchase' if section == 'payment' else section


def parse_statement_text(text, issuer=None, closing_date=None):
    detected = 'amex' if re.search(r'AMERICAN EXPRESS', text, re.I) else 'bofa' if re.search(r'BANK OF AMERICA|PURCHASES AND ADJUSTMENTS|REFERENCE\s+NUMBER', text, re.I) else 'amex' if re.search(r'ACCOUNT ENDING|\b\d{2}/\d{2}/\d{2,4}\b', text, re.I) else None
    issuer = issuer or detected
    if issuer not in ('bofa', 'amex'):
        raise ValueError('Supported statements are Bank of America and American Express. The issuer could not be identified.')
    close = closing_date or closing_from_text(text)
    confirmed = bool(close)
    if not close and issuer == 'amex':
        dates = re.findall(r'(?m)^\s*(\d{2}/\d{2}/\d{2,4})\s+', text)
        close = max((parse_date(d) for d in dates), default='')
    if not close:
        raise ValueError('Statement year is missing. Select the closing date and parse again.')
    date.fromisoformat(close)
    ending_match = re.search(r'ACCOUNT ENDING\s*(?:IN\s*)?([\d-]{4,})', text, re.I)
    ending = re.sub(r'\D', '', ending_match[1])[-5:] if ending_match else ''
    transactions, warnings = [], []
    section = 'purchase'
    page = 1
    pending = []
    pending_section = section
    pending_page = page
    expected = None

    def flush():
        nonlocal pending, ending
        if not pending:
            return
        block = ' '.join(pending)
        if issuer == 'bofa':
            match = re.match(r'^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+(\d{4,})\s+(\d{4})\s+(' + MONEY + r')\s*$', block)
            if match:
                first, posted, description, reference, account, amount = match.groups()
                ending = ending or account
            else:
                match = re.match(r'^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+(' + MONEY + r')\s*$', block)
                if not match:
                    warnings.append(f'Page {pending_page}: a dated BOFA row could not be read. Check the source before saving.')
                    pending = []
                    return
                first, posted, description, amount = match.groups()
                reference, account = '', ending
            issuer_category = ''
        else:
            match = re.match(r'^(\d{2}/\d{2}/\d{2,4})\s+(.+)', pending[0])
            first, description = match.groups()
            posted, reference, account = first, '', ending
            # The amount may be beside the merchant or below a multiline itinerary.
            found = re.search(r'\(?\s*[+-]?\s*\$\s*[+-]?\s*\d[\d,]*\.\d{2}\s*\)?(?:\s*CR)?', block)
            if not found:
                warnings.append(f'Page {pending_page}: a dated Amex entry has no amount. Check the source before saving.')
                pending = []
                return
            amount = found[0].strip()
            description = re.sub(r'\s*' + re.escape(amount) + r'\s*$', '', description).strip()
            issuer_category = next((line for line in pending[1:] if line in ('GROCERY STORE', 'GROCERY STORES', 'MERCHANDISE', 'RESTAURANT', 'RESTAURANTS', 'AIRLINE', 'AIRLINES')), '')
        try:
            amount_cents = cents(amount)
            transactions.append(Transaction(parse_date(first, close), parse_date(posted, close), description,
                                            amount_cents, kind_for(description, amount_cents, pending_section),
                                            reference, account, issuer_category, pending_page))
        except ValueError:
            warnings.append(f'Page {pending_page}: an invalid transaction date or amount needs review.')
        pending = []

    date_start = r'^\d{2}/\d{2}\s+\d{2}/\d{2}\s+' if issuer == 'bofa' else r'^\d{2}/\d{2}/\d{2,4}\s+\S'
    for raw in text.replace('\r', '').splitlines():
        if '\f' in raw:
            page += raw.count('\f')
            raw = raw.replace('\f', '')
        line = ' '.join(raw.split())
        if not line:
            continue
        total = re.search(r'TOTAL PURCHASES AND ADJUSTMENTS FOR THIS PERIOD\s*(' + MONEY + ')', line, re.I)
        if total:
            flush()
            expected = cents(total[1])
            continue
        new_section = section_kind(line, section)
        if new_section != section:
            flush()
            section = new_section
            continue
        if re.match(date_start, line):
            flush()
            pending, pending_section, pending_page = [line], section, page
        elif re.match(r'^(?:TOTAL |P\.\s*\d|PAGE\s+\d|CONTINUED ON|DETAIL CONTINUED|AMOUNT$|.*ACCOUNT ENDING)', line, re.I):
            continue
        elif pending:
            # BOFA rows can end immediately, then be followed by non-transaction
            # statement text. Keep only until their terminal amount is complete.
            if issuer == 'bofa' and re.search(MONEY + r'\s*$', ' '.join(pending)):
                flush()
            else:
                pending.append(line)
    flush()
    if not transactions:
        raise ValueError('No readable transactions found. This layout may be unsupported or the PDF may be scanned.')
    result = Statement(issuer, ending, close, transactions, expected_purchases=expected, warnings=warnings, date_confirmed=confirmed)
    if expected is not None and expected != result.purchases_cents:
        result.warnings.append(f'Purchase total mismatch: parsed ${result.purchases_cents / 100:,.2f}; statement ${expected / 100:,.2f}.')
    if expected is None:
        result.warnings.append('No purchase total was available for reconciliation. Review the extracted transactions against the PDF.')
    if not confirmed:
        result.warnings.append('Closing date was inferred from the latest transaction. Confirm the actual statement closing date.')
    if any(t.posting_date > close for t in transactions):
        result.warnings.append('Some transactions fall after the selected closing date. Verify the statement date before saving.')
    endings = {t.account_ending for t in transactions if t.account_ending}
    if len(endings) > 1:
        result.warnings.append('The PDF contains multiple account endings. Confirm these transactions belong to the selected card account.')
    return result


def extract_pdf_text(path):
    import pymupdf
    path = Path(path)
    if path.stat().st_size > 30_000_000:
        raise ValueError('Select a PDF smaller than 30 MB.')
    pages = []
    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise ValueError('This PDF is password-protected. Import an unlocked local copy.')
        if document.page_count > 100:
            raise ValueError('Select a statement with no more than 100 pages.')
        for page in document:
            rows = []
            for word in sorted(page.get_text('words'), key=lambda w: ((w[1] + w[3]) / 2, w[0])):
                middle = (word[1] + word[3]) / 2
                if not rows or abs(rows[-1][0] - middle) > 3:
                    rows.append((middle, [word]))
                else:
                    rows[-1][1].append(word)
            pages.append('\n'.join(' '.join(w[4] for w in sorted(words, key=lambda w: w[0])) for _, words in rows))
    text = '\n\f\n'.join(pages)
    if not text.strip():
        raise ValueError('This PDF has no selectable text. Scanned statements need OCR and are not supported yet.')
    return text


def parse_pdf(path, issuer=None, closing_date=None):
    result = parse_statement_text(extract_pdf_text(path), issuer, closing_date)
    result.filename = Path(path).name
    result.source_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return result
