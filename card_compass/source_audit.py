"""Source provenance and an explicit, read-only HTTP audit of catalog references.

An issuer-domain link or HTTP success does not verify the terms on a page.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .catalog import SNAPSHOT, read_json, validate_catalog, write_json

ISSUER_DOMAINS = {
    'bank-of-america': ('bankofamerica.com',), 'chase': ('chase.com',),
    'american-express': ('americanexpress.com',), 'citi': ('citi.com', 'citigroup.com'),
    'capital-one': ('capitalone.com',), 'u-s-bank': ('usbank.com',),
    'wells-fargo': ('wellsfargo.com', 'wf.com'), 'barclays': ('barclaycardus.com', 'barclaysus.com'),
    'discover': ('discover.com',), 'penfed-credit-union': ('penfed.org',),
    'alliant-creditunion': ('alliantcreditunion.org',), 'alliant-credit-union': ('alliantcreditunion.org',),
    'navy-federal-credit-union': ('navyfederal.org',), 'usaa': ('usaa.com',),
    'synchrony': ('synchrony.com',), 'td-bank': ('td.com',),
    'pnc-bank': ('pnc.com',), 'sofi': ('sofi.com',), 'brex': ('brex.com',),
    'fidelity': ('fidelity.com',),
}
SECONDARY_DOMAINS = {
    'upgradedpoints.com', 'nerdwallet.com', 'doctorofcredit.com', 'thepointsguy.com',
    'hellosafe.com', 'dailydrop.com', 'frequentmiler.com', 'money.usnews.com',
    'onemileatatime.com', 'cnbc.com', 'awardwallet.com', 'wallethub.com', 'bankrate.com',
    'financebuzz.com', 'uscreditcardguide.com', 'maxrewards.com', 'thriftytraveler.com',
    'finance.yahoo.com', 'cardstack.money', 'flyertalk.com', 'cardratings.com',
    'drcreditcard.net', 'travelupdate.com', 'hustlermoneyblog.com', 'mymoneyblog.com', '10xtravel.com',
}


def on_domain(host, domain):
    return host == domain or host.endswith('.' + domain)


def source_kind(url, issuer):
    if not isinstance(url, str):
        return 'Invalid source'
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        if parsed.scheme != 'https' or not host or parsed.username or parsed.password:
            return 'Invalid source'
    except ValueError:
        return 'Invalid source'
    if any(on_domain(host, d) for d in ISSUER_DOMAINS.get(issuer, ())):
        return 'Issuer domain'
    if any(on_domain(host, d) for d in SECONDARY_DOMAINS):
        return 'Third-party reference'
    return 'Other domain · review needed'


def card_sources(card):
    groups = {
        'card': card.get('sourceMeta', {}).get('sourceUrls', []),
        'offers': card.get('offerSourceMeta', {}).get('sourceUrls', []),
    }
    refs = []
    for group, urls in groups.items():
        refs.extend((group, url) for url in urls)
    for group in ('benefits', 'earningRates', 'offers'):
        refs.extend((group, row['sourceUrl']) for row in card.get(group, []) if row.get('sourceUrl'))
    return sorted(set(refs))


def check_url(url):
    """Fetch a small public response; blocked and unavailable remain unverified."""
    if source_kind(url, '') == 'Invalid source':
        return {'url': url, 'result': 'invalid_url'}
    try:
        request = Request(url, headers={'User-Agent': 'CardCompass/0.1 source-link-audit'})
        with urlopen(request, timeout=10) as response:
            response.read(2048)
            return {'url': url, 'result': 'http_success', 'http_status': response.status,
                    'final_url': response.url, 'content_type': response.headers.get('Content-Type', '')}
    except HTTPError as exc:
        return {'url': url, 'result': 'http_error', 'http_status': exc.code}
    except (URLError, OSError, ValueError) as exc:
        return {'url': url, 'result': 'unavailable', 'error': str(exc)}


def audit(catalog, checker=check_url):
    cards = []
    urls = set()
    for card in catalog['cards']:
        refs = [{'section': section, 'url': url, 'kind': source_kind(url, card['issuerId'])}
                for section, url in card_sources(card)]
        urls.update(ref['url'] for ref in refs)
        cards.append({'id': card['id'], 'name': card['name'], 'issuer': card['issuerId'],
                      'has_issuer_domain_reference': any(ref['kind'] == 'Issuer domain' for ref in refs),
                      'card_review_date': card.get('sourceMeta', {}).get('lastReviewedDate'),
                      'offer_review_date': card.get('offerSourceMeta', {}).get('lastReviewedDate'),
                      'references': refs})
    with ThreadPoolExecutor(max_workers=12) as pool:
        checks = list(pool.map(checker, sorted(urls)))
    return {'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'catalog_fetched_at': catalog.get('fetched_at'),
            'scope': 'Domain provenance and HTTP availability only; individual terms are not verified by this audit.',
            'summary': {'cards': len(cards), 'unique_urls': len(urls),
                        'cards_without_issuer_domain_reference': sum(not c['has_issuer_domain_reference'] for c in cards),
                        'link_results': dict(Counter(c['result'] for c in checks))},
            'cards': cards, 'url_checks': checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', default=str(SNAPSHOT))
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    from pathlib import Path
    report = audit(validate_catalog(read_json(Path(args.catalog))))
    write_json(Path(args.output), report)
    print(json.dumps(report['summary'], indent=2), flush=True)


if __name__ == '__main__':
    main()
