"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import Decimal from "decimal.js";
import type { Workspace } from "@/lib/workspace";
import { TradeDialog } from "./trade-dialog";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});
const pageSize = 20;

export function TradesWidget({
  workspace,
  update,
}: {
  workspace: Workspace;
  update: (change: (state: Workspace) => Workspace) => void;
}) {
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState("");
  const [page, setPage] = useState(0);
  const [editingSymbol, setEditingSymbol] = useState<string | null>(null);
  // Read the account ledger once, independently of watchlist membership.
  const trades = useMemo(
    () =>
      Object.entries(workspace.trades)
        .flatMap(([symbol, entries]) =>
          entries.map((trade, index) => ({ ...trade, symbol, index })),
        )
        .sort(
          (a, b) =>
            b.date.localeCompare(a.date) ||
            a.symbol.localeCompare(b.symbol) ||
            b.index - a.index,
        ),
    [workspace.trades],
  );
  const symbols = [...new Set(trades.map((trade) => trade.symbol))].sort();
  const filtered = trades.filter(
    (trade) =>
      (!symbol || trade.symbol === symbol) && (!side || trade.side === side),
  );
  const lastPage = Math.max(0, Math.ceil(filtered.length / pageSize) - 1);
  const currentPage = Math.min(page, lastPage);
  const start = currentPage * pageSize;
  return (
    <div className="trades-content">
      <div className="widget-toolbar">
        <select
          aria-label="Filter trades by symbol"
          value={symbol}
          onChange={(event) => {
            setSymbol(event.target.value);
            setPage(0);
          }}
        >
          <option value="">All traded symbols</option>
          {symbol && !symbols.includes(symbol) && (
            <option value={symbol}>{symbol}</option>
          )}
          {symbols.map((ticker) => (
            <option key={ticker} value={ticker}>
              {ticker}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter trades by action"
          value={side}
          onChange={(event) => {
            setSide(event.target.value);
            setPage(0);
          }}
        >
          <option value="">Buys &amp; sells</option>
          <option value="buy">Buys</option>
          <option value="sell">Sells</option>
        </select>
        <span className="muted">
          {filtered.length} {filtered.length === 1 ? "trade" : "trades"}
        </span>
      </div>
      {!trades.length ? (
        <div className="empty-inline">
          <p>No trades recorded yet.</p>
          <p>
            Enter a purchase or sale in the watchlist editor to see it here.
          </p>
          <Link
            className="text-button"
            href={`/watchlist/${encodeURIComponent(workspace.watchlists[0].id)}`}
          >
            Open watchlist editor
          </Link>
        </div>
      ) : !filtered.length ? (
        <div className="empty-inline">No trades match these filters.</div>
      ) : (
        <>
          <div className="table-scroll">
            <table className="watch-table trades-table">
              <caption className="sr-only">
                Recorded trades, newest first. All amounts in USD.
              </caption>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Symbol</th>
                  <th>Action</th>
                  <th>Shares</th>
                  <th>Price</th>
                  <th>Fees</th>
                  <th>Total (USD)</th>
                </tr>
              </thead>
              <tbody>
                {filtered.slice(start, start + pageSize).map((trade) => {
                  const gross = new Decimal(trade.quantity).mul(trade.price);
                  const total =
                    trade.side === "buy"
                      ? gross.add(trade.fees)
                      : gross.sub(trade.fees);
                  return (
                    <tr key={`${trade.symbol}:${trade.id}`}>
                      <td>{trade.date}</td>
                      <td>
                        <button
                          className="text-button"
                          aria-label={`Manage ${trade.symbol} trades`}
                          onClick={() => setEditingSymbol(trade.symbol)}
                        >
                          {trade.symbol}
                        </button>
                      </td>
                      <td>
                        <span className={`trade-side trade-side-${trade.side}`}>
                          {trade.side === "buy" ? "Buy" : "Sell"}
                        </span>
                      </td>
                      <td>{trade.quantity}</td>
                      <td>{usd.format(trade.price)}</td>
                      <td>{usd.format(trade.fees)}</td>
                      <td>{usd.format(total.toNumber())}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {filtered.length > pageSize && (
            <div className="trades-pagination">
              <button
                className="text-button"
                disabled={currentPage === 0}
                onClick={() => setPage(currentPage - 1)}
              >
                Previous trades
              </button>
              <span className="muted">
                {start + 1}–{Math.min(start + pageSize, filtered.length)} of{" "}
                {filtered.length}
              </span>
              <button
                className="text-button"
                disabled={currentPage === lastPage}
                onClick={() => setPage(currentPage + 1)}
              >
                Next trades
              </button>
            </div>
          )}
          <p className="widget-footnote">
            Newest first · Buy totals include fees; sell totals deduct fees.
            Click a ticker to edit its trade history.
          </p>
        </>
      )}
      {editingSymbol && (
        <TradeDialog
          key={editingSymbol}
          symbol={editingSymbol}
          workspace={workspace}
          update={update}
          quote={null}
          onClose={() => setEditingSymbol(null)}
        />
      )}
    </div>
  );
}
