import { z } from "zod";
import { metricIds, type Fundamentals } from "./research";

const headers = {
  "User-Agent": "Mozilla/5.0 (compatible; FolioPersonalWorkspace/1.0)",
  Accept: "application/json",
};
type Session = { cookie: string; crumb: string; expires: number };
let session: Promise<Session> | undefined;
export function clearFundamentalsSession() {
  session = undefined;
}
async function publicSession(): Promise<Session> {
  if (session) {
    const existing = await session;
    if (existing.expires > Date.now()) return existing;
  }
  session = (async () => {
    // Yahoo's public session/crumb flow, not a user account or paid API key.
    // The cookie endpoint can return 404 while successfully setting a session cookie.
    const response = await fetch("https://fc.yahoo.com", {
      headers,
      signal: AbortSignal.timeout(7000),
      cache: "no-store",
    });
    const cookie = response.headers
      .getSetCookie()
      .map((value) => value.split(";")[0])
      .join("; ");
    if (!cookie) throw new Error("Public data session unavailable");
    const crumbResponse = await fetch(
      "https://query1.finance.yahoo.com/v1/test/getcrumb",
      {
        headers: { ...headers, Accept: "*/*", Cookie: cookie },
        signal: AbortSignal.timeout(7000),
        cache: "no-store",
      },
    );
    const crumb = await crumbResponse.text();
    if (
      !crumbResponse.ok ||
      !crumb.trim() ||
      crumb.length > 128 ||
      /[<>\r\n]/.test(crumb)
    )
      throw new Error("Public data session unavailable");
    return { cookie, crumb, expires: Date.now() + 3600000 };
  })().catch((error) => {
    session = undefined;
    throw error;
  });
  return session;
}
const modulesSchema = z.object({
  quoteSummary: z.object({
    result: z
      .array(
        z.object({
          price: z.record(z.string(), z.unknown()).optional(),
          summaryDetail: z.record(z.string(), z.unknown()).optional(),
          defaultKeyStatistics: z.record(z.string(), z.unknown()).optional(),
          financialData: z.record(z.string(), z.unknown()).optional(),
        }),
      )
      .nullable(),
  }),
});
function number(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (value && typeof value === "object" && "raw" in value)
    return number(value.raw);
  return null;
}
function text(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (
    value &&
    typeof value === "object" &&
    "raw" in value &&
    typeof value.raw === "string"
  )
    return value.raw;
  return null;
}
export function parseFundamentals(raw: unknown, symbol: string): Fundamentals {
  const modules = modulesSchema.parse(raw).quoteSummary.result?.[0];
  if (!modules) throw new Error("Fundamentals unavailable");
  const merged = {
    ...modules.price,
    ...modules.summaryDetail,
    ...modules.defaultKeyStatistics,
    ...modules.financialData,
  };
  const fields = {
    ...merged,
    priceToSales: merged.priceToSalesTrailing12Months,
    pegRatio: merged.pegRatio ?? merged.trailingPegRatio,
  };
  const values = Object.fromEntries(
    metricIds.map((id) => [id, number(fields[id as keyof typeof fields])]),
  ) as Fundamentals["values"];
  // Yahoo expresses debt/equity as percentage points, unlike its other ratio fields.
  if (values.debtToEquity !== null) values.debtToEquity /= 100;
  for (const id of ["trailingPE", "forwardPE"] as const)
    if (values[id] !== null && values[id]! <= 0) values[id] = null;
  if (values.trailingEps !== null && values.trailingEps <= 0)
    values.trailingPE = null;
  if (values.forwardEps !== null && values.forwardEps <= 0)
    values.forwardPE = null;
  if (Object.values(values).every((value) => value === null))
    throw new Error("Fundamentals unavailable");
  const currency = text(merged.currency) ?? "USD";
  return {
    symbol,
    currency,
    financialCurrency: text(merged.financialCurrency) ?? currency,
    values,
    asOf: new Date().toISOString(),
  };
}
export async function yahooFundamentals(symbol: string): Promise<Fundamentals> {
  return parseFundamentals(
    await yahooSummary(
      symbol,
      "price,summaryDetail,defaultKeyStatistics,financialData",
    ),
    symbol,
  );
}
export async function yahooSummary(
  symbol: string,
  modules: string,
): Promise<unknown> {
  const auth = await publicSession();
  const query = new URLSearchParams({
    modules,
    crumb: auth.crumb,
  });
  const response = await fetch(
    `https://query2.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(symbol)}?${query}`,
    {
      headers: { ...headers, Cookie: auth.cookie },
      signal: AbortSignal.timeout(7000),
      cache: "no-store",
    },
  );
  if (!response.ok) {
    if (response.status === 401 || response.status === 403)
      clearFundamentalsSession();
    throw new Error("Fundamentals provider unavailable");
  }
  return response.json();
}
