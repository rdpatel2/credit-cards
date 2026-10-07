from card_compass.catalog import read_json, SNAPSHOT
from card_compass.search import matches_card
from card_compass.source_audit import audit, source_kind
from card_compass.engine import rules_for


def customized():
    return next(c for c in read_json(SNAPSHOT)['cards'] if c['id'] == 'bank-of-america-customized-cash-rewards')


def test_full_issuer_card_name_and_aliases_are_searchable():
    card = customized()
    for query in ('Bank of America customized cash card', 'BOFA customized',
                  'cash rewards bank-of-america', 'boa rewards customized', 'customized   CASH'):
        assert matches_card(card, query)
    assert not matches_card(card, 'amex customized cash')


def test_unrelated_source_domains_do_not_pollute_card_search():
    card = {'id': 'sample', 'name': 'Sample', 'issuerId': 'chase',
            'benefits': [{'name': 'Travel', 'sourceUrl': 'https://example.com/bank-of-america'}]}
    assert not matches_card(card, 'bank of america')
    assert matches_card(card, 'chase travel')


def test_provenance_distinguishes_issuer_third_party_and_unknown():
    assert source_kind('https://www.bankofamerica.com/credit-cards/', 'bank-of-america') == 'Issuer domain'
    assert source_kind('https://www.nerdwallet.com/reviews', 'bank-of-america') == 'Third-party reference'
    assert source_kind('https://bankofamerica.com.attacker.example/', 'bank-of-america') == 'Other domain · review needed'
    assert source_kind('https://www.chase.com/', 'bank-of-america') == 'Other domain · review needed'
    assert source_kind('http://www.bankofamerica.com/', 'bank-of-america') == 'Invalid source'


def test_audit_checks_unique_urls_without_claiming_terms_verified():
    card = customized()
    requested = []
    def checker(url):
        requested.append(url)
        return {'url': url, 'result': 'http_error', 'http_status': 403}
    report = audit({'cards': [card], 'fetched_at': '2026-10-07'}, checker)
    assert len(requested) == 2
    assert report['summary']['unique_urls'] == 2
    assert report['summary']['link_results'] == {'http_error': 2}
    assert report['cards'][0]['has_issuer_domain_reference']
    assert 'terms are not verified' in report['scope']


def test_customized_choice_requires_selection_but_not_activation():
    card = customized()
    ident = card['benefits'][0]['id']
    rules, warnings = rules_for(card, {'card_settings': {card['id']: {'selections': {ident: ['dining']}}}})
    assert any('dining' in r.categories and r.rate == .03 for r in rules)
    assert not any('activation' in warning for warning in warnings)
    assert any('First-year 6%' in warning for warning in warnings)
    assert not any(r.rate == .06 for r in rules)
