import { afterEach, describe, expect, it, vi } from "vitest";
import { analystSummary, parseAnalyst } from "@/lib/market/analyst";
import { clearCache, getAnalyst } from "@/lib/market/service";
import { clearFundamentalsSession } from "@/lib/market/fundamentals";
import { GET } from "@/app/api/market/route";
import { NextRequest } from "next/server";
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  clearCache();
  clearFundamentalsSession();
});
const raw = (trend: unknown[]) => ({
  quoteSummary: { result: [{ recommendationTrend: { trend } }] },
});
describe("analyst recommendations", () => {
  it("maps sell-to-buy counts correctly and filters malformed and duplicate periods", () => {
    const row = {
      period: "0m",
      strongSell: 1,
      sell: 2,
      hold: 3,
      buy: 4,
      strongBuy: 5,
    };
    expect(
      parseAnalyst(raw([row, row, { ...row, period: "-1m", buy: -1 }]), "AAPL")
        .periods,
    ).toEqual([{ period: "0m", counts: [1, 2, 3, 4, 5] }]);
    expect(
      parseAnalyst({ quoteSummary: { result: [{}] } }, "SPY").periods,
    ).toEqual([]);
  });
  it("uses recommendation-weighted consensus and all five labels", () => {
    expect(analystSummary([]).label).toBe("No coverage");
    ["Very bearish", "Bearish", "Neutral", "Bullish", "Very bullish"].forEach(
      (label, i) => {
        const counts: [number, number, number, number, number] = [
          0, 0, 0, 0, 0,
        ];
        counts[i] = 3;
        expect(analystSummary([{ period: "0m", counts }]).label).toBe(label);
      },
    );
    expect(
      analystSummary([{ period: "0m", counts: [1, 0, 0, 0, 9] }]).score,
    ).toBe(4.6);
  });
  it("labels demo fixtures and keeps unknown symbols unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn());
    expect((await getAnalyst("AAPL", "demo")).source.demo).toBe(true);
    expect((await getAnalyst("SPY", "demo")).data.periods).toEqual([]);
    await expect(getAnalyst("NOTREAL", "demo")).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("requests live recommendationTrend through the shared public session", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response("", {
            headers: { "set-cookie": "session=test; Path=/" },
          }),
        )
        .mockResolvedValueOnce(new Response("crumb"))
        .mockResolvedValueOnce(
          Response.json(
            raw([
              {
                period: "0m",
                strongSell: 0,
                sell: 1,
                hold: 2,
                buy: 3,
                strongBuy: 4,
              },
            ]),
          ),
        ),
    );
    const result = await getAnalyst("AAPL", "auto");
    expect(result.source.demo).toBe(false);
    expect(result.data.periods[0].counts).toEqual([0, 1, 2, 3, 4]);
    expect(String(vi.mocked(fetch).mock.calls[2][0])).toContain(
      "modules=recommendationTrend",
    );
  });
  it("serves the analyst API and labels provider failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const response = await GET(
      new NextRequest("http://localhost/api/market?kind=analyst&symbol=AAPL"),
    );
    expect(response.status).toBe(200);
    expect((await response.json()).source.demo).toBe(true);
    const invalid = await GET(
      new NextRequest(
        "http://localhost/api/market?kind=analyst&symbol=%3Cbad%3E",
      ),
    );
    expect(invalid.status).toBe(400);
  });
});
