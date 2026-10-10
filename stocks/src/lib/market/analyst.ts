import { z } from "zod";

export const analystLabels = [
  "Very bearish",
  "Bearish",
  "Neutral",
  "Bullish",
  "Very bullish",
] as const;
export type AnalystPeriod = {
  period: string;
  counts: [number, number, number, number, number];
};
export type AnalystData = { symbol: string; periods: AnalystPeriod[] };
const count = z.number().int().min(0).max(10000);
const row = z.object({
  period: z.enum(["0m", "-1m", "-2m", "-3m"]),
  strongSell: count,
  sell: count,
  hold: count,
  buy: count,
  strongBuy: count,
});
export function parseAnalyst(raw: unknown, symbol: string): AnalystData {
  const parsed = z
    .object({
      quoteSummary: z.object({
        result: z
          .array(
            z.object({
              recommendationTrend: z
                .object({ trend: z.array(z.unknown()) })
                .optional(),
            }),
          )
          .nullable(),
      }),
    })
    .parse(raw);
  const periods: AnalystPeriod[] = [];
  for (const value of parsed.quoteSummary.result?.[0]?.recommendationTrend
    ?.trend ?? []) {
    const result = row.safeParse(value);
    if (!result.success || periods.some((p) => p.period === result.data.period))
      continue;
    const r = result.data;
    periods.push({
      period: r.period,
      counts: [r.strongSell, r.sell, r.hold, r.buy, r.strongBuy],
    });
  }
  return { symbol, periods };
}
export function analystSummary(periods: AnalystPeriod[]) {
  const counts = [0, 0, 0, 0, 0];
  for (const p of periods) p.counts.forEach((n, i) => (counts[i] += n));
  const total = counts.reduce((a, b) => a + b, 0);
  const score = total
    ? counts.reduce((sum, n, i) => sum + n * (i + 1), 0) / total
    : null;
  return {
    counts,
    total,
    score,
    label:
      score === null
        ? "No coverage"
        : analystLabels[Math.min(4, Math.max(0, Math.round(score) - 1))],
  };
}
