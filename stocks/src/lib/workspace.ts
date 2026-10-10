import { z } from "zod";
import { calculatePosition, tradeSchema } from "./portfolio";
import { taxProfileSchema } from "./tax";
import {
  defaultMetrics,
  metricIds,
  sectorIds,
  sectorLabels,
  type Sector,
} from "./market/research";
export { settleLayout } from "./layout";

export const STORAGE_KEY = "folio.workspace.v1";
export const symbolSchema = z
  .string()
  .trim()
  .toUpperCase()
  .regex(
    /^[A-Z][A-Z0-9.^=-]{0,14}$/,
    "Enter a valid ticker, such as AAPL or BRK-B",
  );
export const widgetTypes = [
  "watchlist",
  "trades",
  "chart",
  "quote",
  "metrics",
  "analyst",
  "movers",
  "market",
  "news",
  "earnings",
  "notes",
] as const;
export type WidgetType = (typeof widgetTypes)[number];
export const ranges = ["1d", "1mo", "3mo", "1y", "5y"] as const;
export type Range = (typeof ranges)[number];
export const widgetCatalog: Record<
  WidgetType,
  { name: string; description: string; category: string }
> = {
  watchlist: {
    name: "Watchlist",
    description: "Your symbols, prices, and daily performance in one place.",
    category: "Markets",
  },
  trades: {
    name: "My trades",
    description:
      "Your recorded purchases and sales, with shares, prices, fees, and totals.",
    category: "Portfolio",
  },
  chart: {
    name: "Price chart",
    description: "Explore price history from one day to five years.",
    category: "Markets",
  },
  quote: {
    name: "Key statistics",
    description: "A focused view of price, volume, and trading ranges.",
    category: "Markets",
  },
  metrics: {
    name: "Advanced metrics",
    description:
      "EPS, valuation multiples, margins, and growth. Show only the metrics you need.",
    category: "Research",
  },
  analyst: {
    name: "Analyst trend",
    description:
      "Five-level analyst consensus for a stock or a watchlist market proxy.",
    category: "Research",
  },
  movers: {
    name: "Daily movers",
    description: "Find the biggest moves within your selected watchlist.",
    category: "Markets",
  },
  market: {
    name: "Market overview",
    description: "Track broad markets with a set of benchmark ETFs.",
    category: "Markets",
  },
  news: {
    name: "News feed",
    description: "The latest headlines for the companies you follow.",
    category: "Research",
  },
  earnings: {
    name: "Earnings calendar",
    description: "Company reporting schedules by day.",
    category: "Research",
  },
  notes: {
    name: "Research notes",
    description: "Keep your thesis, questions, and next steps close.",
    category: "Research",
  },
};
const idSchema = z
  .string()
  .min(1)
  .max(80)
  .regex(/^[a-zA-Z0-9_-]+$/);
export const widgetSchema = z.object({
  id: idSchema,
  type: z.enum(widgetTypes),
  title: z.string().max(80),
  symbol: symbolSchema.default("AAPL"),
  followSymbol: z.boolean().default(true),
  range: z.enum(ranges).default("1mo"),
  watchlistId: idSchema.default("core"),
  sort: z.enum(["symbol", "change", "price"]).default("symbol"),
  direction: z.enum(["gainers", "losers"]).default("gainers"),
  showVolume: z.boolean().default(true),
  showChart: z.boolean().default(true),
  showPositions: z.boolean().default(false),
  analystScope: z.enum(["stock", "watchlist"]).default("stock"),
  metrics: z
    .array(z.enum(metricIds))
    .max(metricIds.length)
    .default(defaultMetrics),
  newsScope: z.enum(["company", "market"]).default("company"),
  sector: z.enum(sectorIds).default("all"),
  newsView: z.enum(["cards", "list", "sectors"]).default("cards"),
  earningsDate: z
    .string()
    .refine(
      (s) =>
        !s ||
        (/^\d{4}-\d{2}-\d{2}$/.test(s) &&
          !isNaN(new Date(s).getTime()) &&
          new Date(s).toISOString().slice(0, 10) === s),
    )
    .default(""),
  limit: z.number().int().min(1).max(120).default(8),
});
export type Widget = z.infer<typeof widgetSchema>;
export const layoutItemSchema = z
  .object({
    i: idSchema,
    x: z.number().int().min(0).max(11),
    y: z.number().int().min(0).max(10000),
    w: z.number().int().min(2).max(12),
    h: z.number().int().min(3).max(30),
  })
  .refine((item) => item.x + item.w <= 12, "Widget extends outside the grid");
export type LayoutItem = z.infer<typeof layoutItemSchema>;
export const pageSchema = z
  .object({
    id: idSchema,
    name: z.string().trim().min(1).max(60),
    widgets: z.array(widgetSchema).max(30),
    layout: z.array(layoutItemSchema).max(30),
  })
  .superRefine((page, ctx) => {
    const ids = page.widgets.map((w) => w.id);
    if (
      new Set(ids).size !== ids.length ||
      new Set(page.layout.map((l) => l.i)).size !== page.layout.length ||
      page.layout.length !== ids.length ||
      page.layout.some((l) => !ids.includes(l.i))
    ) {
      ctx.addIssue({
        code: "custom",
        message: "Each widget must have exactly one unique grid position",
      });
    }
  });
export type WorkspacePage = z.infer<typeof pageSchema>;
export const workspaceSchema = z
  .object({
    version: z.literal(1),
    theme: z.enum(["dark", "light"]),
    dataMode: z.enum(["auto", "demo"]),
    pages: z.array(pageSchema).min(1).max(20),
    stockTemplate: pageSchema,
    stockOverrides: z.record(symbolSchema, pageSchema),
    newsPages: z.partialRecord(z.enum(sectorIds), pageSchema).default({}),
    researchVersion: z.literal(1).optional(),
    portfolioVersion: z.literal(1).optional(),
    watchlists: z
      .array(
        z.object({
          id: idSchema,
          name: z.string().trim().min(1).max(40),
          symbols: z.array(symbolSchema).max(30),
        }),
      )
      .min(1)
      .max(20),
    notes: z.record(z.string().max(120), z.string().max(20000)),
    trades: z.record(symbolSchema, z.array(tradeSchema).max(5000)).default({}),
    taxProfile: taxProfileSchema.nullable().default(null),
  })
  .superRefine((state, ctx) => {
    for (const [symbol, trades] of Object.entries(state.trades)) {
      try {
        calculatePosition(trades);
      } catch {
        ctx.addIssue({
          code: "custom",
          message: `Invalid trade history for ${symbol}`,
          path: ["trades", symbol],
        });
      }
    }
    if (
      !state.pages.some((p) => p.id === "home") ||
      new Set(state.pages.map((p) => p.id)).size !== state.pages.length ||
      new Set(state.watchlists.map((w) => w.id)).size !==
        state.watchlists.length
    ) {
      ctx.addIssue({
        code: "custom",
        message:
          "Workspace must have a home page and unique page/watchlist IDs",
      });
    }
    for (const page of [
      ...state.pages,
      state.stockTemplate,
      ...Object.values(state.stockOverrides),
      ...Object.values(state.newsPages),
    ]) {
      if (
        page.widgets.some(
          (w) => !state.watchlists.some((list) => list.id === w.watchlistId),
        )
      )
        ctx.addIssue({
          code: "custom",
          message: "Widget refers to a missing watchlist",
        });
    }
  });
export type Workspace = z.infer<typeof workspaceSchema>;
export function makeWidget(type: WidgetType, id = crypto.randomUUID()): Widget {
  return widgetSchema.parse({
    id,
    type,
    title: widgetCatalog[type].name,
    ...(type === "market" ? { watchlistId: "benchmarks", limit: 4 } : {}),
  });
}
export function homePage(): WorkspacePage {
  return {
    id: "home",
    name: "Overview",
    widgets: [
      makeWidget("market", "market"),
      makeWidget("watchlist", "watchlist"),
      makeWidget("movers", "movers"),
      makeWidget("news", "news"),
      makeWidget("earnings", "earnings"),
    ],
    layout: [
      { i: "market", x: 0, y: 0, w: 3, h: 7 },
      { i: "watchlist", x: 3, y: 0, w: 6, h: 14 },
      { i: "movers", x: 9, y: 0, w: 3, h: 7 },
      { i: "news", x: 0, y: 7, w: 3, h: 7 },
      { i: "earnings", x: 9, y: 7, w: 3, h: 7 },
    ],
  };
}
export function stockPage(): WorkspacePage {
  return {
    id: "stock-template",
    name: "Stock template",
    widgets: [
      makeWidget("chart", "stock-chart"),
      makeWidget("quote", "stock-quote"),
      makeWidget("news", "stock-news"),
      makeWidget("notes", "stock-notes"),
      makeWidget("metrics", "stock-metrics"),
      makeWidget("analyst", "stock-analyst"),
    ],
    layout: [
      { i: "stock-chart", x: 0, y: 0, w: 8, h: 9 },
      { i: "stock-quote", x: 8, y: 0, w: 4, h: 9 },
      { i: "stock-news", x: 0, y: 9, w: 7, h: 8 },
      { i: "stock-notes", x: 7, y: 9, w: 5, h: 8 },
      { i: "stock-metrics", x: 0, y: 17, w: 12, h: 7 },
      { i: "stock-analyst", x: 0, y: 24, w: 6, h: 10 },
    ],
  };
}
export function initialWorkspace(): Workspace {
  return {
    version: 1,
    theme: "dark",
    dataMode: "auto",
    pages: [homePage()],
    stockTemplate: stockPage(),
    stockOverrides: {},
    newsPages: {},
    researchVersion: 1,
    portfolioVersion: 1,
    watchlists: [
      {
        id: "core",
        name: "Core watchlist",
        symbols: [
          "AAPL",
          "MSFT",
          "NVDA",
          "GOOGL",
          "AMZN",
          "META",
          "TSLA",
          "AMD",
        ],
      },
      {
        id: "benchmarks",
        name: "Benchmarks",
        symbols: ["SPY", "QQQ", "DIA", "IWM"],
      },
    ],
    notes: {},
    trades: {},
    taxProfile: null,
  };
}
export function parseWorkspace(raw: string): Workspace {
  if (raw.length > 2_000_000)
    throw new Error("Workspace file is too large (maximum 2 MB)");
  const result = workspaceSchema.safeParse(JSON.parse(raw));
  if (!result.success)
    throw new Error(
      "Invalid workspace file. Please import a Folio version 1 export.",
    );
  return upgradePortfolioWorkspace(upgradeResearchWorkspace(result.data));
}
function upgradePortfolioWorkspace(state: Workspace): Workspace {
  if (state.portfolioVersion) return state;
  const upgrade = (page: WorkspacePage) =>
    page.widgets.some((w) => w.type === "analyst") || page.widgets.length >= 30
      ? page
      : addWidget(page, "analyst", state.watchlists[0].id);
  return {
    ...state,
    portfolioVersion: 1,
    stockTemplate: upgrade(state.stockTemplate),
    stockOverrides: Object.fromEntries(
      Object.entries(state.stockOverrides).map(([symbol, page]) => [
        symbol,
        upgrade(page),
      ]),
    ),
  };
}
export function newsPage(
  sector: Sector = "all",
  watchlistId = "core",
): WorkspacePage {
  return {
    id: `news-${sector}`,
    name: sector === "all" ? "Market news" : `${sectorLabels[sector]} news`,
    widgets: [
      {
        ...makeWidget("news", "market-news"),
        title: "Market headlines",
        newsScope: "market",
        followSymbol: false,
        sector,
        limit: 20,
        watchlistId,
      },
    ],
    layout: [{ i: "market-news", x: 0, y: 0, w: 12, h: 16 }],
  };
}
function upgradeResearchWorkspace(state: Workspace): Workspace {
  if (state.researchVersion) return state;
  const upgrade = (page: WorkspacePage) =>
    page.widgets.some((w) => w.type === "metrics") || page.widgets.length >= 30
      ? page
      : addWidget(page, "metrics", state.watchlists[0].id);
  return {
    ...state,
    researchVersion: 1,
    stockTemplate: upgrade(state.stockTemplate),
    stockOverrides: Object.fromEntries(
      Object.entries(state.stockOverrides).map(([symbol, page]) => [
        symbol,
        upgrade(page),
      ]),
    ),
  };
}
export function addWidget(
  page: WorkspacePage,
  type: WidgetType,
  watchlistId = "core",
): WorkspacePage {
  if (page.widgets.length >= 30) return page;
  const widget = { ...makeWidget(type), watchlistId };
  const y = Math.max(0, ...page.layout.map((item) => item.y + item.h));
  return {
    ...page,
    widgets: [...page.widgets, widget],
    layout: [
      ...page.layout,
      {
        i: widget.id,
        x: 0,
        y,
        w: ["chart", "watchlist", "trades"].includes(type) ? 6 : 4,
        h: 8,
      },
    ],
  };
}
export function removeWidget(page: WorkspacePage, id: string): WorkspacePage {
  return {
    ...page,
    widgets: page.widgets.filter((w) => w.id !== id),
    layout: page.layout.filter((l) => l.i !== id),
  };
}
export function resolvePage(
  state: Workspace,
  pageId: string,
  symbol?: string,
): WorkspacePage | undefined {
  if (!symbol && pageId.startsWith("news-")) {
    const sector = sectorIds.find((id) => pageId === `news-${id}`);
    if (sector)
      return (
        state.newsPages[sector] ?? newsPage(sector, state.watchlists[0].id)
      );
  }
  return symbol
    ? (state.stockOverrides[symbol] ?? state.stockTemplate)
    : state.pages.find((p) => p.id === pageId);
}
