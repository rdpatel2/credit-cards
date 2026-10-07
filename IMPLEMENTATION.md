# Card Compass implementation

Native desktop application for US credit cards, supporting Windows, macOS, and Linux.

Work proceeds on stacked branches; each step builds on the preceding commit.

1. `feat/catalog`: Python package, live source adapter, validated local cache, profile storage, offline snapshot, data tests.
2. `feat/recommendations`: wallet coverage, spend-based incremental reward estimates, annual fees, point values, offer affordability, calculation tests.
3. `feat/desktop`: Qt desktop interface, wallet editor, spending and coverage inputs, card search, recommendations, benefit and offer details, asynchronous startup refresh.
4. `chore/release`: desktop smoke tests, cross-platform launch/build instructions, final verification and integration into `main`.

## Product decisions

- User information stays on the device. No bank login or card numbers.
- Fetch published source data on every startup in a background worker. Keep the last successful dataset when offline.
- Fetch time and source review time are distinct and visible. Refreshing a community feed does not verify each issuer's current offer.
- Compare a new card to the best rewards already available in the wallet. Show recurring value separately from first-year value.
- Incomplete, activation-dependent, rotating, and selected categories need explicit treatment; never assume every menu category earns at once.
- Benefits are shown with their terms, not automatically converted into cash value.
- Use native Qt widgets, with no web server or browser-based UI.

## Validation

Run data and recommendation tests, then an offscreen Qt workflow covering spending edits, wallet changes, filtering, profile persistence, and refresh error recovery. Live-download validation is separate from offline deterministic tests. Build distributable executables on each target OS using PyInstaller.
