import { XMLParser } from "fast-xml-parser";
import { z } from "zod";
import type { Range } from "@/lib/workspace";
import type { EarningsItem, MarketProvider, NewsItem, Quote } from "./types";

const nullableNumber = z.number().finite().nullable().optional();
const chartSchema = z.object({
  chart: z.object({
    result: z
      .array(
        z.object({
          meta: z.object({
            symbol: z.string(),
            shortName: z.string().optional(),
            longName: z.string().optional(),
            currency: z.string(),
            regularMarketPrice: z.number().finite().positive(),
            regularMarketTime: z.number().positive(),
            chartPreviousClose: nullableNumber,
            previousClose: nullableNumber,
            regularMarketVolume: nullableNumber,
            regularMarketDayHigh: nullableNumber,
            regularMarketDayLow: nullableNumber,
            fiftyTwoWeekHigh: nullableNumber,
            fiftyTwoWeekLow: nullableNumber,
          }),
          timestamp: z.array(z.number()).optional(),
          indicators: z.object({
            quote: z.array(
              z.object({
                close: z.array(z.number().finite().nullable()).optional(),
              }),
            ),
          }),
        }),
      )
      .nullable(),
  }),
});

export async function fetchPublic(url: string): Promise<Response> {
  const response = await fetch(url, {
    signal: AbortSignal.timeout(7000),
    headers: {
      "User-Agent": "Mozilla/5.0 (compatible; FolioPersonalWorkspace/1.0)",
      Accept: "application/json, application/rss+xml, text/xml",
    },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Provider returned ${response.status}`);
  return response;
}
export function parseYahooChart(
  raw: unknown,
  dailyPreviousClose?: number,
): Quote {
  const result = chartSchema.parse(raw).chart.result?.[0];
  if (!result) throw new Error("Symbol unavailable from Yahoo Finance");
  const { meta, timestamp = [], indicators } = result;
  const previousClose =
    dailyPreviousClose ?? meta.previousClose ?? meta.chartPreviousClose;
  if (!previousClose || previousClose <= 0)
    throw new Error("Previous close unavailable");
  const closes = indicators.quote[0]?.close ?? [];
  const points = timestamp.flatMap((time, i) =>
    typeof closes[i] === "number" ? [{ time, close: closes[i] as number }] : [],
  );
  return {
    symbol: meta.symbol,
    name: meta.longName ?? meta.shortName ?? meta.symbol,
    currency: meta.currency,
    price: meta.regularMarketPrice,
    change: meta.regularMarketPrice - previousClose,
    changePercent: (meta.regularMarketPrice / previousClose - 1) * 100,
    previousClose,
    volume: meta.regularMarketVolume ?? null,
    high: meta.regularMarketDayHigh ?? null,
    low: meta.regularMarketDayLow ?? null,
    yearHigh: meta.fiftyTwoWeekHigh ?? null,
    yearLow: meta.fiftyTwoWeekLow ?? null,
    points,
    asOf: new Date(meta.regularMarketTime * 1000).toISOString(),
  };
}
export const yahooProvider: MarketProvider = {
  id: "Yahoo Finance",
  async quote(symbol, range: Range) {
    const endpoint = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}`;
    const interval = range === "1d" ? "5m" : range === "5y" ? "1wk" : "1d";
    // The range's chartPreviousClose is NOT necessarily yesterday's close.
    // Fetch the daily metadata separately so daily changes stay correct at every range.
    const daily = await (
      await fetchPublic(`${endpoint}?range=1d&interval=5m`)
    ).json();
    const quote = parseYahooChart(daily);
    if (range === "1d") return quote;
    const history = await (
      await fetchPublic(`${endpoint}?range=${range}&interval=${interval}`)
    ).json();
    return parseYahooChart(history, quote.previousClose);
  },
};
export function safeUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export async function yahooNews(symbol: string): Promise<NewsItem[]> {
  const response = await fetchPublic(
    `https://finance.yahoo.com/rss/headline?s=${encodeURIComponent(symbol)}`,
  );
  const xml = await response.text();
  return parseNewsFeed(xml, "Yahoo Finance RSS");
}
export function parseNewsFeed(
  xml: string,
  publisher: string,
  limit = 20,
): NewsItem[] {
  if (xml.length > 1_000_000 || /<!DOCTYPE|<!ENTITY/i.test(xml))
    throw new Error("Invalid news feed");
  // DTD/entity declarations are rejected above; decode only standard XML entities.
  const parsed = new XMLParser({ processEntities: true }).parse(xml);
  const raw: unknown = parsed?.rss?.channel?.item;
  if (!raw) throw new Error("News unavailable");
  const items = Array.isArray(raw) ? raw : [raw];
  return items.slice(0, limit).flatMap((item: unknown) => {
    const result = z
      .object({ title: z.string(), link: z.string(), pubDate: z.string() })
      .safeParse(item);
    if (!result.success) return [];
    const url = safeUrl(result.data.link);
    const date = new Date(result.data.pubDate);
    return url && !isNaN(date.getTime())
      ? [
          {
            title: result.data.title,
            url,
            publisher,
            publishedAt: date.toISOString(),
          },
        ]
      : [];
  });
}
const nasdaqSchema = z.object({
  data: z
    .object({
      rows: z
        .array(
          z.object({
            symbol: z.string(),
            name: z.string(),
            time: z.string().nullable().optional(),
            epsForecast: z.string().nullable().optional(),
          }),
        )
        .nullable(),
    })
    .nullable(),
});
export async function nasdaqEarnings(date: string): Promise<EarningsItem[]> {
  const raw = await (
    await fetchPublic(
      `https://api.nasdaq.com/api/calendar/earnings?date=${date}`,
    )
  ).json();
  const parsed = nasdaqSchema.parse(raw);
  if (!parsed.data) throw new Error("Earnings calendar unavailable");
  return (parsed.data.rows ?? []).slice(0, 100).map((row) => ({
    symbol: row.symbol,
    name: row.name,
    date,
    time:
      row.time === "time-pre-market"
        ? "Before open"
        : row.time === "time-after-hours"
          ? "After close"
          : "Time TBD",
    estimate: row.epsForecast ?? "—",
  }));
}
