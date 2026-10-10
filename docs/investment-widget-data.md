# Investment widget data and chart interaction

The active app is Python/PySide6. The widget registry and shared data layer remain independent of dashboard geometry.

## Data audit

| Widgets | Required data | Checked behavior |
|---|---|---|
| Watchlist, positions, movers | Daily quotes for every ticker in the chosen watchlist; saved trades for shares and basis | Symbol subscriptions, sorting, quote coverage, per-row loading/error/cached details and local holdings. |
| Market overview | SPY, QQQ, DIA and IWM daily quotes | All four benchmark requests succeed and each row consumes its own quote. |
| Price chart, key statistics | Selected ticker/range history; daily price, change, volume and trading ranges | All five supported ranges, historical point validation, currency and exchange timezone. |
| Advanced metrics | Company fundamentals with quote/reporting currencies | Percentage/money/ratio formatting; missing values remain unavailable with an explanation. Entirely missing selections have an actionable empty state. |
| Analyst trend | Four recommendation periods for the selected stock, or every ticker in its chosen watchlist | Count aggregation, weighted scores, partial coverage and cached/demo rendering. |
| News, market headlines | Company RSS or merged market feeds | Company news now has the same inferred sector tags as market news; search and sector filters consume those tags. |
| Earnings calendar | Events for the configured date | Report time, forecast EPS and full provider event metadata; no silent truncation at 100 events. Hover a cell for estimates, prior-year EPS/report date, fiscal quarter and market cap. Double-click opens stock research. |
| Recent trades, ledger, notes | Local investment state | All required records render without a network request; note text and trade IDs remain independent of layout. |

The provider's JSON/RSS-only `Accept` header caused HTTP 406 at Yahoo's plain-text session/crumb endpoint. `Accept: */*` fixes the actual negotiation failure. The live audit succeeded for 20 requests covering the default watchlist, benchmarks, all chart ranges, five companies' fundamentals, company/market news and the earnings calendar. One AAPL check supplied 19 of 20 configured metrics; price-to-sales was absent. The UI reports absent fields rather than inventing values.

Switching public/demo mode now invalidates the previous request generation, clears old failures and queues the current subscriptions. Late results cannot overwrite the new mode. Synthetic preview data includes fundamentals, analyst periods and earnings so those widgets can be explored; it remains labeled Demo and excluded from personal portfolio valuation.

## Interactive chart

Hover within the plot to see a crosshair, selected marker and exact recorded date/price. Selection follows timestamps, including uneven trading-day spacing. Intraday readouts include exchange-local time and timezone; unavailable timezone data falls back explicitly to UTC. Click or tap to inspect, or focus the chart and use Left/Right and Home/End. Escape or leaving the chart clears the readout. The chart remains non-interactive during layout editing.

Points are sorted and duplicate timestamps consolidated before painting. Empty, single-point, flat-price and narrow charts are supported. Updating identical data preserves selection; changing the series clears it.

## Credit-card adjustments

Spending defaults to monthly amount descending, with the visible sort indicator. Clicking that header reverses the order; numeric sorting preserves each category's amount and coverage controls.

The Statements transaction description column expands into the available table width. Dates are centered and currency amounts right-aligned. Card/one-off columns can still be revealed, and full purchase details remain available in tooltips.

## Verification

Deterministic tests exercise all 14 registered widget types, data-mode races, provider contracts, missing fields, sector filters, hover through the real dashboard viewport, keyboard/touch inspection and both credit-card adjustments. Live public-provider checks are separate from unit tests so network availability cannot make the regression suite flaky. Native previews were inspected after the changes.

Final full-suite result: **278 passed in 170.03 seconds** using `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`. Live checks succeeded for all 20 requests. Tests use generated Qt input on Linux; physical-device and other-platform interaction has not been manually verified.
