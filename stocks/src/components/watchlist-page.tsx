"use client";
import { useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { makeWidget, type Workspace } from "@/lib/workspace";
import { WatchlistWidget } from "./widgets";

export function WatchlistPage({
  list,
  workspace,
  update,
  navigate,
}: {
  list: Workspace["watchlists"][number];
  workspace: Workspace;
  update: (change: (state: Workspace) => Workspace) => void;
  navigate: (url: string) => void;
}) {
  const [name, setName] = useState(list.name);
  const [saved, setSaved] = useState(false);
  const [widget, setWidget] = useState(() => ({
    ...makeWidget("watchlist", "watchlist-editor"),
    watchlistId: list.id,
    showPositions: true,
  }));
  return (
    <div className="watchlist-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow page-eyebrow">
            <Link className="text-button" href="/">
              <ArrowLeft size={12} />
              Overview
            </Link>
            <span>/ WATCHLIST</span>
          </div>
          <div className="page-title">
            <h1>{list.name}</h1>
          </div>
          <p>
            Add stocks, manage your holdings, and calculate your cost basis.
          </p>
        </div>
      </div>
      <section className="watchlist-page-panel" aria-label="Edit watchlist">
        <form
          className="watchlist-name-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!name.trim()) return;
            update((state) => ({
              ...state,
              watchlists: state.watchlists.map((current) =>
                current.id === list.id
                  ? { ...current, name: name.trim() }
                  : current,
              ),
            }));
            setName(name.trim());
            setSaved(true);
          }}
        >
          <label>
            Watchlist name
            <input
              value={name}
              maxLength={40}
              required
              onChange={(event) => {
                setName(event.target.value);
                setSaved(false);
              }}
            />
          </label>
          <button className="button" type="submit" disabled={!name.trim()}>
            Save name
          </button>
          {saved && <span role="status">Name saved.</span>}
        </form>
        <p className="muted watchlist-intro">
          Add a ticker or choose Edit cost basis beside a stock to enter your
          trades. Amounts are in USD. Changes are saved in this browser.
        </p>
        <WatchlistWidget
          editor
          widget={widget}
          workspace={workspace}
          update={update}
          pageKey={`watchlist-${list.id}`}
          openSymbol={(symbol) =>
            navigate(`/stock/${encodeURIComponent(symbol)}`)
          }
          configure={() => {}}
          patchWidget={(next) => {
            if (next.watchlistId !== list.id)
              navigate(`/watchlist/${encodeURIComponent(next.watchlistId)}`);
            else setWidget(next);
          }}
        />
      </section>
    </div>
  );
}
