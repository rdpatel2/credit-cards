# Credit-card source audit — 2026-10-07

Checked the live community feed downloaded at 2026-10-07T17:38:51+00:00. All 188 records match the bundled snapshot’s card data. This feed includes Bank of America Customized Cash Rewards; the search bug was in the app, not an absent record.

## Scope and findings

- Audited all 188 card records and 445 unique source URLs, including card, reward, benefit, and offer references.
- Every card has at least one reference. References include issuer websites, co-brand sites, and third-party publications.
- 393 URLs returned HTTP success; 35 returned HTTP errors and 17 were unavailable.
- HTTP errors: 26 access blocks (403), six missing pages (404), two rate limits (429), and one bad request (400). A blocked or timed-out response is inconclusive.
- 38 cards lack a link on a recognized issuer domain under the conservative classifier. Many have co-brand links; this count does not mean 38 cards are invalid.
- HTTP success and domain classification do not verify page contents, product availability, current offers, or all benefit terms. Only the BOFA terms listed below were independently compared with issuer content in this review.

Full per-card provenance, review dates, URL results, and redirects are in [source-audit.json](source-audit.json). Rerun the explicit audit with `python -m card_compass.source_audit --catalog /path/to/catalog.json --output /path/to/report.json`. This is separate from the startup catalog download.

## Bank of America Customized Cash Rewards

Issuer pages checked: [card and public offer](https://www.bankofamerica.com/credit-cards/products/cash-back-credit-card/) and [category rules and exclusions](https://www.bankofamerica.com/credit-cards/products/cash-back-credit-card/cash-back-category-choices/). Both returned HTTP 200 during the link audit.

The $0 annual fee, public $200 bonus for $1,000 spent within 90 days, recurring 3%/2%/1% structure, and shared $2,500 quarterly purchase cap agree with the community record. The issuer also advertises 6% in the selected category during the first year; that introductory earning boost is absent from the feed. The app displays the dated finding and flags that it is excluded from numeric first-year estimates.

The standing choice-category benefit requires selecting a category, rather than separate activation. The app now accepts an explicitly selected category without its generic activation checkbox. Gas and EV charging form one issuer choice, whereas the feed separates their options; the app’s dated review identifies this remaining modeling limit. APRs, all ancillary benefits, applicant eligibility, and every other card’s terms were not independently checked.

## Venture Rewards signup offer — follow-up check on 2026-10-07

Capital One's [Venture Rewards page](https://www.capitalone.com/credit-cards/venture/) labels its public offer limited-time and advertises 75,000 miles after $4,000 in purchases within three months, plus a one-time $300 Capital One Travel Stays credit. The credit applies to hotel and vacation-rental bookings through Capital One Travel and expires one year after account opening. This is distinct from Venture X's recurring annual travel credit. No promotion end date was published in the parsed offer.

The community bonus JSON still lists only 75,000 points. The bundled Venture offer now contains both components with a dated issuer check. Refresh independently fetches this issuer page, conservatively parses supported wording, and marks cached terms unconfirmed if the page cannot be checked. This adapter covers Venture Rewards only; it does not certify the rest of the catalog. The restricted credit is displayed but excluded from ranking estimates.

## Missing-page responses requiring follow-up

These source URLs returned 404 during this audit. The response alone does not establish that the card is discontinued.

| Card | Reference |
| --- | --- |
| AAdvantage Aviator Red World Elite | [Source URL](https://cards.barclaycardus.com/banking/cards/aadvantage-aviator-red-world-elite-mastercard/) |
| Carnival World Mastercard | [Source URL](https://cards.barclaycardus.com/banking/cards/carnival-world-mastercard/) |
| Attune | [Source URL](https://creditcards.wellsfargo.com/attune-world-elite-mastercard-credit-card) |
| Platinum (Morgan Stanley) | [Source URL](https://www.americanexpress.com/us/credit-cards/card/platinum-morgan-stanley/) |
| TD Double Up℠ Credit Card | [Source URL](https://www.td.com/us/en/personal-banking/credit-cards/double-up-card) |
| Walmart OnePay CashRewards | [Source URL](https://www.walmart.com/cp/onepay-cashrewards-mastercard/9569366376) |
