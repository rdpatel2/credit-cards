import { z } from "zod";

export const filingStatuses = {
  single: "Single",
  joint: "Married filing jointly / qualifying surviving spouse",
  separate: "Married filing separately",
  head: "Head of household",
} as const;
export const taxProfileSchema = z.object({
  year: z.literal(2026),
  filingStatus: z.enum(["single", "joint", "separate", "head"]),
  ordinaryIncome: z.number().finite().min(0).max(1e12),
  deduction: z.number().finite().min(0).max(1e12),
  stateDeduction: z.number().finite().min(0).max(1e12),
  preferentialIncome: z.number().finite().min(0).max(1e12),
  magi: z.number().finite().min(0).max(1e12),
  investmentIncome: z.number().finite().min(0).max(1e12),
  state: z.literal("NC"),
});
export type TaxProfile = z.infer<typeof taxProfileSchema>;
export const standardDeductions = {
  single: 16100,
  joint: 32200,
  separate: 16100,
  head: 24150,
};
export const ncDeductions = {
  single: 12750,
  joint: 25500,
  separate: 12750,
  head: 19125,
};
// IRS Revenue Procedure 2025-32, §4.01 and §4.03 (tax year 2026).
// https://www.irs.gov/irb/2025-45_IRB
const ordinary: Record<TaxProfile["filingStatus"], number[]> = {
  single: [12400, 50400, 105700, 201775, 256225, 640600],
  joint: [24800, 100800, 211400, 403550, 512450, 768700],
  separate: [12400, 50400, 105700, 201775, 256225, 384350],
  head: [17700, 67450, 105700, 201750, 256200, 640600],
};
const capital = {
  single: [49450, 545500],
  joint: [98900, 613700],
  separate: [49450, 306850],
  head: [66200, 579600],
};
function progressive(income: number, thresholds: number[], rates: number[]) {
  let tax = 0,
    start = 0;
  for (const [i, end] of [...thresholds, Infinity].entries()) {
    tax += Math.max(0, Math.min(income, end) - start) * rates[i];
    start = end;
  }
  return tax;
}
export function estimateTax(
  profile: TaxProfile,
  shortGain: number,
  longGain: number,
) {
  const p = taxProfileSchema.parse(profile);
  if (!Number.isFinite(shortGain) || !Number.isFinite(longGain))
    throw new Error("Invalid gain");
  // Net opposite-sign categories within this scenario. Do not promise loss deductions/refunds.
  if ((shortGain < 0 && longGain > 0) || (shortGain > 0 && longGain < 0)) {
    const net = shortGain + longGain;
    if (shortGain > 0) {
      shortGain = Math.max(0, net);
      longGain = Math.min(0, net);
    } else {
      longGain = Math.max(0, net);
      shortGain = Math.min(0, net);
    }
  }
  const st = Math.max(0, shortGain),
    lt = Math.max(0, longGain);
  const ordinaryTax = (income: number) =>
    progressive(
      income,
      ordinary[p.filingStatus],
      [0.1, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37],
    );
  const prefTax = (base: number, gain: number) =>
    progressive(base + gain, capital[p.filingStatus], [0, 0.15, 0.2]) -
    progressive(base, capital[p.filingStatus], [0, 0.15, 0.2]);
  const regularAt = (short: number, long: number) => {
    const income = Math.max(0, p.ordinaryIncome + short - p.deduction);
    const preferential = Math.max(
      0,
      p.preferentialIncome +
        long -
        Math.max(0, p.deduction - p.ordinaryIncome - short),
    );
    // Qualified Dividends and Capital Gain Tax Worksheet: use regular tax
    // on all taxable income if it is lower than the preferential calculation.
    return Math.min(
      ordinaryTax(income) + prefTax(income, preferential),
      ordinaryTax(income + preferential),
    );
  };
  const regular = regularAt(st, lt) - regularAt(0, 0);
  const threshold =
    p.filingStatus === "joint"
      ? 250000
      : p.filingStatus === "separate"
        ? 125000
        : 200000;
  const niitAt = (gain: number) =>
    0.038 *
    Math.min(p.investmentIncome + gain, Math.max(0, p.magi + gain - threshold));
  const niit = Math.max(0, niitAt(st + lt) - niitAt(0));
  const stateAt = (gain: number) =>
    Math.max(
      0,
      p.ordinaryIncome + p.preferentialIncome + gain - p.stateDeduction,
    ) * 0.0399;
  const state = stateAt(st + lt) - stateAt(0);
  return {
    regular,
    niit,
    federal: regular + niit,
    state,
    total: regular + niit + state,
    effectiveRate: st + lt ? ((regular + niit + state) / (st + lt)) * 100 : 0,
  };
}
