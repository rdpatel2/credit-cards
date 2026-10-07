# Card Compass

A native desktop app for choosing your next US credit card. Runs on Windows, macOS, and Linux using Python and Qt. There is no web server.

[Preview the desktop workspace](docs/gui-preview.png) (sample wallet and spending).

- Search cards, benefits, earning categories, annual fees, and published signup offers.
- Add your existing cards to a saved wallet, including manually entered cards.
- Enter monthly spending; Coverage puts your highest spending categories first.
- Infer coverage from your wallet, or override a category's cash-equivalent rate. Set **0%** for an uncovered category and **Auto** to derive it from cards.
- Compare **additional** annual rewards against your existing wallet, after the new card's fee. Recurring and first-year estimates are separate.
- Refresh the public catalog in the background on every startup. Offline starts use the last successful cache, then the bundled snapshot.

## Run

Install Python 3.10 or newer. From this directory:

```sh
python -m venv .venv
```

Activate the environment:

```sh
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

Then:

```sh
python -m pip install -e .
python -m card_compass
```

The installed `card-compass` command also launches the app. Linux requires a desktop session and Qt system libraries; on Ubuntu install `libegl1 libopengl0 libxcb-cursor0 libxkbcommon-x11-0` if Qt reports missing platform libraries. If you prefer not to activate a virtual environment, use `.venv/bin/python` on macOS/Linux or `.venv\Scripts\python.exe` on Windows directly.

## Use

1. In **Explore cards**, search for each card you own, select it, and click **Add to wallet**. Use **My wallet → Add manually** for cards missing from the catalog.
2. In **Spending**, enter monthly purchases in separate buckets. **Add a category** includes merchant and travel-portal categories. Move that spending out of other buckets to avoid double counting.
3. Use **Coverage** to see gaps against your adjustable target, initially 2%. Manual coverage entries replace inferred wallet coverage for that category.
4. Compare **Your matches**, which highlights your strongest match and its incremental value. Choose ongoing or first-year sorting, and use **Preferences** to set your maximum annual fee, point values, coverage target, and bonus assumptions.
5. Adjust point values, selections, and activation in **Card settings**. The default assumption is **1 cent per point/mile across all programs**; change it to your actual redemption values, especially for hotel points.
6. Signup offers are displayed automatically. Enable **Count public signup bonuses** only if you expect to qualify. Only public, unexpired offers with spend requirements your stated spending pace can meet enter the first-year estimate. Alternative offers are compared individually; they are not added together.

The desktop workspace uses dedicated screens for matches, wallet, spending, coverage, catalog browsing, and preferences. Card details open in a separate window with Overview, Offers, Benefits, and Sources tabs. Click a card or press Enter to open its details; Escape closes the detail window. The interface supports a minimum window size of 1040 × 720, with scrollable settings and spending screens. Card illustrations and navigation icons are drawn as scalable vectors; illustrations are identifiers rather than official issuer artwork.

## Data and calculation limits

Catalog data comes from [fuermosi777/rewards](https://github.com/fuermosi777/rewards), a public community-maintained database with issuer source links and review dates. The app downloads card and bonus JSON; it does not scrape or independently verify issuer pages. A fresh download may contain old reviews. Check the issuer links before applying. The bundled snapshot's download timestamp is stored in `card_compass/data/snapshot.json`.

The allocation solver maximizes wallet rewards subject to category spending and shared caps. Estimates assume steady monthly spending and annualize monthly/quarterly caps; existing cap usage and changes during the year are not modeled. Category aliases cover a small set of synonymous labels; issuer eligibility and exclusions still apply. Restricted merchant and portal categories remain distinct. Broad travel categories are matched exactly and do not automatically include separate airfare/hotel buckets.

Selection-dependent bonuses are excluded until configured. Automatic highest-category bonuses assume your selected categories will be the highest on that particular card each cycle. Rotating bonuses appear in details but are excluded from annual estimates; an old quarter is marked unresolved. The adapter handles cross-record shared caps for Bank of America Customized Cash and Citi Costco, and corrects AAA Daily Advantage's reward-denominated cap from its source terms. Other caps follow their source records; incomplete data cannot guarantee an exhaustive optimization.

Signup spending feasibility uses total monthly spending × days / 30. It assumes that spending can be charged to the new card; excluded purchases, issuer eligibility, and the reward opportunity cost of rerouting spending to earn a bonus are not modeled. First-year figures use the annual fee waiver in the source and count one qualifying public offer if enabled. Statement credits, memberships, free nights, interest, taxes, and approval odds are excluded from numeric rankings. Benefits and their terms are listed for your own evaluation. Existing wallet annual fees are common to both scenarios and cancel out of the comparison.

Your wallet and spending stay on your device. Refresh requests send no profile information; opening an issuer source link launches your browser. Local files:

| OS | Location |
| --- | --- |
| Windows | `%LOCALAPPDATA%\CardCompass` |
| macOS | `~/Library/Application Support/CardCompass` |
| Linux | `$XDG_DATA_HOME/CardCompass` or `~/.local/share/CardCompass` |

`profile.json` holds preferences, wallet and spending; `catalog.json` holds the last complete download. Failed refreshes preserve the old catalog. A close during refresh cancels pending requests and waits for in-flight requests to finish (up to their network timeout). Custom cards are preserved across catalog refreshes.

## Test and build

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python -m PyInstaller --noconfirm --windowed --onedir --name CardCompass --collect-data card_compass run_app.py
```

To exercise a packaged app without live requests or writing a real profile, run it with `--smoke-test`. On Linux a headless check is `QT_QPA_PLATFORM=offscreen dist/CardCompass/CardCompass --smoke-test`.

Distributables are in `dist/`. Build on each target operating system: PyInstaller does not cross-compile. Windows produces an `.exe`, macOS an `.app`, and Linux a native executable directory. Keep all files in the generated directory together. These are unsigned development builds; signing/notarization is a separate distribution step.

`.github/workflows/desktop.yml` runs tests and creates downloadable build artifacts for all three platforms when pushed to GitHub or triggered manually. Local validation covers Linux offscreen UI workflows; Windows/macOS validation remains pending until those jobs run.

## Implementation history

See [IMPLEMENTATION.md](IMPLEMENTATION.md). The branches form a stack: `feat/catalog` → `feat/recommendations` → `feat/desktop` → `chore/release`. Each branch retains its completed milestone. `main` is advanced to the fully verified result after the release step. No remote push is required to run locally.

The desktop redesign is on `feat/gui-redesign`, with separate commits for the visual components, task-based workspace and workflow tests, and documentation/preview. The completed redesign is integrated into `main`.
