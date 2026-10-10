import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  parseYahooChart,
  safeUrl,
  nasdaqEarnings,
  yahooNews,
} from "@/lib/market/providers";
import {
  clearCache,
  getEarnings,
  getNews,
  getQuote,
} from "@/lib/market/service";
import { demoQuote } from "@/lib/market/demo";
import { GET } from "@/app/api/market/route";
import { NextRequest } from "next/server";

const chart = (previous = 100) => ({
  chart: {
    result: [
      {
        meta: {
          symbol: "AAPL",
          shortName: "Apple",
          currency: "USD",
          regularMarketPrice: 110,
          regularMarketTime: 1736974800,
          chartPreviousClose: previous,
          regularMarketVolume: 400000,
        },
        timestamp: [1, 2, 3],
        indicators: { quote: [{ close: [100, null, 110] }] },
      },
    ],
  },
});
beforeEach(() => {
  clearCache();
  vi.stubEnv("MARKET_DATA_MODE", "auto");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
describe("provider parsing", () => {
  it("computes daily change and removes missing chart points", () => {
    const quote = parseYahooChart(chart());
    expect(quote.change).toBe(10);
    expect(quote.changePercent).toBeCloseTo(10);
    expect(quote.points).toEqual([
      { time: 1, close: 100 },
      { time: 3, close: 110 },
    ]);
    expect(quote.high).toBeNull();
  });
  it("uses yesterday's close instead of the start-of-range close", () => {
    expect(parseYahooChart(chart(50), 100).changePercent).toBeCloseTo(10);
  });
  it("rejects missing, malformed, zero, and nonfinite prices", () => {
    expect(() => parseYahooChart({ chart: { result: null } })).toThrow();
    expect(() => parseYahooChart(chart(0))).toThrow();
    const invalid = chart();
    invalid.chart.result[0].meta.regularMarketPrice = NaN;
    expect(() => parseYahooChart(invalid)).toThrow();
  });
  it("only accepts web URLs for news links", () => {
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(safeUrl("file:///etc/passwd")).toBeNull();
    expect(safeUrl("https://example.com/story")).toBe(
      "https://example.com/story",
    );
  });
  it("parses RSS items while excluding unsafe links and invalid dates", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            "<rss><channel><item><title>Valid</title><link>https://example.com/news</link><pubDate>Wed, 15 Jan 2025 12:00:00 GMT</pubDate></item><item><title>Unsafe</title><link>javascript:alert(1)</link><pubDate>invalid</pubDate></item></channel></rss>",
          ),
        ),
    );
    expect(await yahooNews("AAPL")).toEqual([
      {
        title: "Valid",
        url: "https://example.com/news",
        publishedAt: "2025-01-15T12:00:00.000Z",
        publisher: "Yahoo Finance RSS",
      },
    ]);
  });
  it("rejects XML entity declarations", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response('<!DOCTYPE rss [<!ENTITY x "test">]><rss/>'),
        ),
    );
    await expect(yahooNews("AAPL")).rejects.toThrow("Invalid news feed");
  });
  it("supports an empty earnings day and normalizes session labels", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ data: { rows: null } }))
      .mockResolvedValueOnce(
        Response.json({
          data: {
            rows: [
              {
                symbol: "AAPL",
                name: "Apple",
                time: "time-after-hours",
                epsForecast: "$1.50",
              },
            ],
          },
        }),
      );
    vi.stubGlobal("fetch", fetch);
    expect(await nasdaqEarnings("2026-09-30")).toEqual([]);
    expect((await nasdaqEarnings("2026-10-01"))[0]).toMatchObject({
      time: "After close",
      date: "2026-10-01",
      estimate: "$1.50",
    });
  });
});
describe("free data fallback and cache", () => {
  it("coalesces concurrent quotes into one upstream request", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(async () => Response.json(chart()));
    vi.stubGlobal("fetch", fetch);
    const [a, b] = await Promise.all([
      getQuote("AAPL", "1d", "auto"),
      getQuote("AAPL", "1d", "auto"),
    ]);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(a).toEqual(b);
    expect(a.source.demo).toBe(false);
  });
  it("labels provider failures as demo and keeps fallback timestamps historical", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("rate limited")),
    );
    const result = await getQuote("AAPL", "1mo", "auto");
    expect(result.source.demo).toBe(true);
    expect(result.source.message).toMatch(/unavailable/);
    expect(result.data.asOf).toBe(result.source.asOf);
    expect((await getNews("AAPL", "auto")).source.demo).toBe(true);
    expect((await getEarnings("2026-09-30", "auto")).source.demo).toBe(true);
  });
  it("makes no upstream calls in demo mode and does not invent unknown prices", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    expect((await getQuote("AAPL", "1d", "demo")).source.demo).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
    await expect(getQuote("UNKNOWN", "1d", "demo")).rejects.toThrow();
  });
  it("keeps demo history deterministic and aligned with the quote", () => {
    const quote = demoQuote("AAPL", "1y");
    expect(quote).toEqual(demoQuote("AAPL", "1y"));
    expect(quote.points.at(-1)!.close).toBe(quote.price);
    expect(() => demoQuote("UNKNOWN")).toThrow();
    const down = demoQuote("TSLA", "1d");
    expect(down.points.at(-1)!.close).toBeLessThan(down.points[0].close);
  });
});
describe("market API boundary", () => {
  it.each([
    "symbol=../secret",
    "symbol=AAPL&range=forever",
    "kind=anything",
    "date=2026-02-31",
    "mode=paid",
  ])("rejects bad queries: %s", async (query) => {
    const response = await GET(
      new NextRequest(`http://localhost/api/market?${query}`),
    );
    expect(response.status).toBe(400);
  });
  it("returns a labeled quote envelope", async () => {
    const response = await GET(
      new NextRequest("http://localhost/api/market?symbol=aapl&mode=demo"),
    );
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.data.symbol).toBe("AAPL");
    expect(body.source.demo).toBe(true);
  });
  it("returns an honest unavailable response for an unknown demo ticker", async () => {
    const response = await GET(
      new NextRequest("http://localhost/api/market?symbol=UNKNOWN&mode=demo"),
    );
    expect(response.status).toBe(503);
  });
});
