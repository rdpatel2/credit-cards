"use client";
import useSWR from "swr";
import type { Widget, Workspace } from "@/lib/workspace";
import type { DataEnvelope } from "@/lib/market/types";
import {
  analystLabels,
  analystSummary,
  type AnalystData,
} from "@/lib/market/analyst";

export function AnalystWidget({
  widget,
  workspace,
  symbol,
  patchWidget,
}: {
  widget: Widget;
  workspace: Workspace;
  symbol?: string;
  patchWidget: (widget: Widget) => void;
}) {
  const list =
    workspace.watchlists.find((l) => l.id === widget.watchlistId) ??
    workspace.watchlists[0];
  const ticker = symbol && widget.followSymbol ? symbol : widget.symbol;
  const symbols =
    widget.analystScope === "stock" ? [ticker] : [...new Set(list.symbols)];
  const { data, isLoading, mutate } = useSWR(
    ["analysts", symbols.join(","), workspace.dataMode],
    async () =>
      Promise.all(
        symbols.map(async (symbol) => {
          try {
            const response = await fetch(
              `/api/market?kind=analyst&symbol=${encodeURIComponent(symbol)}&mode=${workspace.dataMode}`,
            );
            if (!response.ok) return null;
            return (await response.json()) as DataEnvelope<AnalystData>;
          } catch {
            return null;
          }
        }),
      ),
    { revalidateOnFocus: false, refreshInterval: 300000 },
  );
  // Never blend synthetic fallback counts with real analyst opinions.
  const usable = (data ?? []).filter(
    (r): r is DataEnvelope<AnalystData> =>
      !!r && (workspace.dataMode === "demo" ? r.source.demo : !r.source.demo),
  );
  const current = usable.flatMap((r) =>
    r.data.periods.filter(
      (p) => p.period === "0m" && p.counts.some((n) => n > 0),
    ),
  );
  const result = analystSummary(current);
  const paired = usable.filter((r) =>
    ["0m", "-1m"].every((period) =>
      r.data.periods.some(
        (p) => p.period === period && p.counts.some((n) => n > 0),
      ),
    ),
  );
  const previous = analystSummary(
    paired.flatMap((r) => r.data.periods.filter((p) => p.period === "-1m")),
  );
  const pairedCurrent = analystSummary(
    paired.flatMap((r) => r.data.periods.filter((p) => p.period === "0m")),
  );
  const delta =
    previous.score !== null && pairedCurrent.score !== null
      ? pairedCurrent.score - previous.score
      : null;
  return (
    <div className="analyst-content">
      <div className="widget-toolbar">
        <select
          aria-label="Analyst coverage"
          value={widget.analystScope}
          onChange={(e) =>
            patchWidget({
              ...widget,
              analystScope: e.target.value as Widget["analystScope"],
            })
          }
        >
          <option value="stock">Stock consensus</option>
          <option value="watchlist">Market proxy (watchlist)</option>
        </select>
        {widget.analystScope === "watchlist" && (
          <select
            aria-label="Analyst watchlist"
            value={list.id}
            onChange={(e) =>
              patchWidget({ ...widget, watchlistId: e.target.value })
            }
          >
            {workspace.watchlists.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        )}
      </div>
      {isLoading ? (
        <p className="muted">Loading analyst coverage…</p>
      ) : (
        <>
          {workspace.dataMode === "demo" && (
            <p className="warning-banner">
              DEMO · Illustrative analyst opinions, not current recommendations.
            </p>
          )}
          <div className="consensus-heading">
            <span>{widget.analystScope === "stock" ? ticker : list.name}</span>
            <strong>{result.label}</strong>
            <span>
              {result.score === null
                ? "No current analyst coverage available."
                : `${result.score.toFixed(2)} / 5 · ${result.total} recommendations`}
            </span>
          </div>
          <div className="consensus-bars">
            {analystLabels.map((label, i) => (
              <div key={label}>
                <span>{label}</span>
                <meter
                  aria-label={label}
                  min={0}
                  max={Math.max(1, result.total)}
                  value={result.counts[i]}
                />
                <b>{result.counts[i]}</b>
              </div>
            ))}
          </div>
          <p className="widget-footnote">
            {current.length} / {symbols.length} stocks covered.{" "}
            {symbols.length - current.length > 0 &&
              "Uncovered stocks and unavailable feeds excluded."}
          </p>
          <p className="widget-footnote">
            {delta === null
              ? "Prior-month comparison unavailable."
              : `${delta >= 0 ? "+" : ""}${delta.toFixed(2)} points vs prior month (${paired.length} matched stocks).`}
          </p>
          <details className="widget-footnote">
            <summary>Method & sources</summary>
            <p>
              Strong sell → very bearish; sell → bearish; hold → neutral; buy →
              bullish; strong buy → very bullish. Consensus is the
              recommendation-count-weighted mean (1–5), rounded to the nearest
              label. This watchlist proxy is not a broad-market index or a
              forecast. Analyst counts can change between months.
            </p>
            <p>
              Yahoo Finance recommendation trends; cached for five minutes.
              Provider periods are month-relative, not daily sentiment.{" "}
              {usable[0] &&
                `Retrieved ${new Date(usable[0].source.asOf).toLocaleString()}.`}
            </p>
          </details>
          <button className="text-button" onClick={() => mutate()}>
            Refresh analyst data
          </button>
        </>
      )}
    </div>
  );
}
