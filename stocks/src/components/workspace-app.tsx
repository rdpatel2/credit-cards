"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import GridLayout from "react-grid-layout/legacy";
import { useContainerWidth } from "react-grid-layout";
import {
  ArrowLeft,
  ArrowUpRight,
  Bookmark,
  Check,
  ChevronDown,
  CircleHelp,
  Copy,
  FileText,
  Grip,
  LayoutDashboard,
  LayoutGrid,
  Maximize2,
  Menu,
  Moon,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Settings2,
  SlidersHorizontal,
  Sun,
  Trash2,
  TrendingUp,
  X,
} from "lucide-react";
import { useWorkspace } from "@/lib/use-workspace";
import {
  addWidget,
  homePage,
  removeWidget,
  resolvePage,
  newsPage,
  stockPage,
  symbolSchema,
  type Widget,
  type WidgetType,
  type Workspace,
  type WorkspacePage,
  type LayoutItem,
} from "@/lib/workspace";
import { reconcileLayout, resolveLayoutMove } from "@/lib/layout";
import { sectorIds, sectorLabels } from "@/lib/market/research";
import { companies } from "@/lib/market/demo";
import { WidgetContent } from "./widgets";
import {
  NameDialog,
  SearchDialog,
  WidgetLibrary,
  WidgetSettings,
  WorkspaceSettings,
} from "./workspace-dialogs";
import { Modal } from "./modal";
import { WatchlistPage } from "./watchlist-page";

export function WorkspaceApp() {
  const { workspace, error, update } = useWorkspace();
  if (!workspace)
    return (
      <div className="boot-screen">
        <span className="brand-mark">f</span>
        <p>Opening your workspace…</p>
      </div>
    );
  return (
    <WorkspaceShell
      workspace={workspace}
      storageError={error}
      update={update}
    />
  );
}
function WorkspaceShell({
  workspace,
  storageError,
  update,
}: {
  workspace: Workspace;
  storageError: string | null;
  update: (change: Workspace | ((s: Workspace) => Workspace)) => void;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const parts = pathname.split("/").filter(Boolean);
  const symbolResult =
    parts[0] === "stock" ? symbolSchema.safeParse(parts[1] ?? "") : null;
  const symbol = symbolResult?.success ? symbolResult.data : undefined;
  const newsSector =
    parts[0] === "news" && parts.length <= 2
      ? sectorIds.find((id) => id === (parts[1] ?? "all"))
      : undefined;
  const watchlist =
    parts[0] === "watchlist" && parts.length === 2
      ? workspace.watchlists.find(
          (list) => encodeURIComponent(list.id) === parts[1],
        )
      : undefined;
  const pageId =
    parts.length === 0
      ? "home"
      : newsSector
        ? `news-${newsSector}`
        : parts[0] === "dashboard" && parts.length === 2
          ? parts[1]
          : "missing";
  const page =
    parts.length <= 2 ? resolvePage(workspace, pageId, symbol) : undefined;
  const [editing, setEditing] = useState(false);
  const [dialog, setDialog] = useState<
    "search" | "library" | "settings" | "new" | "rename" | "help" | null
  >(null);
  const [configId, setConfigId] = useState<string | null>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [toast, setToast] = useState("");
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notify = (message: string) => {
    setToast(message);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 3500);
  };
  useEffect(
    () => () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    },
    [],
  );
  useEffect(() => {
    document.documentElement.dataset.theme = workspace.theme;
  }, [workspace.theme]);
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (
        event.key === "/" &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) &&
        !document.querySelector("dialog[open]")
      ) {
        event.preventDefault();
        setDialog("search");
      }
    };
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, []);
  const navigate = (url: string) => {
    router.push(url);
    setMobileNav(false);
    setEditing(false);
    setConfigId(null);
  };
  const openSymbol = (ticker: string) =>
    navigate(`/stock/${encodeURIComponent(ticker)}`);
  const changePage = (change: (page: WorkspacePage) => WorkspacePage) => {
    update((state) => {
      const current = resolvePage(state, pageId, symbol);
      if (!current) return state;
      const changed = change(current);
      const next = {
        ...changed,
        layout: reconcileLayout(current.layout, changed.layout),
      };
      if (newsSector)
        return {
          ...state,
          newsPages: { ...state.newsPages, [newsSector]: next },
        };
      if (symbol)
        return state.stockOverrides[symbol]
          ? {
              ...state,
              stockOverrides: { ...state.stockOverrides, [symbol]: next },
            }
          : { ...state, stockTemplate: next };
      return {
        ...state,
        pages: state.pages.map((p) => (p.id === pageId ? next : p)),
      };
    });
  };
  const patchWidget = (widget: Widget) =>
    changePage((page) => ({
      ...page,
      widgets: page.widgets.map((w) => (w.id === widget.id ? widget : w)),
    }));
  const onAdd = (type: WidgetType) => {
    changePage((page) =>
      addWidget(
        page,
        type,
        type === "market"
          ? (workspace.watchlists.find((list) => list.id === "benchmarks")
              ?.id ?? workspace.watchlists[0].id)
          : workspace.watchlists[0].id,
      ),
    );
    setDialog(null);
    setEditing(true);
    notify(
      `${type === "chart" ? "Price chart" : type[0].toUpperCase() + type.slice(1)} added`,
    );
  };
  const customStock = !!(symbol && workspace.stockOverrides[symbol]);
  const config = page?.widgets.find((w) => w.id === configId);
  function resetPage() {
    if (
      !confirm(
        "Restore this page's default layout and widgets? Your watchlists and notes will be kept.",
      )
    )
      return;
    changePage((current) => {
      const next = newsSector
        ? newsPage(newsSector, workspace.watchlists[0].id)
        : symbol
          ? stockPage()
          : pageId === "home"
            ? homePage()
            : { ...current, widgets: [], layout: [] };
      return {
        ...next,
        id: current.id,
        name: current.name,
        widgets: next.widgets.map((w) => ({
          ...w,
          watchlistId: workspace.watchlists.some(
            (list) => list.id === w.watchlistId,
          )
            ? w.watchlistId
            : workspace.watchlists[0].id,
        })),
      };
    });
    notify("Default layout restored");
  }
  return (
    <div className="app-shell">
      {mobileNav && (
        <button
          className="nav-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileNav(false)}
        />
      )}
      <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
        <Link
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            navigate("/");
          }}
        >
          <span className="brand-mark">
            f<span>◦</span>
          </span>
          folio<span className="brand-beta">BETA</span>
        </Link>
        <button
          className="workspace-switch"
          onClick={() => setDialog("settings")}
        >
          <span className="workspace-avatar">P</span>
          <span>
            Personal workspace<small>Make room for your ideas</small>
          </span>
          <ChevronDown size={13} />
        </button>
        <button className="sidebar-search" onClick={() => setDialog("search")}>
          <Search size={16} />
          Find a symbol<kbd>/</kbd>
        </button>
        <div className="nav-label">
          WORKSPACE
          <button
            aria-label="Create dashboard"
            className="icon-button"
            onClick={() => setDialog("new")}
            disabled={workspace.pages.length >= 20}
          >
            <Plus size={15} />
          </button>
        </div>
        <nav aria-label="Dashboards">
          <button
            className={`nav-item ${watchlist ? "selected" : ""}`}
            onClick={() =>
              navigate(
                `/watchlist/${encodeURIComponent(workspace.watchlists[0].id)}`,
              )
            }
          >
            <Bookmark size={17} />
            <span>Watchlists</span>
          </button>
          <button
            className={`nav-item ${newsSector ? "selected" : ""}`}
            onClick={() => navigate("/news")}
          >
            <FileText size={17} />
            <span>Market news</span>
          </button>
          {workspace.pages.map((p) => (
            <button
              key={p.id}
              className={`nav-item ${!symbol && pageId === p.id ? "selected" : ""}`}
              onClick={() =>
                navigate(p.id === "home" ? "/" : `/dashboard/${p.id}`)
              }
            >
              {p.id === "home" ? (
                <LayoutDashboard size={17} />
              ) : (
                <LayoutGrid size={17} />
              )}
              <span>{p.name}</span>
              {p.id === "home" && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="nav-label watchlist-nav-label">
          QUICK ACCESS
          <Bookmark size={12} />
        </div>
        <nav className="stock-nav" aria-label="Quick access stocks">
          {workspace.watchlists[0].symbols.slice(0, 8).map((ticker) => (
            <button
              key={ticker}
              className={`nav-item ${symbol === ticker ? "selected" : ""}`}
              onClick={() => openSymbol(ticker)}
            >
              <span className={`tiny-symbol color-${ticker.charCodeAt(0) % 5}`}>
                {ticker[0]}
              </span>
              <span>{ticker}</span>
              <span className="nav-stock-name">
                {companies[ticker]?.[0].split(" ")[0] ?? "Stock"}
              </span>
            </button>
          ))}
        </nav>
        <button
          className="add-dashboard"
          onClick={() => setDialog("new")}
          disabled={workspace.pages.length >= 20}
        >
          <Plus size={15} />
          New dashboard
        </button>
        <div className="sidebar-bottom">
          <div className="workspace-tip">
            <span className="tip-icon">
              <LayoutGrid size={19} />
            </span>
            <strong>A little more you.</strong>
            <p>Arrange your workspace around the way you think.</p>
            <button
              onClick={() => {
                setEditing(true);
                setDialog("library");
              }}
            >
              Explore widgets <ArrowUpRight size={13} />
            </button>
          </div>
          <button className="nav-item" onClick={() => setDialog("help")}>
            <CircleHelp size={16} />
            Getting started
          </button>
          <button className="nav-item" onClick={() => setDialog("settings")}>
            <Settings2 size={16} />
            Settings & preferences
          </button>
          <div className="sidebar-profile">
            <span className="profile-avatar">P</span>
            <span>
              Personal account<small>Local workspace</small>
            </span>
            <button
              className="icon-button"
              aria-label={`Switch to ${workspace.theme === "dark" ? "light" : "dark"} theme`}
              onClick={() =>
                update((s) => ({
                  ...s,
                  theme: s.theme === "dark" ? "light" : "dark",
                }))
              }
            >
              {workspace.theme === "dark" ? (
                <Sun size={16} />
              ) : (
                <Moon size={16} />
              )}
            </button>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-button mobile-menu"
              aria-label="Open navigation"
              onClick={() => setMobileNav(true)}
            >
              <Menu size={19} />
            </button>
            <LayoutGrid size={15} />
            <span>Workspace</span>
            <span className="breadcrumb-divider">/</span>
            <strong>
              {watchlist?.name ?? symbol ?? page?.name ?? "Not found"}
            </strong>
          </div>
          <div className="topbar-right">
            <span className="local-status">
              <span />
              {storageError ? "Storage needs attention" : "Saved locally"}
            </span>
            <span className="topbar-divider" />
            <button
              className="icon-button"
              aria-label="Search stocks"
              onClick={() => setDialog("search")}
            >
              <Search size={17} />
            </button>
            <span className="profile-avatar small">P</span>
          </div>
        </header>
        <main className="main-content">
          {watchlist ? (
            <>
              {storageError && (
                <div className="warning-banner" role="alert">
                  {storageError}
                </div>
              )}
              <WatchlistPage
                key={watchlist.id}
                list={watchlist}
                workspace={workspace}
                update={update}
                navigate={navigate}
              />
            </>
          ) : !page ? (
            <div className="empty-page">
              <LayoutGrid size={36} />
              <h1>This page isn’t in your workspace</h1>
              <p>It may have been removed or imported from another browser.</p>
              <button className="button primary" onClick={() => navigate("/")}>
                Go to overview
              </button>
            </div>
          ) : (
            <>
              <div className="page-heading">
                <div>
                  <div className="eyebrow page-eyebrow">
                    {symbol ? (
                      <>
                        <button
                          className="text-button"
                          onClick={() => navigate("/")}
                        >
                          <ArrowLeft size={12} />
                          Overview
                        </button>
                        <span>/ COMPANY RESEARCH</span>
                      </>
                    ) : (
                      <>
                        <span className="eyebrow-dot" />
                        YOUR PERSONAL MARKET PERSPECTIVE
                      </>
                    )}
                  </div>
                  <div className="page-title">
                    <h1>{symbol ? symbol : page.name}</h1>
                    {symbol && (
                      <span className="stock-company-name">
                        {companies[symbol]?.[0] ?? "Stock research"}
                      </span>
                    )}
                    <span className="page-badge">
                      {symbol ? "US" : "WORKSPACE"}
                    </span>
                  </div>
                  <p>
                    {newsSector
                      ? "Market-wide headlines, organized by the sectors you follow. Each sector has its own customizable view."
                      : symbol
                        ? "The details that matter. A research space that works your way."
                        : "A clear view of your markets. A space to make your own."}
                  </p>
                </div>
                <div className="page-actions">
                  <button
                    className={`button ${editing ? "editing-active" : ""}`}
                    onClick={() => setEditing(!editing)}
                  >
                    {editing ? (
                      <Check size={15} />
                    ) : (
                      <SlidersHorizontal size={15} />
                    )}
                    {editing ? "Done editing" : "Edit layout"}
                  </button>
                  <button
                    className="button primary"
                    disabled={page.widgets.length >= 30}
                    onClick={() => setDialog("library")}
                  >
                    <Plus size={16} />
                    Add widget
                  </button>
                </div>
              </div>
              {newsSector && (
                <nav className="sector-tabs" aria-label="News sectors">
                  {sectorIds.map((sector) => (
                    <button
                      key={sector}
                      aria-pressed={newsSector === sector}
                      className={newsSector === sector ? "active" : ""}
                      onClick={() =>
                        navigate(sector === "all" ? "/news" : `/news/${sector}`)
                      }
                    >
                      {sectorLabels[sector]}
                    </button>
                  ))}
                </nav>
              )}
              <div className="workspace-meta">
                <div>
                  <span className="status-dot" />
                  {workspace.dataMode === "demo"
                    ? "Demo workspace"
                    : "Free public data"}
                  <span className="meta-dot">·</span>
                  <span>
                    {workspace.dataMode === "demo"
                      ? "Illustrative prices, headlines, and events"
                      : "Delayed feeds · Demo fallbacks labeled per widget"}
                  </span>
                </div>
                <button
                  className="text-button"
                  onClick={() => setDialog("settings")}
                >
                  Data preferences <ArrowUpRight size={12} />
                </button>
              </div>
              {storageError && (
                <div className="warning-banner" role="alert">
                  {storageError}
                </div>
              )}
              {symbol && (
                <div className="stock-template-bar">
                  <span>
                    <Copy size={14} />
                    {customStock
                      ? `Custom layout for ${symbol}`
                      : "Shared stock template"}
                  </span>
                  <span className="muted">
                    {customStock
                      ? "Changes apply only to this ticker."
                      : "Layout changes apply to all stocks using this template. Notes stay ticker-specific."}
                  </span>
                  <button
                    className="text-button"
                    onClick={() => {
                      if (customStock) {
                        if (
                          !confirm(
                            "Remove this ticker’s custom layout and use the shared stock template?",
                          )
                        )
                          return;
                        update((s) => {
                          const overrides = { ...s.stockOverrides };
                          delete overrides[symbol];
                          return { ...s, stockOverrides: overrides };
                        });
                      } else
                        update((s) => ({
                          ...s,
                          stockOverrides: {
                            ...s.stockOverrides,
                            [symbol]: structuredClone(s.stockTemplate),
                          },
                        }));
                    }}
                  >
                    {customStock
                      ? "Use shared template"
                      : "Customize only this stock"}
                  </button>
                </div>
              )}
              {editing && (
                <div className="editing-bar">
                  <span>
                    <Grip size={16} />
                    Drag to swap or snap nearby · Pull a corner to resize ·
                    Settings include keyboard-friendly positioning
                  </span>
                  <div>
                    <button
                      className="text-button"
                      onClick={() => setDialog("rename")}
                    >
                      <Pencil size={13} />
                      Rename
                    </button>
                    <button className="text-button" onClick={resetPage}>
                      <RotateCcw size={13} />
                      Reset
                    </button>
                    {pageId !== "home" && !symbol && !newsSector && (
                      <button
                        className="text-button danger"
                        onClick={() => {
                          if (confirm(`Delete ${page.name} and its widgets?`)) {
                            update((s) => ({
                              ...s,
                              pages: s.pages.filter((p) => p.id !== pageId),
                            }));
                            navigate("/");
                          }
                        }}
                      >
                        <Trash2 size={13} />
                        Delete page
                      </button>
                    )}
                  </div>
                </div>
              )}
              <DashboardGrid
                key={symbol ?? pageId}
                page={page}
                workspace={workspace}
                symbol={symbol}
                editing={editing}
                openSymbol={openSymbol}
                update={update}
                changePage={changePage}
                patchWidget={patchWidget}
                configure={(widget) => setConfigId(widget.id)}
              />
              {!page.widgets.length && (
                <div className="empty-page">
                  <LayoutGrid size={42} />
                  <h2>A blank canvas for your next idea</h2>
                  <p>Add a widget to start building this dashboard.</p>
                  <button
                    className="button primary"
                    onClick={() => setDialog("library")}
                  >
                    <Plus size={16} />
                    Add your first widget
                  </button>
                </div>
              )}
              <button
                className="add-widget-strip"
                disabled={page.widgets.length >= 30}
                onClick={() => setDialog("library")}
              >
                <Plus size={16} />
                <span>Add a widget</span>
                <span className="muted">Make this view work for you</span>
              </button>
              <footer className="page-footer">
                <span>
                  <span className="footer-brand">f.</span> YOUR MARKETS. YOUR
                  WAY.
                </span>
                <span>
                  {page.widgets.length} widgets
                  <span className="meta-dot">·</span>Local storage
                  <span className="meta-dot">·</span>No subscriptions
                </span>
              </footer>
            </>
          )}
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <Check size={16} />
          {toast}
        </div>
      )}
      {dialog === "search" && (
        <SearchDialog onClose={() => setDialog(null)} onSelect={openSymbol} />
      )}
      {dialog === "library" && page && (
        <WidgetLibrary onClose={() => setDialog(null)} onAdd={onAdd} />
      )}
      {dialog === "settings" && (
        <WorkspaceSettings
          workspace={workspace}
          update={update}
          onClose={() => setDialog(null)}
          notify={notify}
        />
      )}
      {dialog === "new" && (
        <NameDialog
          title="Create a dashboard"
          onClose={() => setDialog(null)}
          onSave={(name) => {
            if (workspace.pages.length >= 20) return;
            const id = crypto.randomUUID();
            update((s) => ({
              ...s,
              pages: [...s.pages, { id, name, widgets: [], layout: [] }],
            }));
            navigate(`/dashboard/${id}`);
            notify("Dashboard created");
          }}
        />
      )}
      {dialog === "rename" && page && (
        <NameDialog
          title={symbol ? "Rename stock layout" : "Rename dashboard"}
          initial={page.name}
          onClose={() => setDialog(null)}
          onSave={(name) => changePage((p) => ({ ...p, name }))}
        />
      )}
      {config && page && (
        <WidgetSettings
          key={config.id}
          widget={config}
          position={page.layout.find((p) => p.i === config.id)!}
          workspace={workspace}
          onClose={() => setConfigId(null)}
          onSave={(widget, position) => {
            changePage((p) => ({
              ...p,
              widgets: p.widgets.map((w) => (w.id === widget.id ? widget : w)),
              layout: p.layout.map((l) => (l.i === position.i ? position : l)),
            }));
            notify("Widget settings saved");
          }}
        />
      )}
      {dialog === "help" && (
        <Modal
          title="A workspace that thinks like you"
          subtitle="Everything starts with a widget."
          onClose={() => setDialog(null)}
        >
          <div className="help-steps">
            <p>
              <strong>01 · Make it yours</strong>Choose Edit layout to drag and
              resize widgets. Use each widget’s settings to change its content,
              ticker, or grid position.
            </p>
            <p>
              <strong>02 · Follow your ideas</strong>Add tickers to a watchlist,
              then click a company to open its modular research page. Press / to
              search any US ticker.
            </p>
            <p>
              <strong>03 · Create different perspectives</strong>Build separate
              dashboards for different workflows. Stock pages share a template
              until you choose a ticker-specific layout.
            </p>
            <p>
              <strong>04 · Keep your work</strong>Changes save in this browser
              automatically. Export your workspace in Settings for a backup or
              to move to another device.
            </p>
            <p className="help-text">
              Free data is best-effort. DEMO labels indicate illustrative
              values, never real market prices or actual earnings dates. Hover
              source labels for timestamps. Mobile devices show widgets in a
              single column; desktop layouts stay intact.
            </p>
          </div>
        </Modal>
      )}
    </div>
  );
}

function DashboardGrid({
  page,
  workspace,
  symbol,
  editing,
  openSymbol,
  update,
  changePage,
  patchWidget,
  configure,
}: {
  page: WorkspacePage;
  workspace: Workspace;
  symbol?: string;
  editing: boolean;
  openSymbol: (s: string) => void;
  update: (change: (s: Workspace) => Workspace) => void;
  changePage: (change: (p: WorkspacePage) => WorkspacePage) => void;
  patchWidget: (w: Widget) => void;
  configure: (w: Widget) => void;
}) {
  const { width, containerRef, mounted } = useContainerWidth({
    initialWidth: 1200,
  });
  const mobile = width < 800;
  const ordered = [...page.layout].sort((a, b) => a.y - b.y || a.x - b.x);
  let mobileRow = 0;
  const layout = mobile
    ? ordered.map((l) => {
        const h =
          page.widgets.find((w) => w.id === l.i)?.type === "watchlist" ? 14 : 9;
        const position = { ...l, x: 0, y: mobileRow, w: 12, h };
        mobileRow += h;
        return position;
      })
    : page.layout.map((l) => ({ ...l, minW: 2, minH: 3, maxH: 30 }));
  const beforeInteraction = useRef<LayoutItem[] | null>(null);
  const rememberLayout = () => {
    beforeInteraction.current = page.layout.map((item) => ({ ...item }));
  };
  const saveItem = (item: LayoutItem, kind: "move" | "resize") => {
    if (!mobile) {
      const previous = beforeInteraction.current ?? page.layout;
      changePage((p) => ({
        ...p,
        layout: resolveLayoutMove(
          previous,
          { i: item.i, x: item.x, y: item.y, w: item.w, h: item.h },
          kind,
        ),
      }));
    }
    beforeInteraction.current = null;
  };
  return (
    <div
      ref={containerRef}
      className={`dashboard-grid ${editing ? "is-editing" : ""}`}
      data-testid="dashboard-grid"
    >
      {mounted && (
        <GridLayout
          width={width}
          cols={12}
          rowHeight={34}
          margin={[16, 16]}
          containerPadding={[0, 0]}
          layout={layout}
          compactType={null}
          allowOverlap
          isDraggable={editing && !mobile}
          isResizable={editing && !mobile}
          draggableHandle=".widget-drag-handle"
          draggableCancel="button,a,input,select,textarea"
          onDragStart={rememberLayout}
          onResizeStart={rememberLayout}
          onDragStop={(_layout, _oldItem, item) => {
            if (item) saveItem(item, "move");
          }}
          onResizeStop={(_layout, _oldItem, item) => {
            if (item) saveItem(item, "resize");
          }}
        >
          {page.widgets.map((widget) => (
            <section
              key={widget.id}
              className={`widget-card widget-${widget.type}`}
              data-testid={`widget-${widget.id}`}
              aria-label={widget.title}
            >
              <header className="widget-header">
                <div
                  className="widget-drag-handle"
                  title={editing ? "Drag to move widget" : widget.title}
                >
                  {editing ? (
                    <Grip size={15} />
                  ) : widget.type === "watchlist" ? (
                    <Bookmark size={14} />
                  ) : widget.type === "news" || widget.type === "notes" ? (
                    <FileText size={14} />
                  ) : widget.type === "chart" || widget.type === "movers" ? (
                    <TrendingUp size={14} />
                  ) : (
                    <LayoutGrid size={14} />
                  )}
                  <h2>
                    {widget.type === "watchlist" && !editing ? (
                      <Link
                        className="widget-title-link"
                        href={`/watchlist/${encodeURIComponent((workspace.watchlists.find((list) => list.id === widget.watchlistId) ?? workspace.watchlists[0]).id)}`}
                      >
                        {widget.title}
                      </Link>
                    ) : (
                      widget.title
                    )}
                  </h2>
                </div>
                <div className="widget-controls">
                  <button
                    className="icon-button"
                    aria-label={`Settings for ${widget.title}`}
                    onClick={() => configure(widget)}
                  >
                    <SlidersHorizontal size={13} />
                  </button>
                  {editing && (
                    <>
                      <button
                        className="icon-button"
                        aria-label={`Expand ${widget.title}`}
                        onClick={() =>
                          changePage((p) => ({
                            ...p,
                            layout: p.layout.map((l) =>
                              l.i === widget.id
                                ? { ...l, x: 0, w: l.w === 12 ? 6 : 12 }
                                : l,
                            ),
                          }))
                        }
                      >
                        <Maximize2 size={13} />
                      </button>
                      <button
                        className="icon-button"
                        aria-label={`Remove ${widget.title}`}
                        onClick={() => {
                          if (
                            confirm(`Remove ${widget.title} from this layout?`)
                          )
                            changePage((p) => removeWidget(p, widget.id));
                        }}
                      >
                        <X size={14} />
                      </button>
                    </>
                  )}
                </div>
              </header>
              <div className="widget-body">
                <WidgetContent
                  widget={widget}
                  workspace={workspace}
                  symbol={symbol}
                  pageKey={symbol ? `stock-${symbol}` : page.id}
                  openSymbol={openSymbol}
                  update={update}
                  configure={configure}
                  patchWidget={patchWidget}
                />
              </div>
            </section>
          ))}
        </GridLayout>
      )}
    </div>
  );
}
