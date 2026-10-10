import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  parseFundamentals,
  yahooFundamentals,
  clearFundamentalsSession,
} from "@/lib/market/fundamentals";
import { classifySectors } from "@/lib/market/research";
import { combineArticles, marketNews } from "@/lib/market/market-news";
import {
  getFundamentals,
  getMarketNews,
  clearCache,
} from "@/lib/market/service";
import {
  initialWorkspace,
  parseWorkspace,
  newsPage,
  resolvePage,
  workspaceSchema,
} from "@/lib/workspace";
import { GET } from "@/app/api/market/route";
import { NextRequest } from "next/server";

const raw = {
  quoteSummary: {
    result: [
      {
        price: { currency: "USD", marketCap: { raw: 3200000000000 } },
        summaryDetail: {
          trailingPE: { raw: 28.5 },
          forwardPE: { raw: 24.1 },
          dividendYield: { raw: 0.004 },
        },
        defaultKeyStatistics: {
          trailingEps: { raw: 6.2 },
          forwardEps: { raw: 7.4 },
        },
        financialData: {
          financialCurrency: "USD",
          debtToEquity: { raw: 142 },
          revenueGrowth: { raw: -0.05 },
          profitMargins: { raw: 0 },
        },
      },
    ],
  },
};
beforeEach(() => {
  clearCache();
  clearFundamentalsSession();
  vi.stubEnv("MARKET_DATA_MODE", "auto");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
describe("fundamentals", () => {
  it("normalizes raw metrics, zeroes, negatives, and percentage units", () => {
    const data = parseFundamentals(raw, "AAPL");
    expect(data.values).toMatchObject({
      trailingEps: 6.2,
      forwardPE: 24.1,
      dividendYield: 0.004,
      debtToEquity: 1.42,
      revenueGrowth: -0.05,
      profitMargins: 0,
      pegRatio: null,
    });
  });
  it("does not invent missing values or meaningful P/E for negative earnings", () => {
    const data = parseFundamentals(
      {
        quoteSummary: {
          result: [
            {
              defaultKeyStatistics: {
                trailingEps: { raw: -2 },
                trailingPE: { raw: 20 },
                forwardPE: { raw: -5 },
                beta: { raw: Infinity },
              },
            },
          ],
        },
      },
      "LOSS",
    );
    expect(data.values.trailingEps).toBe(-2);
    expect(data.values.trailingPE).toBeNull();
    expect(data.values.forwardPE).toBeNull();
    expect(data.values.beta).toBeNull();
    expect(() =>
      parseFundamentals({ quoteSummary: { result: null } }, "BAD"),
    ).toThrow();
    expect(() =>
      parseFundamentals({ quoteSummary: { result: [{}] } }, "BAD"),
    ).toThrow();
  });
  it("coalesces the public session handshake without exposing it to clients", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response("", {
          status: 404,
          headers: { "set-cookie": "A3=public-test-cookie; Path=/; Secure" },
        }),
      )
      .mockResolvedValueOnce(new Response("public-test-crumb"))
      .mockImplementation(async () => Response.json(raw));
    vi.stubGlobal("fetch", fetch);
    const data = await Promise.all([
      yahooFundamentals("AAPL"),
      yahooFundamentals("MSFT"),
    ]);
    expect(fetch).toHaveBeenCalledTimes(4);
    expect(fetch.mock.calls[1][1].headers.Accept).toBe("*/*");
    expect(data[0].values.trailingPE).toBe(28.5);
    expect(JSON.stringify(data)).not.toContain("public-test-cookie");
  });
  it("labels outages and demo mode explicitly, including unsupported ETF metrics", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("unavailable")));
    expect((await getFundamentals("AAPL", "auto")).source.demo).toBe(true);
    expect(
      Object.values((await getFundamentals("SPY", "demo")).data.values).every(
        (v) => v === null,
      ),
    ).toBe(true);
    await expect(getFundamentals("UNKNOWN", "demo")).rejects.toThrow();
  });
});
describe("market-wide news", () => {
  it("tags multiple sectors and keeps unmatched headlines in the market-wide category", () => {
    expect(
      classifySectors("Microsoft and JPMorgan discuss AI investment"),
    ).toEqual(["technology", "financials"]);
    expect(classifySectors("Federal Reserve weighs inflation outlook")).toEqual(
      ["macro"],
    );
    expect(classifySectors("Oil refinery growth")).toEqual(["energy"]);
  });
  it("deduplicates headlines and tracking URLs, retaining the newest item", () => {
    const items = combineArticles([
      {
        title: "Chip outlook",
        url: "https://example.com/a?utm_source=rss",
        publisher: "A",
        publishedAt: "2026-09-30T12:00:00Z",
      },
      {
        title: "Chip outlook",
        url: "https://example.com/b",
        publisher: "B",
        publishedAt: "2026-09-29T12:00:00Z",
      },
      {
        title: "Different headline",
        url: "https://example.com/a?utm_source=other",
        publisher: "A",
        publishedAt: "2026-09-28T12:00:00Z",
      },
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].url).toBe("https://example.com/a");
  });
  it("keeps available feeds visible when one provider fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response(
            "<rss><channel><item><title>Oil demand</title><link>https://example.com/oil</link><pubDate>Wed, 30 Sep 2026 12:00:00 GMT</pubDate></item></channel></rss>",
          ),
        )
        .mockRejectedValueOnce(new Error("offline")),
    );
    const result = await marketNews();
    expect(result.articles[0].sectors).toEqual(["energy"]);
    expect(result.unavailableFeeds).toEqual(["CNBC Markets"]);
  });
  it("returns a labeled diverse demo collection when all providers fail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const result = await getMarketNews("auto");
    expect(result.source.demo).toBe(true);
    expect(new Set(result.data.articles.flatMap((a) => a.sectors)).size).toBe(
      12,
    );
  });
  it("exposes metrics and market-news as validated API kinds", async () => {
    const metrics = await GET(
      new NextRequest(
        "http://localhost/api/market?kind=fundamentals&symbol=AAPL&mode=demo",
      ),
    );
    expect(metrics.status).toBe(200);
    expect((await metrics.json()).data.values.forwardPE).toBeGreaterThan(0);
    const news = await GET(
      new NextRequest("http://localhost/api/market?kind=market-news&mode=demo"),
    );
    expect(news.status).toBe(200);
    expect((await news.json()).data.articles.length).toBeGreaterThan(10);
  });
});
describe("saved workspace compatibility", () => {
  it("upgrades old stock views once without altering existing geometry or notes", () => {
    const old = initialWorkspace();
    delete old.researchVersion;
    old.stockTemplate.widgets = old.stockTemplate.widgets.filter(
      (w) => w.type !== "metrics",
    );
    old.stockTemplate.layout = old.stockTemplate.layout.filter(
      (l) => l.i !== "stock-metrics",
    );
    old.stockOverrides.AAPL = structuredClone(old.stockTemplate);
    old.notes["stock-AAPL:stock-notes"] = "Keep this research";
    const upgraded = parseWorkspace(JSON.stringify(old));
    expect(
      upgraded.stockTemplate.widgets.filter((w) => w.type === "metrics"),
    ).toHaveLength(1);
    expect(
      upgraded.stockTemplate.layout.slice(0, old.stockTemplate.layout.length),
    ).toEqual(old.stockTemplate.layout);
    expect(
      upgraded.stockOverrides.AAPL.widgets.filter((w) => w.type === "metrics"),
    ).toHaveLength(1);
    expect(upgraded.notes).toEqual(old.notes);
    expect(parseWorkspace(JSON.stringify(upgraded))).toEqual(upgraded);
  });
  it("keeps sector layouts independent and validates their watchlist references", () => {
    const state = initialWorkspace();
    state.newsPages.technology = newsPage("technology");
    state.newsPages.technology.widgets[0].newsView = "list";
    expect(resolvePage(state, "news-technology")?.widgets[0].newsView).toBe(
      "list",
    );
    expect(resolvePage(state, "news-energy")?.widgets[0].newsView).toBe(
      "cards",
    );
    expect(parseWorkspace(JSON.stringify(state))).toEqual(state);
    state.newsPages.technology.widgets[0].watchlistId = "missing";
    expect(workspaceSchema.safeParse(state).success).toBe(false);
  });
  it("preserves a deliberately empty metric selection", () => {
    const state = initialWorkspace();
    state.stockTemplate.widgets.find((w) => w.type === "metrics")!.metrics = [];
    expect(
      parseWorkspace(JSON.stringify(state)).stockTemplate.widgets.find(
        (w) => w.type === "metrics",
      )!.metrics,
    ).toEqual([]);
  });
});
