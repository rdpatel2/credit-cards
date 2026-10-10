# Modular dashboard rebuild

## Phase 1: audit and design

The shipped UI is native Python/PySide6. `stocks_ui.py` uses a `QGridLayout` with shared rows, manually placed QWidget panes, custom `DragTitle` mouse handling, a floating thumbnail and slot swapping. There is no DnD library. The separate `stocks/` web project is not the active desktop pane renderer.

Failures are structural. Shared grid rows tie a short pane to its taller neighbor. Content minimum sizes and long footer labels can exceed the configured pane height. Slot swapping cannot express arbitrary placement or insertion. Some actions rebuild the full canvas, resetting component state. There is no edit/view separation, per-breakpoint geometry, coherent history or modular data/component contract.

The replacement uses the established Qt Graphics View scene system with `QGraphicsProxyWidget` for native interactive content. Browser libraries such as react-grid-layout and GridStack would require embedding a second runtime and rebuilding the native finance/data integration. Qt already provides transformed scene items, event forwarding, touch support, animation and widget clipping. A replaceable `LayoutEngine` handles pure grid geometry; the scene only renders its state.

New boundaries: `layout_engine` (12 columns, 10-pixel row units, collision repair and per-column compaction), `layout_store` (history, schema migration and save state), `grid_canvas` (scene interactions), `widget_frame` (header/body/footer and menu), `widget_registry` (component definitions), and `data_layer` (shared cached requests and subscription states). Each dashboard owns settings and a layout for lg/md/sm/xs. The page has one vertical scroll owner; pane tables, editors and overflowing bodies own only their content scrolling.

Old drag/slot-swap/shared-row implementations will be deleted, rather than retained as a fallback. Existing saved finance/trade/note data will be migrated and preserved. Native Qt interaction tests replace the requested browser-only Playwright/Cypress runner because the active application has no DOM.

## Verification

Acceptance checks, migration coverage and phase commit details are recorded here as each phase completes.
