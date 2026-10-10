import { describe, it, expect } from "vitest";
import {
  calculatePosition,
  isLongTerm,
  simulateSale,
  type Trade,
} from "@/lib/portfolio";
import {
  estimateTax,
  standardDeductions,
  ncDeductions,
  type TaxProfile,
} from "@/lib/tax";
import { initialWorkspace, parseWorkspace } from "@/lib/workspace";

const buy = (
  id: string,
  quantity: number,
  price: number,
  date = "2024-01-02",
  fees = 0,
): Trade => ({ id, side: "buy", date, quantity, price, fees });
const profile: TaxProfile = {
  year: 2026,
  filingStatus: "single",
  ordinaryIncome: 100000,
  preferentialIncome: 0,
  magi: 100000,
  investmentIncome: 0,
  deduction: 16100,
  stateDeduction: 12750,
  state: "NC",
};
describe("trade ledger", () => {
  it("includes fees and removes FIFO basis on a partial sale", () => {
    const trades = [
      buy("a", 10, 100, "2024-01-02", 10),
      buy("b", 10, 200, "2025-01-01"),
      { ...buy("c", 15, 300, "2026-01-03", 15), side: "sell" as const },
    ];
    const p = calculatePosition(trades);
    expect(p.quantity).toBe(5);
    expect(p.basis).toBe(1000);
    expect(p.averageCost).toBe(200);
    expect(p.purchaseAverage).toBe(150.5);
    expect(p.realizedGain).toBe(2475);
    expect(p.disposals.map((d) => d.basis)).toEqual([1010, 1000]);
  });
  it("uses decimal arithmetic for fractional quantities and clears fully sold positions", () => {
    const p = calculatePosition([
      buy("a", 0.1, 10),
      buy("b", 0.2, 20),
      { ...buy("c", 0.3, 30, "2026-01-01"), side: "sell" },
    ]);
    expect(p.quantity).toBe(0);
    expect(p.basis).toBe(0);
    expect(p.averageCost).toBeNull();
    expect(p.realizedGain).toBe(4);
  });
  it("rejects overselling, invalid dates, duplicate IDs, nonfinite values, and negative inputs", () => {
    expect(() =>
      calculatePosition([{ ...buy("a", 1, 10), side: "sell" }]),
    ).toThrow(/exceeds/);
    expect(() => calculatePosition([buy("a", 1, 10), buy("a", 1, 10)])).toThrow(
      /Duplicate/,
    );
    for (const t of [
      buy("a", -1, 1),
      buy("a", 1, NaN),
      buy("a", 1, 1, "2025-02-29"),
      { ...buy("a", 1, 1), fees: -1 },
    ])
      expect(() => calculatePosition([t])).toThrow();
  });
  it("uses chronological FIFO and stable entry order for same-day trades", () => {
    const a = buy("a", 1, 10),
      b = buy("b", 1, 20, "2025-01-02");
    expect(
      calculatePosition([b, a, { ...buy("c", 1, 30, b.date), side: "sell" }])
        .basis,
    ).toBe(20);
    expect(() =>
      calculatePosition([
        { ...a, side: "sell" },
        { ...a, id: "b" },
      ]),
    ).toThrow();
  });
  it("classifies the exact calendar anniversary and leap-day boundary", () => {
    expect(isLongTerm("2024-01-01", "2025-01-01")).toBe(false);
    expect(isLongTerm("2024-01-01", "2025-01-02")).toBe(true);
    expect(isLongTerm("2024-02-29", "2025-02-28")).toBe(false);
    expect(isLongTerm("2024-02-29", "2025-03-01")).toBe(true);
  });
  it("simulates mixed-term lots without mutating the ledger", () => {
    const trades = [buy("a", 1, 10), buy("b", 1, 20, "2026-01-02")];
    const sale = simulateSale(trades, {
      quantity: 2,
      price: 30,
      date: "2026-02-01",
      fees: 2,
    });
    expect(sale.shortGain).toBe(9);
    expect(sale.longGain).toBe(19);
    expect(trades).toHaveLength(2);
    expect(() =>
      simulateSale(trades, {
        quantity: 1,
        price: 30,
        date: "2025-01-01",
        fees: 0,
      }),
    ).toThrow(/follow/);
  });
  it("migrates empty ledgers and rejects broken histories during import", () => {
    const legacy = {
      ...initialWorkspace(),
      trades: undefined,
      taxProfile: undefined,
    };
    const parsed = parseWorkspace(JSON.stringify(legacy));
    expect(parsed.trades).toEqual({});
    expect(parsed.taxProfile).toBeNull();
    const state = initialWorkspace();
    state.trades.AAPL = [buy("a", 1, 100)];
    state.taxProfile = profile;
    expect(parseWorkspace(JSON.stringify(state))).toEqual(state);
    state.trades.AAPL.push({ ...buy("b", 2, 200, "2026-01-01"), side: "sell" });
    expect(() => parseWorkspace(JSON.stringify(state))).toThrow(/Invalid/);
  });
  it("adds analyst widgets once while preserving legacy layouts and deliberate removals", () => {
    const state = initialWorkspace();
    delete state.portfolioVersion;
    state.stockTemplate.widgets = state.stockTemplate.widgets.filter(
      (w) => w.type !== "analyst",
    );
    state.stockTemplate.layout = state.stockTemplate.layout.filter(
      (l) => l.i !== "stock-analyst",
    );
    const migrated = parseWorkspace(JSON.stringify(state));
    expect(
      migrated.stockTemplate.layout.slice(0, state.stockTemplate.layout.length),
    ).toEqual(state.stockTemplate.layout);
    expect(
      migrated.stockTemplate.widgets.filter((w) => w.type === "analyst"),
    ).toHaveLength(1);
    const analystId = migrated.stockTemplate.widgets.find(
      (w) => w.type === "analyst",
    )!.id;
    migrated.stockTemplate.widgets = migrated.stockTemplate.widgets.filter(
      (w) => w.id !== analystId,
    );
    migrated.stockTemplate.layout = migrated.stockTemplate.layout.filter(
      (l) => l.i !== analystId,
    );
    expect(
      parseWorkspace(JSON.stringify(migrated)).stockTemplate.widgets.some(
        (w) => w.type === "analyst",
      ),
    ).toBe(false);
  });
});
describe("2026 federal and North Carolina incremental tax", () => {
  it("taxes short gains at ordinary rates and long gains at preferential rates", () => {
    expect(estimateTax(profile, 1000, 0).regular).toBeCloseTo(220);
    const tax = estimateTax(profile, 0, 1000);
    expect(tax.regular).toBeCloseTo(150);
    expect(tax.state).toBeCloseTo(39.9);
    expect(tax.total).toBeCloseTo(189.9);
  });
  it("crosses ordinary brackets progressively", () => {
    expect(
      estimateTax({ ...profile, ordinaryIncome: 16100 + 50400 - 500 }, 1000, 0)
        .regular,
    ).toBeCloseTo(170);
  });
  it("stacks long gains and existing qualified income across thresholds", () => {
    expect(
      estimateTax({ ...profile, ordinaryIncome: 16100 + 49000 }, 0, 1000)
        .regular,
    ).toBeCloseTo(82.5);
    expect(
      estimateTax(
        { ...profile, ordinaryIncome: 16100 + 49000, preferentialIncome: 1000 },
        1000,
        0,
      ).regular,
    ).toBeCloseTo(187.5); // $120 ordinary + $67.50 displaced gains
  });
  it("applies NIIT only to the incremental lesser-of calculation", () => {
    expect(
      estimateTax({ ...profile, magi: 199500, investmentIncome: 0 }, 0, 1000)
        .niit,
    ).toBeCloseTo(19);
    expect(
      estimateTax({ ...profile, magi: 300000, investmentIncome: 0 }, 0, 1000)
        .niit,
    ).toBeCloseTo(38);
    expect(
      estimateTax({ ...profile, magi: 199500, investmentIncome: 2000 }, 0, 1000)
        .niit,
    ).toBeCloseTo(19);
  });
  it("accounts for unused deductions and does not promise a loss refund", () => {
    expect(
      estimateTax({ ...profile, ordinaryIncome: 0, magi: 0 }, 1000, 0).total,
    ).toBe(0);
    expect(estimateTax(profile, -1000, 0).total).toBe(0);
    expect(estimateTax(profile, -500, 1500).regular).toBeCloseTo(150);
    expect(estimateTax(profile, 1500, -500).regular).toBeCloseTo(220);
  });
  it("caps preferential tax at the ordinary tax on all income when lower", () => {
    expect(
      estimateTax({ ...profile, ordinaryIncome: 16100 + 49450 }, 0, 1000)
        .regular,
    ).toBeCloseTo(125);
  });
  it("supports each filing status and rejects unsupported tax years", () => {
    for (const filingStatus of [
      "single",
      "joint",
      "separate",
      "head",
    ] as const) {
      const p = {
        ...profile,
        filingStatus,
        ordinaryIncome: standardDeductions[filingStatus],
        stateDeduction: ncDeductions[filingStatus],
        deduction: standardDeductions[filingStatus],
      };
      expect(estimateTax(p, 100, 0).regular).toBeCloseTo(10);
    }
    expect(() =>
      estimateTax({ ...profile, year: 2027 } as unknown as TaxProfile, 0, 1000),
    ).toThrow();
  });
});
