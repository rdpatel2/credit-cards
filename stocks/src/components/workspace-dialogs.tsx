"use client";
import { useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Check,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { Modal } from "./modal";
import { companies } from "@/lib/market/demo";
import {
  metrics,
  metricIds,
  defaultMetrics,
  sectorIds,
  sectorLabels,
  type Sector,
} from "@/lib/market/research";
import {
  parseWorkspace,
  ranges,
  symbolSchema,
  widgetCatalog,
  widgetTypes,
  type LayoutItem,
  type Widget,
  type WidgetType,
  type Workspace,
} from "@/lib/workspace";

export function SearchDialog({
  onClose,
  onSelect,
}: {
  onClose: () => void;
  onSelect: (symbol: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const results = Object.entries(companies)
    .filter(([symbol, info]) =>
      `${symbol} ${info[0]}`.toLowerCase().includes(query.toLowerCase()),
    )
    .slice(0, 9);
  const select = (symbol: string) => {
    const parsed = symbolSchema.safeParse(symbol);
    if (!parsed.success) return setError("Enter a valid US ticker symbol.");
    onSelect(parsed.data);
    onClose();
  };
  return (
    <Modal
      title="Find your next idea"
      subtitle="Search popular companies or enter any US stock or ETF ticker."
      onClose={onClose}
    >
      <form
        className="search-field"
        onSubmit={(e) => {
          e.preventDefault();
          select(query);
        }}
      >
        <Search size={20} />
        <input
          autoFocus
          aria-label="Search stocks"
          placeholder="Search symbol or company…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <kbd>↵</kbd>
      </form>
      {error && <p className="field-error">{error}</p>}
      <div className="search-results">
        {results.map(([symbol, [name]]) => (
          <button key={symbol} onClick={() => select(symbol)}>
            <span className={`company-icon color-${symbol.charCodeAt(0) % 5}`}>
              {symbol[0]}
            </span>
            <span>
              <strong>{symbol}</strong>
              <small>{name}</small>
            </span>
            <span className="muted">US equity / ETF ↗</span>
          </button>
        ))}
      </div>
      {query && symbolSchema.safeParse(query).success && (
        <button className="button search-direct" onClick={() => select(query)}>
          Open {query.toUpperCase()} →
        </button>
      )}
      <p className="help-text">
        Symbols outside the demo catalog require an available public data feed.
      </p>
    </Modal>
  );
}
export function WidgetLibrary({
  onClose,
  onAdd,
}: {
  onClose: () => void;
  onAdd: (type: WidgetType) => void;
}) {
  return (
    <Modal
      title="Make this space yours"
      subtitle="Choose a widget. Place it anywhere. Change it anytime."
      wide
      onClose={onClose}
    >
      <div className="widget-library">
        {widgetTypes.map((type, i) => (
          <button
            key={type}
            className="library-card"
            onClick={() => onAdd(type)}
          >
            <div className={`mini-preview preview-${type}`}>
              <span className="preview-label">
                {String(i + 1).padStart(2, "0")} /{" "}
                {widgetCatalog[type].category}
              </span>
              <div className="preview-lines">
                <i />
                <i />
                <i />
                {type === "chart" && (
                  <svg viewBox="0 0 150 35">
                    <path
                      d="M0 30 L15 22 L26 27 L40 12 L53 20 L67 17 L79 24 L91 8 L104 15 L118 5 L132 10 L150 1"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    />
                  </svg>
                )}
              </div>
            </div>
            <div className="library-title">
              <h3>{widgetCatalog[type].name}</h3>
              <Plus size={17} />
            </div>
            <p>{widgetCatalog[type].description}</p>
          </button>
        ))}
      </div>
    </Modal>
  );
}
export function WidgetSettings({
  widget,
  position,
  workspace,
  onClose,
  onSave,
}: {
  widget: Widget;
  position: LayoutItem;
  workspace: Workspace;
  onClose: () => void;
  onSave: (widget: Widget, position: LayoutItem) => void;
}) {
  const [draft, setDraft] = useState(widget);
  const [layout, setLayout] = useState(position);
  const [error, setError] = useState("");
  const hasSymbol =
    ["chart", "quote", "metrics", "analyst"].includes(widget.type) ||
    (widget.type === "news" && draft.newsScope === "company");
  return (
    <Modal
      title="Widget settings"
      subtitle={`Customize ${widgetCatalog[widget.type].name.toLowerCase()}. Changes apply to this widget.`}
      onClose={onClose}
    >
      <form
        className="form-stack"
        onSubmit={(e) => {
          e.preventDefault();
          const parsed = symbolSchema.safeParse(draft.symbol);
          if (!parsed.success) return setError("Enter a valid ticker.");
          onSave(
            { ...draft, symbol: parsed.data },
            { ...layout, x: Math.min(layout.x, 12 - layout.w) },
          );
          onClose();
        }}
      >
        <label>
          Title
          <input
            required
            maxLength={80}
            value={draft.title}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          />
        </label>
        {hasSymbol && (
          <>
            <label>
              Default ticker
              <input
                required
                aria-label="Default ticker"
                maxLength={15}
                value={draft.symbol}
                onChange={(e) =>
                  setDraft({ ...draft, symbol: e.target.value.toUpperCase() })
                }
              />
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={draft.followSymbol}
                onChange={(e) =>
                  setDraft({ ...draft, followSymbol: e.target.checked })
                }
              />
              Follow the page ticker on stock pages
            </label>
          </>
        )}
        {widget.type === "metrics" && (
          <fieldset className="metric-picker">
            <legend>Visible metrics</legend>
            <div className="metric-actions">
              <button
                type="button"
                className="text-button"
                onClick={() => setDraft({ ...draft, metrics: [...metricIds] })}
              >
                Select all
              </button>
              <button
                type="button"
                className="text-button"
                onClick={() => setDraft({ ...draft, metrics: [] })}
              >
                Clear all
              </button>
              <button
                type="button"
                className="text-button"
                onClick={() =>
                  setDraft({ ...draft, metrics: [...defaultMetrics] })
                }
              >
                Defaults
              </button>
            </div>
            <div className="metric-checkboxes">
              {metricIds.map((id) => (
                <label
                  key={id}
                  className="checkbox-label"
                  title={metrics[id].description}
                >
                  <input
                    type="checkbox"
                    checked={draft.metrics.includes(id)}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        metrics: e.target.checked
                          ? [...draft.metrics, id]
                          : draft.metrics.filter((metric) => metric !== id),
                      })
                    }
                  />
                  {metrics[id].label}
                </label>
              ))}
            </div>
          </fieldset>
        )}
        {widget.type === "news" && (
          <>
            <label>
              News coverage
              <select
                aria-label="News coverage"
                value={draft.newsScope}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    newsScope: e.target.value as Widget["newsScope"],
                  })
                }
              >
                <option value="company">This company</option>
                <option value="market">Whole market / sector</option>
              </select>
            </label>
            {draft.newsScope === "market" && (
              <>
                <label>
                  Sector
                  <select
                    aria-label="News sector"
                    value={draft.sector}
                    onChange={(e) =>
                      setDraft({ ...draft, sector: e.target.value as Sector })
                    }
                  >
                    {sectorIds.map((sector) => (
                      <option key={sector} value={sector}>
                        {sectorLabels[sector]}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Article view
                  <select
                    aria-label="Article view"
                    value={draft.newsView}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        newsView: e.target.value as Widget["newsView"],
                      })
                    }
                  >
                    <option value="cards">Cards</option>
                    <option value="list">Headlines</option>
                    <option value="sectors">Grouped by sector</option>
                  </select>
                </label>
              </>
            )}
          </>
        )}
        {widget.type === "chart" && (
          <label>
            Time range
            <select
              value={draft.range}
              onChange={(e) =>
                setDraft({ ...draft, range: e.target.value as Widget["range"] })
              }
            >
              {ranges.map((range) => (
                <option key={range}>{range}</option>
              ))}
            </select>
          </label>
        )}
        {widget.type === "analyst" && (
          <label>
            Analyst coverage
            <select
              value={draft.analystScope}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  analystScope: e.target.value as Widget["analystScope"],
                })
              }
            >
              <option value="stock">Stock consensus</option>
              <option value="watchlist">Market proxy (watchlist)</option>
            </select>
          </label>
        )}
        {["watchlist", "movers", "market", "analyst"].includes(widget.type) && (
          <label>
            Watchlist
            <select
              value={draft.watchlistId}
              onChange={(e) =>
                setDraft({ ...draft, watchlistId: e.target.value })
              }
            >
              {workspace.watchlists.map((list) => (
                <option key={list.id} value={list.id}>
                  {list.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {widget.type === "watchlist" && (
          <>
            <label>
              Sort by
              <select
                value={draft.sort}
                onChange={(e) =>
                  setDraft({ ...draft, sort: e.target.value as Widget["sort"] })
                }
              >
                <option value="symbol">Symbol A–Z</option>
                <option value="price">Highest price</option>
                <option value="change">Largest daily gain</option>
              </select>
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={draft.showChart}
                onChange={(e) =>
                  setDraft({ ...draft, showChart: e.target.checked })
                }
              />
              Show price trends
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={draft.showVolume}
                onChange={(e) =>
                  setDraft({ ...draft, showVolume: e.target.checked })
                }
              />
              Show volume
            </label>
          </>
        )}
        {["movers", "market", "news", "earnings"].includes(widget.type) && (
          <label>
            Maximum rows
            <input
              type="number"
              min={1}
              max={
                draft.type === "news" && draft.newsScope === "market" ? 120 : 20
              }
              value={draft.limit}
              onChange={(e) =>
                setDraft({ ...draft, limit: Number(e.target.value) })
              }
            />
          </label>
        )}
        <div className="form-divider">
          Grid position <span>12 columns · Also adjustable by dragging</span>
        </div>
        <div className="form-grid">
          {(
            [
              ["x", "Column (0–11)", 0, 10],
              ["y", "Row", 0, 10000],
              ["w", "Width", 2, 12],
              ["h", "Height", 3, 30],
            ] as const
          ).map(([key, label, min, max]) => (
            <label key={key}>
              {label}
              <input
                aria-label={label}
                type="number"
                required
                min={min}
                max={max}
                value={layout[key]}
                onChange={(e) =>
                  setLayout({ ...layout, [key]: Number(e.target.value) })
                }
              />
            </label>
          ))}
        </div>
        {error && (
          <p role="alert" className="field-error">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button primary" type="submit">
            <Check size={15} />
            Save settings
          </button>
        </div>
      </form>
    </Modal>
  );
}
export function NameDialog({
  title,
  initial = "",
  onClose,
  onSave,
}: {
  title: string;
  initial?: string;
  onClose: () => void;
  onSave: (name: string) => void;
}) {
  const [name, setName] = useState(initial);
  return (
    <Modal title={title} onClose={onClose}>
      <form
        className="form-stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) {
            onSave(name.trim());
            onClose();
          }
        }}
      >
        <label>
          Name
          <input
            autoFocus
            aria-label="Name"
            required
            maxLength={60}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Long-term ideas"
          />
        </label>
        <div className="modal-actions">
          <button type="button" className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button primary" type="submit">
            Save
          </button>
        </div>
      </form>
    </Modal>
  );
}
export function WorkspaceSettings({
  workspace,
  update,
  onClose,
  notify,
}: {
  workspace: Workspace;
  update: (change: Workspace | ((state: Workspace) => Workspace)) => void;
  onClose: () => void;
  notify: (message: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  function exportWorkspace() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(workspace, null, 2)], {
        type: "application/json",
      }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "folio-workspace.json";
    anchor.click();
    URL.revokeObjectURL(url);
    notify("Workspace exported");
  }
  async function importWorkspace(file?: File) {
    if (!file) return;
    try {
      if (file.size > 2_000_000)
        throw new Error("Workspace file is too large (maximum 2 MB)");
      const parsed = parseWorkspace(await file.text());
      if (
        confirm(
          "Replace your current workspace with this file? Export first if you want to keep the current version.",
        )
      ) {
        update(parsed);
        notify("Workspace imported");
        onClose();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not import workspace");
    }
    if (fileRef.current) fileRef.current.value = "";
  }
  function deleteList(id: string) {
    if (
      workspace.watchlists.length <= 1 ||
      !confirm(
        "Delete this watchlist? Widgets using it will switch to the first remaining list.",
      )
    )
      return;
    update((state) => {
      const watchlists = state.watchlists.filter((list) => list.id !== id);
      const repair = (page: Workspace["stockTemplate"]) => ({
        ...page,
        widgets: page.widgets.map((w) =>
          w.watchlistId === id ? { ...w, watchlistId: watchlists[0].id } : w,
        ),
      });
      return {
        ...state,
        watchlists,
        pages: state.pages.map(repair),
        stockTemplate: repair(state.stockTemplate),
        newsPages: Object.fromEntries(
          Object.entries(state.newsPages).map(([sector, page]) => [
            sector,
            repair(page),
          ]),
        ),
        stockOverrides: Object.fromEntries(
          Object.entries(state.stockOverrides).map(([symbol, page]) => [
            symbol,
            repair(page),
          ]),
        ),
      };
    });
  }
  return (
    <Modal
      title="Workspace settings"
      subtitle="Your preferences, lists, and saved layouts. All stored in this browser."
      onClose={onClose}
    >
      <div className="form-stack">
        <label>
          Appearance
          <select
            aria-label="Appearance"
            value={workspace.theme}
            onChange={(e) =>
              update((s) => ({
                ...s,
                theme: e.target.value as Workspace["theme"],
              }))
            }
          >
            <option value="dark">Dark</option>
            <option value="light">Light</option>
          </select>
        </label>
        <label>
          Market data
          <select
            aria-label="Market data mode"
            value={workspace.dataMode}
            onChange={(e) =>
              update((s) => ({
                ...s,
                dataMode: e.target.value as Workspace["dataMode"],
              }))
            }
          >
            <option value="auto">
              Free public feeds + labeled demo fallback
            </option>
            <option value="demo">Demo only (illustrative data)</option>
          </select>
        </label>
        <p className="help-text">
          No API keys or subscriptions. Public feeds may be delayed or
          unavailable. Demo prices, headlines, and events are illustrative.
          Sources and timestamps appear on each widget.
        </p>
        <div className="form-divider">
          Watchlists <span>{workspace.watchlists.length}/20</span>
        </div>
        <div className="settings-lists">
          {workspace.watchlists.map((list) => (
            <div key={list.id}>
              <input
                aria-label={`Rename ${list.name}`}
                maxLength={40}
                value={list.name}
                onChange={(e) => {
                  const value = e.target.value;
                  if (value.trim())
                    update((s) => ({
                      ...s,
                      watchlists: s.watchlists.map((l) =>
                        l.id === list.id ? { ...l, name: value } : l,
                      ),
                    }));
                }}
              />
              <span className="muted">{list.symbols.length} symbols</span>
              <button
                className="icon-button"
                disabled={workspace.watchlists.length === 1}
                onClick={() => deleteList(list.id)}
                aria-label={`Delete ${list.name}`}
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </div>
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim() || workspace.watchlists.length >= 20) return;
            update((s) => ({
              ...s,
              watchlists: [
                ...s.watchlists,
                { id: crypto.randomUUID(), name: name.trim(), symbols: [] },
              ],
            }));
            setName("");
          }}
        >
          <input
            aria-label="New watchlist name"
            maxLength={40}
            placeholder="New watchlist name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button
            className="button"
            disabled={workspace.watchlists.length >= 20 || !name.trim()}
          >
            <Plus size={14} />
            Create
          </button>
        </form>
        <div className="form-divider">Take your workspace with you</div>
        <div className="settings-export">
          <button className="button" onClick={exportWorkspace}>
            <ArrowDownToLine size={15} />
            Export workspace
          </button>
          <button className="button" onClick={() => fileRef.current?.click()}>
            <ArrowUpFromLine size={15} />
            Import workspace
          </button>
          <input
            hidden
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            aria-label="Import workspace file"
            onChange={(e) => importWorkspace(e.target.files?.[0])}
          />
        </div>
        <p className="help-text">
          Exports include layouts, watchlists, private notes, trade history, and
          sensitive tax/income assumptions in plain text. Store backups securely
          before clearing browser data.
        </p>
        {error && (
          <p role="alert" className="field-error">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
