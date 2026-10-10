# Finance Compass usability review — October 8, 2026

The highest priorities are making income easy to change, preserving history when recurring amounts change, and keeping transaction descriptions readable on smaller windows. The original findings below are retained as the audit record. They have now been addressed; see the implementation checklist at the end.

**Review scope and evidence**

Reviewed the native desktop workspace: Overview, Budget & cash flow, all seven Credit cards pages, Preferences, investment dashboards, watchlists/positions, trade history, stock research, and market news. Exercised navigation and income editing with synthetic data, captured every main page at 1380 × 900 and 1040 × 720, and inspected the supporting dialog and persistence code. Checked empty states, imported purchases/refunds, recurring income, and light/dark rendering. Runtime checks used temporary profiles with network refresh disabled, not personal data.

This is a task-based inspection, not a study with representative users. Live market services, real bank files, screen readers, and Windows/macOS rendering were not evaluated. Import behavior was reviewed in the UI code and existing automated workflows. The retained `stocks/` web implementation is outside the native app's runtime and this review.

**High priority**

1. **Changing income is hard to find.** On Overview or Budget, “Expected income” is read-only and the prominent action says “Add monthly income.” To change an existing amount, the user must scroll past charts and the category plan, select an income row under “Income & expenses outside cards,” and click “Edit selected.” In the sample at 1040 × 720, that table begins about 1,039 pixels below the Budget content's top, while the viewport is only 573 pixels tall; it is still farther down on Overview. The add action also makes accidentally creating a second salary plausible. Suggested direction: a “Manage income” action beside the total, with income sources and edit actions immediately visible. Evidence: rendered Budget/Overview and [FinancePage](../card_compass/finance_ui.py), `edit_selected`.

2. **A raise can rewrite historical income.** Editing a recurring entry changes the entire series, including already received months. A synthetic September salary marked received at $4,500 became $5,000 in September's actual income after editing its series amount. There is explanatory text above the entries table, but the edit dialog does not offer “this month,” “this and future months,” or an effective date for the amount change. Preserving history requires ending the old series and adding another. Suggested direction: an explicit effective month and edit scope, defaulting to future occurrences. Evidence: exercised series update; [EntryDialog](../card_compass/finance_ui.py) and [FinanceStore.save_entry](../card_compass/finance.py).

3. **Transaction descriptions disappear at the minimum window size.** In Statements at 1040 × 720, date, amount, category, card name, and one-off columns consume nearly all the width. The first column collapses until the merchant/description and even its header are effectively unreadable. This blocks identifying the transaction to categorize; it is more than a cosmetic issue. Suggested direction: reserve useful width for descriptions and move secondary fields into expandable details or optional columns. Evidence: rendered Statements; [grid and ImportsPage](../card_compass/import_ui.py).

4. **Investment market value can look like a complete portfolio total when it is partial.** The total excludes positions without usable live USD quotes. If some positions have quotes, it still displays a dollar amount; the number of excluded positions is only in a tooltip. The subtitle says which quotes qualify, but users must discover the tooltip to understand coverage. Suggested direction: display “Partial value · 2 of 3 positions priced,” with the missing holdings accessible beside the total. Evidence: code inspection of [StocksPage.render_metrics](../card_compass/stocks_ui.py); live quote failure was not exercised.

**Medium priority — budgeting and shared navigation**

5. **The selected month does not follow the user.** Overview and Budget each maintain their own month. Selecting September in Budget and navigating to Overview left Overview on October in the runtime check. Breakdown separately defaults to all imported dates. Suggested direction: share the month between Overview and Budget, and make Breakdown's different period scope explicit. Evidence: exercised navigation; [FinancePage.period](../card_compass/finance_ui.py), [BreakdownPage.reload](../card_compass/breakdown_ui.py).

6. **Routine monthly bookkeeping takes repeated selection and scrolling.** Recurring income and bills start every month as scheduled. Actual cash flow can therefore show a deficit while expected income looks healthy, until the user finds the rows and marks each received/paid individually. The same “Mark received / paid” button silently acts as an undo when the selected entry is already posted. Suggested direction: visible pending items near the summary, per-row status actions, and labels that state the next action. Evidence: rendered Budget; [FinancePage.toggle_posted](../card_compass/finance_ui.py).

7. **Budget defaults have a surprising connection to credit-card estimates.** Unsaved category targets inherit recommendation spending inputs, and saved targets apply only to their month. A credit-card spending edit can therefore change an unsaved budget target, while moving to a new month does not retain the prior month's explicit targets. “Copy previous month targets” immediately replaces matching targets without a preview or result summary. Suggested direction: label target sources, offer recurring targets, and preview which amounts a copy will replace. Evidence: [FinanceStore.summary and copy_targets](../card_compass/finance.py), [FinancePage.copy_targets](../card_compass/finance_ui.py).

8. **Selection-dependent buttons appear ready when they cannot act.** “Edit selected,” “Mark received / paid,” and “Delete selected series” are enabled without an entry selected; clicking them does nothing. Similar no-selection returns occur in statement and investment actions. Suggested direction: disable unavailable actions with a short selection hint, or put frequent actions on each row. Evidence: verified enabled Budget controls with no selection; corresponding handlers in [finance_ui.py](../card_compass/finance_ui.py), [import_ui.py](../card_compass/import_ui.py), and [stocks_ui.py](../card_compass/stocks_ui.py).

9. **Small windows spend too much space on navigation and summaries.** Expanded Credit cards navigation pushes Preferences and catalog refresh below the sidebar's visible area at 720 pixels tall. Investments uses much of its height for two toolbar rows, summary tiles, status, tabs, and dashboard controls, leaving only a small working area with nested scrollbars. Suggested direction: a collapsible credit-card group, compact summaries, and fewer always-visible utility actions. Evidence: rendered minimum-size pages; [MainWindow.build_ui](../card_compass/app.py), [StocksPage](../card_compass/stocks_ui.py). Scrolling does make these controls reachable.

10. **“Preferences” and “Export” have narrower scopes than their labels suggest.** The global-looking Preferences page only configures card recommendations; investment tax assumptions and view settings are elsewhere. Investment Export saves investment state, not budgets, wallet settings, or statement history, and there is no whole-app backup/restore action. Suggested direction: label these “Card preferences” and “Export investments,” and provide a clearly scoped app backup flow. Evidence: [MainWindow.build_ui](../card_compass/app.py), [StocksPage.export_workspace](../card_compass/stocks_ui.py).

**Medium priority — credit cards**

11. **Correcting statement history does not consistently update recommendations.** Saving a new statement offers the imported-spending preview, which helps. But changing transaction categories, merchant history, linked accounts, or removing an import updates history/Breakdown without replacing the saved recommendation inputs. Users must remember to run “Use imported spending” again. One-off toggles behave differently: they do update inputs automatically when the source is imports. Suggested direction: a persistent “Recommendation inputs need updating” state and a clear apply action after relevant edits. Evidence: [ImportsPage](../card_compass/import_ui.py), `edit_transaction`, `edit_rule`, `edit_account`, `remove_import`, and `one_off_changed`.

12. **Finding and investigating a purchase requires manual navigation.** Statements has no merchant search, month/card filters, or visible sort controls. Breakdown filters and aggregates spending, but clicking a category or merchant cannot open its underlying purchases. Suggested direction: transaction search/filtering and a drill-through from Breakdown that preserves the selected period/card. Evidence: [ImportsPage](../card_compass/import_ui.py), [BreakdownPage](../card_compass/breakdown_ui.py). This becomes more painful as imported history grows.

13. **Coverage gives conflicting visual signals and hides the best card.** With the default 2% target, 2% coverage is “Covered” on Coverage but yellow on Spending, whose color thresholds are fixed at 1% and 3%. The card supplying the effective rate is only in a table tooltip. Returning a manual rate to “Auto” also relies on a special numeric-field value rather than an explicit mode choice. Suggested direction: align colors with the configured target, display the recommended card, and provide an Auto/manual switch. Evidence: rendered 2% example; [MainWindow.color_spending_coverage, recalculate, and add_spend_row](../card_compass/app.py).

14. **Recommendation warnings are difficult to resolve.** “2 wallet assumptions need review in Card settings” does not name the affected cards or link to their settings. The strongest-match dollar figure is much more prominent than the assumptions behind it, including point values. Suggested direction: specific warnings beside the affected card, direct settings actions, and a short value calculation available beside the result. Evidence: rendered Matches; [MainWindow.recalculate](../card_compass/app.py).

15. **Browsing cards opens a separate window as soon as selection changes.** Both search results and recommendations open details on selection, not just an explicit open action. That can interrupt scanning and keyboard navigation through a list. Suggested direction: use an explicit open action or a persistent preview pane. Evidence: selection signals in [MainWindow.build_ui](../card_compass/app.py) and existing desktop interaction tests.

16. **First-time CSV import asks several technical questions at once.** The mapping dialog presents required and optional columns together, all with “Not supplied,” plus an amount convention the user must choose. The small raw preview helps, but does not directly show the resulting purchase/refund interpretation before proceeding. Suggested direction: identify required fields clearly, place optional mappings behind an expansion, and show a translated purchase/refund example. Keep explicit confirmation of the amount convention. Evidence: code inspection of [CsvMappingDialog](../card_compass/import_ui.py).

**Medium priority — investments**

17. **Widget configuration exposes unrelated settings.** A price chart's Configure dialog includes Watchlist, Analyst scope, News scope, Row limit, and Earnings date alongside chart settings. Position and size require grid coordinates. This makes ordinary customization harder to understand and increases the chance of changing an irrelevant control. Suggested direction: show controls relevant to the widget type, with layout coordinates under advanced settings. Evidence: opened the chart configuration dialog and inspected its labels; [WidgetDialog](../card_compass/stocks_ui.py).

18. **Research edits can affect other stocks without an obvious save scope.** “Customize only this stock” is unchecked for the shared template; the explanation that unchecked edits affect the shared template is in a tooltip. A user arranging the AAPL research page could reasonably expect the change to stay with AAPL. Suggested direction: show “Editing shared stock layout” prominently and offer an explicit shared/this-stock choice. Evidence: [ResearchPanel and StocksPage.customize_stock](../card_compass/stocks_ui.py).

19. **Sale tax setup is separated from the sale workflow.** “What if I sell?” is available from positions and stock research, but “Tax assumptions” is in Trade history. When assumptions are missing, the result explains that setup is needed without an action that opens it. Suggested direction: link to assumptions directly from the sale dialog/result and return to the scenario after saving. Evidence: [StocksPage.tax_settings and sale_scenario](../card_compass/stocks_ui.py).

20. **Removing a widget is immediate and has no undo.** The small × next to Configure immediately removes the widget. Removing a notes widget makes its notes inaccessible through that widget; there is no visible recovery action. Suggested direction: undo for widget removal, with special care for widgets containing user notes. Evidence: [StocksPage.remove_widget](../card_compass/stocks_ui.py). This finding concerns recoverability in the UI, not a claim that all stored note text is deleted.

**Suggested order for a later implementation pass**

Start with income access and effective-date editing, then transaction readability/search, then clear month and budget-source behavior. Next address stale recommendation inputs and partial investment valuations. Keep the remaining navigation and customization changes in a separate pass so they can be evaluated as complete user flows.

**Dark-mode work completed separately**

The working tree already contained theme-aware Breakdown chart labels, bars, and tracks. Those rendered correctly in the local checks, so the originally reported visual failure was not reproduced on this Linux offscreen setup. This change gives Breakdown's scroll content the shared `scrollContent` background treatment already used by Overview and Budget. Repeated light/dark switches, saved-dark reopening, populated charts/refunds, and minimum-size rendering were checked. This does not establish whether an older installed build or another platform still exhibits the original symptom.

Validation: the full Python test suite passed (178 tests), and `git diff --check` reported no whitespace errors.


## Implementation checklist — October 9, 2026

| Finding | Implemented behavior |
| --- | --- |
| 1 | Manage income at the top of Overview and Budget; direct edits for each source. |
| 2 | Effective-month editing defaults to current/future occurrences; this-month-only splits a series atomically; earlier postings are preserved. |
| 3 | Readable transaction description width; optional secondary columns; complete purchase details. |
| 4 | Partial market values visibly show priced/total positions, missing tickers and a details action. |
| 5 | Shared, saved finance month with dropdown, year and visible previous/next controls. |
| 6 | Pending status near totals, per-row received/paid actions and explicit undo labels. |
| 7 | Independent repeating budgets and monthly overrides, source labels and copy preview. |
| 8 | Selection-dependent actions disabled until a row is selected. |
| 9 | Collapsible card navigation, fixed sidebar utility actions, compact metrics and tabbed finance tasks. |
| 10 | Card preferences, investment-scoped import/export labels, validated full-app backup and restore with a recovery copy. |
| 11 | Persistent update action for changed imported recommendation inputs, including after restart. |
| 12 | Merchant/description search, month/card filters, sorting and Breakdown drill-through. |
| 13 | Target-aware coverage colors, visible card providers and explicit Auto/manual choice. Auto uses a single field. |
| 14 | Named card assumptions with direct settings links and selected-match calculation details. |
| 15 | Selection alone leaves details closed; Open, double-click or Enter opens a card. |
| 16 | Required CSV mappings separated from optional fields; interpreted purchase/refund examples. |
| 17 | Widget-specific controls; advanced grid coordinates hidden until requested. |
| 18 | Visible shared/per-stock editing scope on research pages. |
| 19 | Tax setup available inside a sale scenario without discarding its inputs. |
| 20 | Undo widget removal restores the widget, layout and saved notes within the session. |

The follow-up visual review also added wide dashboard drag headers with floating previews and stable tile targets; a persistent vertical splitter for news headlines; inset margins in every investment tab and dashboard; correctly escaped ampersands in native controls; and compact Breakdown and Preferences layouts. A first-plan builder creates an optional starting budget from four familiar amounts without bank linking or reading documentation.

The ten-minute goal is supported by a short first-run flow and automated end-to-end task checks, rather than a timed study with novice participants. Platform validation remains Linux offscreen; Windows/macOS native rendering and live market endpoints still need independent testing.

Validation after implementation: all 196 Python tests passed, including 18 new usability regression tests. The desktop smoke test also passed. Final visual refinements passed 57 affected UI tests, with another 21-test finance/usability run after simplifying the first-run screen. At 1040 × 720, the populated Overview, Budget, Breakdown, Card preferences and card category browser fit without whole-page scrolling in both themes. Long transaction lists and configurable investment dashboards retain their own scrolling regions.

Representative screenshots: [Budget](usability-budget.png), [investment dashboard](usability-investments.png), [resizable news](usability-news.png), and [single-field automatic coverage](usability-coverage.png).

The subsequent investment movement fix keeps all real tiles and the page extent stationary while dragging. A floating copy follows the pointer; occupied tiles show a named swap target and available spaces show outlined drop squares. Movement commits on release using the existing frames, preserving notes and table state. Edge scrolling is bounded by the existing page. Escape, tab changes, focus loss and resizing clear the preview. Different-sized tiles pack into rows when a direct swap cannot fit. Matches now uses 104-pixel rows for 80-pixel artwork, leaving 24 pixels between adjacent card images.

Verification: the full 211-test suite passed. After the final in-place placement refinement, all 47 affected UI tests passed. The latest 16 movement and image-spacing tests also passed, including forty consecutive pointer drags without rebuilding tiles or accumulating page height. Coverage includes home/custom dashboards, shared/per-stock research, sector news, empty-slot placement, cancellation, persistence and continued note editing after a drop. Light/dark screenshots were inspected on Linux offscreen: [dragging a tile](usability-investment-drag.png) and [Matches image spacing](usability-matches.png).
