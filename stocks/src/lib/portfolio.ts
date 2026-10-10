import Decimal from "decimal.js";
import { z } from "zod";

export const tradeDateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/)
  .refine((s) => {
    const d = new Date(s);
    return Number.isFinite(d.getTime()) && d.toISOString().slice(0, 10) === s;
  }, "Enter a valid date");
export const tradeSchema = z
  .object({
    id: z.string().min(1).max(80),
    side: z.enum(["buy", "sell"]),
    date: tradeDateSchema,
    quantity: z.number().finite().positive().max(1e9),
    price: z.number().finite().nonnegative().max(1e9),
    fees: z.number().finite().nonnegative().max(1e9),
  })
  .refine(
    (t) => t.side === "buy" || t.fees <= t.quantity * t.price,
    "Sale fees cannot exceed proceeds",
  );
export type Trade = z.infer<typeof tradeSchema>;
export type Lot = { id: string; date: string; quantity: number; basis: number };
export type Disposal = {
  tradeId: string;
  lotId: string;
  acquired: string;
  sold: string;
  quantity: number;
  basis: number;
  proceeds: number;
  gain: number;
  longTerm: boolean;
};

// Calendar anniversary, not 365 days: leap years must not change the holding period.
export function isLongTerm(acquired: string, sold: string) {
  const date = new Date(`${acquired}T00:00:00Z`);
  const month = date.getUTCMonth();
  date.setUTCFullYear(date.getUTCFullYear() + 1);
  if (date.getUTCMonth() !== month) date.setUTCDate(0);
  return sold > date.toISOString().slice(0, 10);
}

/** One USD taxable account. Stable same-day order follows ledger entry order. */
export function calculatePosition(input: Trade[]) {
  const trades = z.array(tradeSchema).max(5000).parse(input);
  if (new Set(trades.map((t) => t.id)).size !== trades.length)
    throw new Error("Duplicate trade IDs");
  const lots: {
    id: string;
    date: string;
    quantity: Decimal;
    basis: Decimal;
  }[] = [];
  const disposals: Disposal[] = [];
  let buyCost = new Decimal(0),
    buyShares = new Decimal(0);
  for (const trade of [...trades].sort((a, b) =>
    a.date.localeCompare(b.date),
  )) {
    const qty = new Decimal(trade.quantity);
    if (trade.side === "buy") {
      const basis = qty.mul(trade.price).add(trade.fees);
      lots.push({ id: trade.id, date: trade.date, quantity: qty, basis });
      buyCost = buyCost.add(basis);
      buyShares = buyShares.add(qty);
      continue;
    }
    let remaining = qty;
    for (const lot of lots) {
      if (remaining.isZero()) break;
      if (lot.quantity.isZero()) continue;
      const used = Decimal.min(remaining, lot.quantity);
      const basis = lot.basis.mul(used).div(lot.quantity);
      const proceeds = used
        .mul(trade.price)
        .sub(new Decimal(trade.fees).mul(used).div(qty));
      disposals.push({
        tradeId: trade.id,
        lotId: lot.id,
        acquired: lot.date,
        sold: trade.date,
        quantity: used.toNumber(),
        basis: basis.toNumber(),
        proceeds: proceeds.toNumber(),
        gain: proceeds.sub(basis).toNumber(),
        longTerm: isLongTerm(lot.date, trade.date),
      });
      lot.quantity = lot.quantity.sub(used);
      lot.basis = lot.basis.sub(basis);
      remaining = remaining.sub(used);
    }
    if (remaining.gt(0))
      throw new Error(
        `Sale on ${trade.date} exceeds shares held on that date.`,
      );
  }
  const quantity = Decimal.sum(0, ...lots.map((l) => l.quantity));
  const basis = Decimal.sum(0, ...lots.map((l) => l.basis));
  return {
    quantity: quantity.toNumber(),
    basis: basis.toNumber(),
    averageCost: quantity.gt(0) ? basis.div(quantity).toNumber() : null,
    purchaseAverage: buyShares.gt(0) ? buyCost.div(buyShares).toNumber() : null,
    realizedGain: Decimal.sum(0, ...disposals.map((d) => d.gain)).toNumber(),
    lots: lots
      .filter((l) => l.quantity.gt(0))
      .map((l) => ({
        ...l,
        quantity: l.quantity.toNumber(),
        basis: l.basis.toNumber(),
      })),
    disposals,
  };
}

export function simulateSale(
  trades: Trade[],
  sale: Omit<Trade, "id" | "side">,
) {
  if (trades.some((t) => t.date > sale.date))
    throw new Error("Sale scenario must follow all recorded trades.");
  let sequence = 0;
  let id = `sale-scenario-${sequence}`;
  while (trades.some((t) => t.id === id)) id = `sale-scenario-${++sequence}`;
  const result = calculatePosition([...trades, { ...sale, id, side: "sell" }]);
  const lots = result.disposals.filter((d) => d.tradeId === id);
  const sum = (items: Disposal[]) =>
    Decimal.sum(0, ...items.map((l) => l.gain)).toNumber();
  return {
    lots,
    shortGain: sum(lots.filter((l) => !l.longTerm)),
    longGain: sum(lots.filter((l) => l.longTerm)),
    gain: sum(lots),
    hasLoss: lots.some((l) => l.gain < 0),
  };
}
