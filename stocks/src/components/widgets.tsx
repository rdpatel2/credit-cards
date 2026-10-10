"use client";
import { useEffect, useId, useRef, useState } from "react";
import useSWR from "swr";
import { AnalystWidget } from "./analyst-widget";
import { TradesWidget } from "./trades-widget";
import Link from "next/link";
import { TradeEditor, TradeDialog } from "./trade-dialog";
import { calculatePosition } from "@/lib/portfolio";
import {
  ArrowDownRight,
  ArrowUpRight,
  ArrowUpRight as External,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import type { Widget, Workspace } from "@/lib/workspace";
import { ranges, symbolSchema } from "@/lib/workspace";
import {
  metrics,
  sectorLabels,
  sectorIds,
  type Fundamentals,
  type MetricId,
} from "@/lib/market/research";
import type { MarketNews, MarketArticle } from "@/lib/market/market-news";
import type {
  DataEnvelope,
  EarningsItem,
  NewsItem,
  Quote,
  Source,
} from "@/lib/market/types";
export async function fetcher<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error ?? "Data unavailable. Try again later.");
  }
  return response.json();
}
export function useMarket<T>(
  kind: string,
  mode: string,
  symbol = "AAPL",
  range = "1d",
  date?: string,
) {
  const query = new URLSearchParams({
    kind,
    mode,
    symbol,
    range,
    ...(date ? { date } : {}),
  });
  return useSWR<DataEnvelope<T>>(`/api/market?${query}`, fetcher, {
    revalidateOnFocus: false,
    refreshInterval: 300000,
    errorRetryCount: 1,
    dedupingInterval: 60000,
  });
}
export const money = (value: number | null | undefined, currency = "USD") =>
  value == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        maximumFractionDigits: 2,
      }).format(value);
const compact = (value: number | null) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        notation: "compact",
        maximumFractionDigits: 1,
      }).format(value);
const percent = (value: number) =>
  `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
export function SourceBadge({
  source,
  asOf,
}: {
  source?: Source;
  asOf?: string;
}) {
  if (!source) return null;
  return (
    <span
      className={`source-badge ${source.demo ? "demo" : ""}`}
      title={`${source.message ?? source.provider} • As of ${new Date(asOf ?? source.asOf).toLocaleString()}`}
    >
      {source.demo ? "DEMO" : "DELAYED"}
    </span>
  );
}
function DataState({ error, retry }: { error?: Error; retry?: () => void }) {
  return (
    <div className={`data-state ${error ? "failed" : ""}`}>
      {error ? (
        <>
          <span>{error.message}</span>
          <button className="text-button" onClick={retry}>
            <RefreshCw size={13} /> Retry
          </button>
        </>
      ) : (
        <>
          <span className="loading-bar" />
          <span className="muted">Loading market data…</span>
        </>
      )}
    </div>
  );
}
export function Sparkline({
  points,
  positive = true,
  large = false,
}: {
  points: Quote["points"];
  positive?: boolean;
  large?: boolean;
}) {
  const id = useId().replace(/:/g, "");
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2)
    return <span className="muted">Chart unavailable</span>;
  const values = points.map((p) => p.close);
  const low = Math.min(...values),
    high = Math.max(...values);
  const width = 600,
    height = large ? 220 : 90,
    pad = large ? 12 : 3;
  const coords = points.map((p, i) => [
    (i / (points.length - 1)) * width,
    height - pad - ((p.close - low) / (high - low || 1)) * (height - pad * 2),
  ]);
  const path = coords
    .map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`)
    .join(" ");
  const color = positive ? "var(--green)" : "var(--red)";
  return (
    <div className={large ? "price-plot" : "sparkline"}>
      {large && hover !== null && (
        <div className="chart-tooltip">
          {new Date(points[hover].time * 1000).toLocaleString()}{" "}
          <strong>{points[hover].close.toFixed(2)}</strong>
        </div>
      )}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={large ? "Historical price chart" : "Price trend"}
        onMouseMove={
          large
            ? (event) => {
                const box = event.currentTarget.getBoundingClientRect();
                setHover(
                  Math.max(
                    0,
                    Math.min(
                      points.length - 1,
                      Math.round(
                        ((event.clientX - box.left) / box.width) *
                          (points.length - 1),
                      ),
                    ),
                  ),
                );
              }
            : undefined
        }
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop
              offset="0%"
              stopColor={color}
              stopOpacity={large ? 0.2 : 0.1}
            />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {large &&
          [0.25, 0.5, 0.75].map((y) => (
            <line
              key={y}
              x1="0"
              x2={width}
              y1={height * y}
              y2={height * y}
              stroke="var(--border)"
              strokeDasharray="4 6"
            />
          ))}
        <path
          d={`${path} L${width},${height} L0,${height} Z`}
          fill={`url(#${id})`}
        />
        <path
          d={path}
          fill="none"
          stroke={color}
          strokeWidth={large ? 2 : 1.7}
          vectorEffect="non-scaling-stroke"
        />
        {large && hover !== null && (
          <>
            <line
              x1={coords[hover][0]}
              x2={coords[hover][0]}
              y1="0"
              y2={height}
              stroke="var(--muted)"
              strokeDasharray="4 4"
            />
            <circle
              cx={coords[hover][0]}
              cy={coords[hover][1]}
              r="4"
              fill={color}
            />
          </>
        )}
      </svg>
      {large && (
        <div className="chart-axis">
          <span>{new Date(points[0].time * 1000).toLocaleDateString()}</span>
          <span>
            {low.toFixed(2)} – {high.toFixed(2)}
          </span>
          <span>
            {new Date(points.at(-1)!.time * 1000).toLocaleDateString()}
          </span>
        </div>
      )}
    </div>
  );
}
type Props = {
  widget: Widget;
  workspace: Workspace;
  symbol?: string;
  pageKey: string;
  openSymbol: (symbol: string) => void;
  update: (change: (state: Workspace) => Workspace) => void;
  configure: (widget: Widget) => void;
  patchWidget: (widget: Widget) => void;
};
export function WidgetContent(props: Props) {
  const { widget } = props;
  switch (widget.type) {
    case "watchlist":
      return <WatchlistWidget {...props} />;
    case "trades":
      return <TradesWidget workspace={props.workspace} update={props.update} />;
    case "market":
      return <MarketWidget {...props} />;
    case "movers":
      return <MoversWidget {...props} />;
    case "chart":
      return <ChartWidget {...props} />;
    case "quote":
      return <QuoteWidget {...props} />;
    case "metrics":
      return <MetricsWidget {...props} />;
    case "analyst":
      return <AnalystWidget {...props} />;
    case "news":
      return props.widget.newsScope === "market" ? (
        <MarketNewsWidget {...props} />
      ) : (
        <NewsWidget {...props} />
      );
    case "earnings":
      return <EarningsWidget {...props} />;
    case "notes":
      return <NotesWidget {...props} />;
  }
}
const activeSymbol = (props: Props) =>
  props.symbol && props.widget.followSymbol
    ? props.symbol
    : props.widget.symbol;
function metricValue(id: MetricId, value: number | null, data: Fundamentals) {
  if (value === null) return "—";
  const currency = ["totalRevenue", "freeCashflow"].includes(id)
    ? data.financialCurrency
    : data.currency;
  switch (metrics[id].format) {
    case "money":
      return money(value, currency);
    case "percent":
      return `${(value * 100).toFixed(2)}%`;
    case "multiple":
      return `${value.toFixed(2)}×`;
    case "compact":
      return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        notation: "compact",
        maximumFractionDigits: 2,
      }).format(value);
    default:
      return value.toFixed(2);
  }
}
function MetricsWidget(props: Props) {
  const symbol = activeSymbol(props);
  const { data, error, mutate } = useMarket<Fundamentals>(
    "fundamentals",
    props.workspace.dataMode,
    symbol,
  );
  return (
    <>
      <div className="widget-toolbar">
        <span className="eyebrow">{symbol} · Fundamentals</span>
        <button
          className="text-button"
          onClick={() => props.configure(props.widget)}
        >
          Choose metrics
        </button>
        <SourceBadge source={data?.source} />
      </div>
      {!props.widget.metrics.length ? (
        <div className="empty-inline">
          No metrics selected. Choose metrics to build your view.
        </div>
      ) : !data ? (
        <DataState error={error} retry={() => mutate()} />
      ) : (
        <>
          <dl className="metrics-grid">
            {props.widget.metrics.map((id) => (
              <div
                key={id}
                data-metric={id}
                title={`${metrics[id].description}${data.data.values[id] === null ? " Not provided or not meaningful for this security." : ""}`}
              >
                <dt>{metrics[id].label}</dt>
                <dd>{metricValue(id, data.data.values[id], data.data)}</dd>
              </div>
            ))}
          </dl>
          <div className="widget-footnote">
            {data.source.demo
              ? "Illustrative fundamentals"
              : "Yahoo Finance fundamentals"}{" "}
            · Retrieved {new Date(data.data.asOf).toLocaleString()}
            <br />
            TTM = trailing twelve months · Forward values are estimates · — =
            unavailable / not meaningful
            {data.data.currency !== data.data.financialCurrency && (
              <>
                <br />
                Trading currency: {data.data.currency} · Financial reporting:{" "}
                {data.data.financialCurrency}
              </>
            )}
          </div>
        </>
      )}
    </>
  );
}
function MarketNewsWidget(props: Props) {
  const { data, error, mutate } = useMarket<MarketNews>(
    "market-news",
    props.workspace.dataMode,
  );
  const [query, setQuery] = useState("");
  const { widget } = props;
  const articles =
    data?.data.articles.filter(
      (article) =>
        (widget.sector === "all" || article.sectors.includes(widget.sector)) &&
        `${article.title} ${article.publisher}`
          .toLowerCase()
          .includes(query.toLowerCase()),
    ) ?? [];
  const visible = articles.slice(0, widget.limit);
  function articleCard(article: MarketArticle) {
    return (
      <a
        className="market-article"
        key={article.url}
        href={article.url}
        target="_blank"
        rel="noopener noreferrer"
      >
        <div className="article-meta">
          <span>{article.publisher}</span>
          <time dateTime={article.publishedAt}>
            {new Date(article.publishedAt).toLocaleString("en-US", {
              month: "short",
              day: "numeric",
              hour: "numeric",
              minute: "2-digit",
            })}
          </time>
        </div>
        <h3>
          {article.title}
          <External size={14} />
        </h3>
        <div className="article-sectors">
          {article.sectors.map((sector) => (
            <span key={sector}>{sectorLabels[sector]}</span>
          ))}
        </div>
      </a>
    );
  }
  return (
    <div className="market-news-content">
      <div className="news-controls">
        <input
          aria-label="Search market headlines"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search headlines or publishers…"
        />
        <div className="segmented" aria-label="News display">
          {(["cards", "list", "sectors"] as const).map((view) => (
            <button
              key={view}
              aria-pressed={widget.newsView === view}
              className={widget.newsView === view ? "active" : ""}
              onClick={() => props.patchWidget({ ...widget, newsView: view })}
            >
              {view === "cards"
                ? "Cards"
                : view === "list"
                  ? "Headlines"
                  : "By sector"}
            </button>
          ))}
        </div>
        <SourceBadge source={data?.source} />
      </div>
      <p className="news-coverage-note">
        {sectorLabels[widget.sector]} · Sector labels are inferred from
        headlines; articles may cover multiple sectors.
      </p>
      {!data ? (
        <DataState error={error} retry={() => mutate()} />
      ) : (
        <>
          {data.data.unavailableFeeds.length > 0 && (
            <div className="warning-banner" role="status">
              Some feeds are unavailable:{" "}
              {data.data.unavailableFeeds.join(", ")}. Showing available
              coverage.
            </div>
          )}
          {data.source.demo && (
            <div className="news-demo-note">
              Sample headlines, not current news. Links open the publisher’s
              news page.
            </div>
          )}
          {widget.newsView === "sectors" ? (
            <div className="news-sector-groups">
              {sectorIds
                .filter(
                  (sector) =>
                    sector !== "all" &&
                    (widget.sector === "all" || widget.sector === sector),
                )
                .map((sector) => {
                  const group = visible.filter(
                    (article) =>
                      sector !== "all" && article.sectors.includes(sector),
                  );
                  return group.length ? (
                    <section className="news-sector-group" key={sector}>
                      <h3>
                        {sectorLabels[sector]} <span>{group.length}</span>
                      </h3>
                      <div className="market-articles cards">
                        {group.map(articleCard)}
                      </div>
                    </section>
                  ) : null;
                })}
            </div>
          ) : (
            <div className={`market-articles ${widget.newsView}`}>
              {visible.map(articleCard)}
            </div>
          )}
          {!articles.length && (
            <div className="empty-inline">
              No matching headlines in the available feeds. Try another sector
              or search.
            </div>
          )}
          {articles.length > widget.limit && (
            <button
              className="button news-more"
              onClick={() =>
                props.patchWidget({
                  ...widget,
                  limit: Math.min(120, widget.limit + 20),
                })
              }
            >
              Show more headlines ({articles.length - widget.limit} remaining)
            </button>
          )}
          <div className="widget-footnote">
            {visible.length} of {articles.length} matching articles ·{" "}
            {data.source.demo
              ? "Demo collection"
              : "Free public RSS feeds; coverage is not exhaustive"}{" "}
            · Refreshed {new Date(data.source.asOf).toLocaleString()}
          </div>
        </>
      )}
    </div>
  );
}
export function WatchlistWidget(props: Props & { editor?: boolean }) {
  const { widget, workspace, update } = props;
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [tradeSymbol, setTradeSymbol] = useState<string | null>(null);
  const list =
    workspace.watchlists.find((l) => l.id === widget.watchlistId) ??
    workspace.watchlists[0];
  const editorHeading = useRef<HTMLHeadingElement>(null);
  const selectedSymbol =
    tradeSymbol && list.symbols.includes(tradeSymbol) ? tradeSymbol : null;
  useEffect(() => {
    if (props.editor && selectedSymbol) editorHeading.current?.focus();
  }, [props.editor, selectedSymbol]);
  // One SWR batch key, individual server calls still use the shared provider cache.
  const { data, isLoading, mutate } = useSWR(
    ["watchlist", list.symbols.join(","), workspace.dataMode],
    async () =>
      Promise.all(
        list.symbols.map(async (symbol) => {
          try {
            return {
              symbol,
              result: await fetcher<DataEnvelope<Quote>>(
                `/api/market?symbol=${symbol}&mode=${workspace.dataMode}`,
              ),
            };
          } catch {
            return { symbol, result: null };
          }
        }),
      ),
    { revalidateOnFocus: false, refreshInterval: 300000 },
  );
  const sorted = [
    ...(data ?? list.symbols.map((symbol) => ({ symbol, result: null }))),
  ].sort((a, b) =>
    widget.sort === "symbol"
      ? a.symbol.localeCompare(b.symbol)
      : widget.sort === "price"
        ? (b.result?.data.price ?? -Infinity) -
          (a.result?.data.price ?? -Infinity)
        : (b.result?.data.changePercent ?? -Infinity) -
          (a.result?.data.changePercent ?? -Infinity),
  );
  function add(event: React.FormEvent) {
    event.preventDefault();
    const result = symbolSchema.safeParse(draft);
    if (!result.success) return setError("Enter a valid ticker, e.g. AAPL.");
    if (list.symbols.includes(result.data))
      return setError("This ticker is already in your watchlist.");
    if (list.symbols.length >= 30)
      return setError("Each watchlist supports up to 30 symbols.");
    update((state) => ({
      ...state,
      watchlists: state.watchlists.map((l) =>
        l.id === list.id ? { ...l, symbols: [...l.symbols, result.data] } : l,
      ),
    }));
    setDraft("");
    setError("");
    if (props.editor) setTradeSymbol(result.data);
  }
  return (
    <div
      className={`watchlist-content ${props.editor ? "watchlist-editor-content" : ""}`}
    >
      <div className="widget-toolbar">
        <select
          aria-label="Selected watchlist"
          value={list.id}
          onChange={(e) =>
            props.patchWidget({ ...widget, watchlistId: e.target.value })
          }
        >
          {workspace.watchlists.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
        <span className="muted">{list.symbols.length} symbols</span>
        {!props.editor && (
          <Link
            className="text-button"
            href={`/watchlist/${encodeURIComponent(list.id)}`}
          >
            Edit watchlist <External size={12} />
          </Link>
        )}
        {!props.editor && (
          <button
            className="text-button"
            aria-pressed={widget.showPositions}
            onClick={() =>
              props.patchWidget({
                ...widget,
                showPositions: !widget.showPositions,
              })
            }
          >
            {widget.showPositions ? "Show prices" : "Show positions"}
          </button>
        )}
      </div>
      <form className="add-symbol" onSubmit={add}>
        <Plus size={15} />
        <input
          aria-label="Add ticker to watchlist"
          placeholder="Add a symbol, e.g. NFLX"
          value={draft}
          maxLength={15}
          onChange={(e) => setDraft(e.target.value.toUpperCase())}
        />
        <button className="text-button" type="submit">
          Add
        </button>
      </form>
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      <div className="table-scroll">
        <table className="watch-table">
          <thead>
            <tr>
              <th>
                <button
                  onClick={() =>
                    props.patchWidget({ ...widget, sort: "symbol" })
                  }
                >
                  Company
                </button>
              </th>
              <th>
                <button
                  onClick={() =>
                    props.patchWidget({ ...widget, sort: "price" })
                  }
                >
                  Price
                </button>
              </th>
              <th>
                <button
                  onClick={() =>
                    props.patchWidget({ ...widget, sort: "change" })
                  }
                >
                  Change %
                </button>
              </th>
              {widget.showPositions ? (
                <>
                  <th>Shares</th>
                  <th>Avg. cost</th>
                  {props.editor && <th>Cost basis</th>}
                </>
              ) : (
                <>
                  {widget.showChart && <th>Trend</th>}
                  {widget.showVolume && <th>Volume</th>}
                </>
              )}
              <th>Position</th>
              <th>
                <span className="sr-only">Remove</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(({ symbol, result }) => (
              <tr key={symbol}>
                <td>
                  <button
                    className="company-link"
                    onClick={() => props.openSymbol(symbol)}
                  >
                    <span
                      className={`company-icon color-${symbol.charCodeAt(0) % 5}`}
                    >
                      {symbol.slice(0, 1)}
                    </span>
                    <span>
                      <strong>{symbol}</strong>
                      <small>{result?.data.name ?? "Unavailable"}</small>
                    </span>
                  </button>
                </td>
                <td>
                  {result ? (
                    <>
                      {money(result.data.price, result.data.currency)}
                      <SourceBadge
                        source={result.source}
                        asOf={result.data.asOf}
                      />
                    </>
                  ) : (
                    <button className="text-button" onClick={() => mutate()}>
                      Retry
                    </button>
                  )}
                </td>
                <td
                  className={
                    result && result.data.changePercent >= 0
                      ? "positive"
                      : "negative"
                  }
                >
                  {result ? percent(result.data.changePercent) : "—"}
                </td>
                {widget.showPositions && (
                  <>
                    <td>
                      {
                        calculatePosition(workspace.trades[symbol] ?? [])
                          .quantity
                      }
                    </td>
                    <td>
                      {money(
                        calculatePosition(workspace.trades[symbol] ?? [])
                          .averageCost,
                      )}
                    </td>
                    {props.editor && (
                      <td>
                        {money(
                          calculatePosition(workspace.trades[symbol] ?? [])
                            .basis,
                        )}
                      </td>
                    )}
                  </>
                )}
                {!widget.showPositions && widget.showChart && (
                  <td>
                    {result && (
                      <Sparkline
                        points={result.data.points}
                        positive={result.data.changePercent >= 0}
                      />
                    )}
                  </td>
                )}
                {!widget.showPositions && widget.showVolume && (
                  <td className="muted">
                    {result ? compact(result.data.volume) : "—"}
                  </td>
                )}
                <td>
                  <button
                    className="text-button"
                    aria-label={`${props.editor ? "Edit cost basis" : "Trades"} for ${symbol}`}
                    onClick={() => setTradeSymbol(symbol)}
                  >
                    {props.editor ? "Edit cost basis" : "Trades"}
                  </button>
                </td>
                <td>
                  <button
                    className="row-remove icon-button"
                    aria-label={`Remove ${symbol}`}
                    onClick={() =>
                      update((state) => ({
                        ...state,
                        watchlists: state.watchlists.map((l) =>
                          l.id === list.id
                            ? {
                                ...l,
                                symbols: l.symbols.filter((s) => s !== symbol),
                              }
                            : l,
                        ),
                      }))
                    }
                  >
                    <Trash2 size={12} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!props.editor && tradeSymbol && (
          <TradeDialog
            key={tradeSymbol}
            symbol={tradeSymbol}
            workspace={workspace}
            update={update}
            quote={data?.find((r) => r.symbol === tradeSymbol)?.result ?? null}
            onClose={() => setTradeSymbol(null)}
          />
        )}
        {isLoading && <DataState />}
        {!isLoading && !list.symbols.length && (
          <div className="empty-inline">
            Your next idea starts here. Add a ticker to get started.
          </div>
        )}
      </div>
      <div className="widget-footnote">
        Quotes refresh every 5 min · Hover a source label for its timestamp
      </div>
      {props.editor && selectedSymbol && (
        <section
          className="watchlist-cost-editor"
          aria-labelledby="cost-editor-heading"
        >
          <h2 id="cost-editor-heading" ref={editorHeading} tabIndex={-1}>
            {selectedSymbol} · Cost basis
          </h2>
          <p className="muted">
            Enter each purchase date, shares, price per share, and any fees. Add
            sales to keep your remaining shares and cost basis up to date.
          </p>
          <TradeEditor
            key={selectedSymbol}
            symbol={selectedSymbol}
            workspace={workspace}
            update={update}
            quote={
              data?.find((r) => r.symbol === selectedSymbol)?.result ?? null
            }
            costBasisOnly
          />
        </section>
      )}
    </div>
  );
}
function MarketWidget(props: Props) {
  const list =
    props.workspace.watchlists.find((l) => l.id === props.widget.watchlistId) ??
    props.workspace.watchlists[0];
  return (
    <div className="market-list">
      {list.symbols.slice(0, props.widget.limit).map((symbol) => (
        <MarketRow key={symbol} {...props} symbol={symbol} />
      ))}
      {!list.symbols.length && (
        <div className="empty-inline">
          Add symbols to {list.name} through a watchlist widget.
        </div>
      )}
      <div className="widget-footnote">
        {list.name} · Prices and daily changes
      </div>
    </div>
  );
}
function MarketRow({
  symbol,
  workspace,
  openSymbol,
}: Props & { symbol: string }) {
  const { data, error, mutate } = useMarket<Quote>(
    "quote",
    workspace.dataMode,
    symbol,
  );
  if (!data) return <DataState error={error} retry={() => mutate()} />;
  const quote = data.data;
  return (
    <button className="market-row" onClick={() => openSymbol(symbol)}>
      <span>
        <strong>{symbol}</strong>
        <small>{quote.name}</small>
      </span>
      <span className="market-spark">
        <Sparkline points={quote.points} positive={quote.changePercent >= 0} />
      </span>
      <span className="market-number">
        <strong>{money(quote.price)}</strong>
        <small className={quote.changePercent >= 0 ? "positive" : "negative"}>
          {percent(quote.changePercent)}{" "}
          <SourceBadge source={data.source} asOf={quote.asOf} />
        </small>
      </span>
    </button>
  );
}
function MoversWidget(props: Props) {
  const { widget, workspace } = props;
  const list =
    workspace.watchlists.find((l) => l.id === widget.watchlistId) ??
    workspace.watchlists[0];
  const { data, isLoading } = useSWR(
    ["watchlist", list.symbols.join(","), workspace.dataMode],
    async () =>
      Promise.all(
        list.symbols.map(async (symbol) => {
          try {
            return {
              symbol,
              result: await fetcher<DataEnvelope<Quote>>(
                `/api/market?symbol=${symbol}&mode=${workspace.dataMode}`,
              ),
            };
          } catch {
            return { symbol, result: null };
          }
        }),
      ),
    { revalidateOnFocus: false, refreshInterval: 300000 },
  );
  const rows = (data ?? [])
    .filter(
      (row) =>
        row.result &&
        (widget.direction === "gainers"
          ? row.result.data.changePercent >= 0
          : row.result.data.changePercent < 0),
    )
    .sort(
      (a, b) =>
        (widget.direction === "gainers" ? 1 : -1) *
        (b.result!.data.changePercent - a.result!.data.changePercent),
    )
    .slice(0, widget.limit);
  return (
    <>
      <div className="segmented">
        {(["gainers", "losers"] as const).map((direction) => (
          <button
            key={direction}
            className={widget.direction === direction ? "active" : ""}
            onClick={() => props.patchWidget({ ...widget, direction })}
          >
            {direction === "gainers" ? "Gainers" : "Losers"}
          </button>
        ))}
      </div>
      <div className="movers-list">
        {rows.map(({ symbol, result }, i) => (
          <button
            key={symbol}
            className="mover-row"
            onClick={() => props.openSymbol(symbol)}
          >
            <span className="rank">{String(i + 1).padStart(2, "0")}</span>
            <span>
              <strong>{symbol}</strong>
              <small>{result!.data.name}</small>
            </span>
            <span
              className={`change-pill ${result!.data.changePercent >= 0 ? "positive" : "negative"}`}
            >
              {percent(result!.data.changePercent)}
              <SourceBadge source={result!.source} asOf={result!.data.asOf} />
            </span>
          </button>
        ))}
        {isLoading && <DataState />}
        {!isLoading && !rows.length && (
          <div className="empty-inline">
            No available {widget.direction} in this list.
          </div>
        )}
      </div>
      <div className="widget-footnote">
        Within {list.name.toLowerCase()} ·{" "}
        {data?.filter((row) => row.result).length ?? 0}/{list.symbols.length}{" "}
        available
      </div>
    </>
  );
}
function ChartWidget(props: Props) {
  const symbol = activeSymbol(props);
  const { data, error, mutate } = useMarket<Quote>(
    "quote",
    props.workspace.dataMode,
    symbol,
    props.widget.range,
  );
  return (
    <div className="chart-widget">
      <div className="chart-topline">
        <button
          className="text-button"
          onClick={() => props.openSymbol(symbol)}
        >
          {symbol}
          <External size={13} />
        </button>
        <div className="range-buttons">
          {ranges.map((range) => (
            <button
              key={range}
              className={range === props.widget.range ? "active" : ""}
              onClick={() => props.patchWidget({ ...props.widget, range })}
            >
              {range.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      {!data ? (
        <DataState error={error} retry={() => mutate()} />
      ) : (
        <>
          <div className="chart-price">
            {money(data.data.price, data.data.currency)}
            <SourceBadge source={data.source} asOf={data.data.asOf} />
            <span className={data.data.change >= 0 ? "positive" : "negative"}>
              {data.data.change >= 0 ? (
                <ArrowUpRight size={17} />
              ) : (
                <ArrowDownRight size={17} />
              )}
              {percent(data.data.changePercent)} <small>today</small>
            </span>
          </div>
          <Sparkline
            points={data.data.points}
            large
            positive={
              (data.data.points.at(-1)?.close ?? 0) >=
              (data.data.points[0]?.close ?? 0)
            }
          />
          <div className="widget-footnote">
            {data.source.demo
              ? "Illustrative price history"
              : data.source.provider}{" "}
            · {new Date(data.data.asOf).toLocaleString()} · {data.data.currency}
          </div>
        </>
      )}
    </div>
  );
}
function QuoteWidget(props: Props) {
  const { data, error, mutate } = useMarket<Quote>(
    "quote",
    props.workspace.dataMode,
    activeSymbol(props),
  );
  if (!data) return <DataState error={error} retry={() => mutate()} />;
  const q = data.data;
  return (
    <div className="quote-widget">
      <div className="quote-identity">
        <span className="company-icon">{q.symbol[0]}</span>
        <div>
          <strong>{q.symbol}</strong>
          <small>{q.name}</small>
        </div>
        <SourceBadge source={data.source} asOf={q.asOf} />
      </div>
      <div className="quote-price">
        {money(q.price, q.currency)}
        <span className={q.change >= 0 ? "positive" : "negative"}>
          {percent(q.changePercent)}
        </span>
      </div>
      <dl>
        {[
          ["Previous close", money(q.previousClose, q.currency)],
          ["Day low", money(q.low, q.currency)],
          ["Day high", money(q.high, q.currency)],
          ["52-week low", money(q.yearLow, q.currency)],
          ["52-week high", money(q.yearHigh, q.currency)],
          ["Volume", compact(q.volume)],
          ["Currency", q.currency],
        ].map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="widget-footnote">
        As of {new Date(q.asOf).toLocaleString()}
      </div>
    </div>
  );
}
function NewsWidget(props: Props) {
  const symbol = activeSymbol(props);
  const { data, error, mutate } = useMarket<NewsItem[]>(
    "news",
    props.workspace.dataMode,
    symbol,
  );
  if (!data) return <DataState error={error} retry={() => mutate()} />;
  return (
    <>
      <div className="widget-toolbar">
        <span className="eyebrow">Following {symbol}</span>
        <SourceBadge source={data.source} />
      </div>
      <div className="news-list">
        {data.data.slice(0, props.widget.limit).map((news, i) => (
          <a
            key={`${news.url}-${i}`}
            href={news.url}
            target="_blank"
            rel="noopener noreferrer"
            className="news-item"
          >
            <div>
              <span className="news-dot" />
              {news.publisher}
              <span>
                {new Date(news.publishedAt).toLocaleDateString("en-US", {
                  month: "short",
                  day: "numeric",
                })}
              </span>
            </div>
            <h3>{news.title}</h3>
            <External size={13} />
          </a>
        ))}
      </div>
      {!data.data.length && (
        <div className="empty-inline">
          No headlines available for this symbol.
        </div>
      )}
      {data.source.demo && (
        <div className="widget-footnote">
          Sample headlines · Links open the actual news page
        </div>
      )}
    </>
  );
}
function EarningsWidget(props: Props) {
  const date =
    props.widget.earningsDate || new Date().toLocaleDateString("en-CA");
  const { data, error, mutate } = useMarket<EarningsItem[]>(
    "earnings",
    props.workspace.dataMode,
    "AAPL",
    "1d",
    date,
  );
  return (
    <>
      <div className="widget-toolbar">
        <input
          className="date-input"
          aria-label="Earnings date"
          type="date"
          value={date}
          onChange={(e) => {
            if (e.target.value)
              props.patchWidget({
                ...props.widget,
                earningsDate: e.target.value,
              });
          }}
        />
        <SourceBadge source={data?.source} />
      </div>
      {props.widget.earningsDate && (
        <button
          className="text-button"
          onClick={() =>
            props.patchWidget({ ...props.widget, earningsDate: "" })
          }
        >
          Follow today
        </button>
      )}
      {!data ? (
        <DataState error={error} retry={() => mutate()} />
      ) : (
        <>
          <div className="earnings-list">
            {data.data.slice(0, props.widget.limit).map((item, i) => (
              <button
                className="earnings-row"
                key={`${item.symbol}-${i}`}
                onClick={() => props.openSymbol(item.symbol)}
              >
                <span className="calendar-day">
                  <small>
                    {new Date(`${date}T12:00:00`)
                      .toLocaleDateString("en-US", { month: "short" })
                      .toUpperCase()}
                  </small>
                  {date.slice(-2)}
                </span>
                <span>
                  <strong>{item.symbol}</strong>
                  <small>{item.time}</small>
                </span>
                <span className="eps">
                  {item.estimate}
                  <small>EPS est.</small>
                </span>
              </button>
            ))}
          </div>
          {!data.data.length && (
            <div className="empty-inline">
              No earnings scheduled for this day.
            </div>
          )}
          <div className="widget-footnote">
            {data.source.demo
              ? "Sample events · Not an actual earnings schedule"
              : "Nasdaq · Dates and estimates may change"}
          </div>
        </>
      )}
    </>
  );
}
function NotesWidget(props: Props) {
  const key = `${props.pageKey}:${props.widget.id}`;
  const text = props.workspace.notes[key] ?? "";
  return (
    <div className="notes-widget">
      <textarea
        aria-label="Research notes"
        placeholder={
          props.symbol
            ? `Your ${props.symbol} thesis starts here…\n\nWhat matters?\nWhat would change your mind?\nWhat are you watching next?`
            : "Make room for your next idea…\n\nWrite a thesis, keep a checklist, or capture what matters."
        }
        value={text}
        maxLength={20000}
        onChange={(e) =>
          props.update((state) => ({
            ...state,
            notes: { ...state.notes, [key]: e.target.value },
          }))
        }
      />
      <div className="widget-footnote">
        Private to this browser · {text.length.toLocaleString()} / 20,000
        characters
      </div>
    </div>
  );
}
