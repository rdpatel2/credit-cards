import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { ranges, symbolSchema } from "@/lib/workspace";
import {
  getEarnings,
  getNews,
  getQuote,
  getFundamentals,
  getMarketNews,
  getAnalyst,
} from "@/lib/market/service";

const querySchema = z.object({
  kind: z
    .enum([
      "quote",
      "news",
      "earnings",
      "fundamentals",
      "market-news",
      "analyst",
    ])
    .default("quote"),
  symbol: symbolSchema.default("AAPL"),
  range: z.enum(ranges).default("1d"),
  mode: z.enum(["auto", "demo"]).default("auto"),
  date: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .refine((s) => {
      const d = new Date(s);
      return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
    })
    .optional(),
});
export async function GET(request: NextRequest) {
  const parsed = querySchema.safeParse(
    Object.fromEntries(request.nextUrl.searchParams),
  );
  if (!parsed.success)
    return NextResponse.json(
      { error: "Invalid market data request" },
      { status: 400 },
    );
  const { kind, symbol, range, mode, date } = parsed.data;
  try {
    const result =
      kind === "analyst"
        ? await getAnalyst(symbol, mode)
        : kind === "fundamentals"
          ? await getFundamentals(symbol, mode)
          : kind === "market-news"
            ? await getMarketNews(mode)
            : kind === "news"
              ? await getNews(symbol, mode)
              : kind === "earnings"
                ? await getEarnings(
                    date ?? new Date().toISOString().slice(0, 10),
                    mode,
                  )
                : await getQuote(symbol, range, mode);
    return NextResponse.json(result, {
      headers: { "Cache-Control": "private, max-age=60" },
    });
  } catch {
    return NextResponse.json(
      {
        error: `Data is unavailable for ${symbol}. Check the symbol or try again later.`,
      },
      { status: 503 },
    );
  }
}
