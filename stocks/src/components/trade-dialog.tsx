"use client";
import { useState } from "react";
import { Modal } from "./modal";
import {
  calculatePosition,
  simulateSale,
  tradeSchema,
  type Trade,
} from "@/lib/portfolio";
import {
  estimateTax,
  filingStatuses,
  ncDeductions,
  standardDeductions,
  taxProfileSchema,
  type TaxProfile,
} from "@/lib/tax";
import type { Workspace } from "@/lib/workspace";
import type { DataEnvelope, Quote } from "@/lib/market/types";

const usd = (value: number | null) =>
  value === null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(value);
const today = () => new Date().toLocaleDateString("en-CA");
function NumberField({
  label,
  name,
  value,
  required = true,
  positive = false,
}: {
  label: string;
  name: string;
  value?: number | string;
  required?: boolean;
  positive?: boolean;
}) {
  return (
    <label>
      {label}
      <input
        name={name}
        type="number"
        min={positive ? "0.00000001" : "0"}
        max="1000000000000"
        step="any"
        defaultValue={value}
        required={required}
      />
    </label>
  );
}
function TaxSettings({
  profile,
  onSave,
}: {
  profile: TaxProfile | null;
  onSave: (profile: TaxProfile) => void;
}) {
  const [filingStatus, setFilingStatus] = useState<TaxProfile["filingStatus"]>(
    profile?.filingStatus ?? "single",
  );
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  return (
    <details className="tax-settings" open={!profile || undefined}>
      <summary>
        {profile ? "Edit tax assumptions" : "Set up tax estimate"} · 2026 ·
        North Carolina
      </summary>
      <p className="muted">
        Enter annual income before this hypothetical sale. Include recorded
        realized gains and other accounts in these inputs; the ledger is not
        automatically added. All amounts are USD. Income stays in this browser
        and your workspace export.
      </p>
      <form
        className="form-stack"
        onChange={() => setSaved(false)}
        onSubmit={(e) => {
          e.preventDefault();
          const form = new FormData(e.currentTarget);
          const ordinaryIncome = Number(form.get("ordinaryIncome")),
            preferentialIncome = Number(form.get("preferentialIncome"));
          const result = taxProfileSchema.safeParse({
            year: 2026,
            state: "NC",
            filingStatus,
            ordinaryIncome,
            preferentialIncome,
            deduction: Number(form.get("deduction")),
            stateDeduction: Number(form.get("stateDeduction")),
            magi:
              form.get("magi") === ""
                ? ordinaryIncome + preferentialIncome
                : Number(form.get("magi")),
            investmentIncome: Number(form.get("investmentIncome")),
          });
          if (!result.success) {
            setError(
              "Check all tax inputs; use finite, nonnegative dollar amounts.",
            );
            return;
          }
          onSave(result.data);
          setError("");
          setSaved(true);
        }}
      >
        <div className="portfolio-fields">
          <label>
            Filing status
            <select
              aria-label="Filing status"
              value={filingStatus}
              onChange={(e) =>
                setFilingStatus(e.target.value as TaxProfile["filingStatus"])
              }
            >
              {Object.entries(filingStatuses).map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Tax year
            <select aria-label="Tax year" defaultValue="2026">
              <option>2026</option>
            </select>
          </label>
        </div>
        <div className="portfolio-fields">
          <NumberField
            label="Ordinary income before deductions"
            name="ordinaryIncome"
            value={profile?.ordinaryIncome}
          />
          <NumberField
            label="Net long-term gains + qualified dividends"
            name="preferentialIncome"
            value={profile?.preferentialIncome ?? 0}
          />
          <NumberField
            key={`f-${filingStatus}`}
            label="Federal deduction"
            name="deduction"
            value={
              filingStatus === profile?.filingStatus
                ? profile.deduction
                : standardDeductions[filingStatus]
            }
          />
          <NumberField
            key={`s-${filingStatus}`}
            label="NC deduction"
            name="stateDeduction"
            value={
              filingStatus === profile?.filingStatus
                ? profile.stateDeduction
                : ncDeductions[filingStatus]
            }
          />
          <NumberField
            label="MAGI for NIIT (blank = total income above)"
            name="magi"
            value={profile?.magi}
            required={false}
          />
          <NumberField
            label="Net investment income before this sale"
            name="investmentIncome"
            value={profile?.investmentIncome}
          />
        </div>
        <p className="muted">
          Ordinary income includes wages, interest, and net short-term gains
          after income adjustments, but before deductions. Net investment income
          includes taxable investment income across all accounts (for NIIT), not
          wages. Deduction defaults assume standard-deduction eligibility;
          adjust for itemizing, age, dependents, or a spouse who itemizes.
        </p>
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <button className="button" type="submit">
          Save tax assumptions
        </button>
        {saved && <p role="status">Tax assumptions saved.</p>}
      </form>
    </details>
  );
}

type TradeEditorProps = {
  symbol: string;
  workspace: Workspace;
  update: (fn: (s: Workspace) => Workspace) => void;
  quote: DataEnvelope<Quote> | null;
};

export function TradeDialog({
  onClose,
  ...props
}: TradeEditorProps & {
  onClose: () => void;
}) {
  return (
    <Modal
      title={`${props.symbol} · Trades & taxes`}
      subtitle="Personal USD trade ledger · FIFO lots · No brokerage connection or order execution"
      wide
      onClose={onClose}
    >
      <TradeEditor {...props} />
    </Modal>
  );
}

export function TradeEditor({
  symbol,
  workspace,
  update,
  quote,
  costBasisOnly = false,
}: TradeEditorProps & { costBasisOnly?: boolean }) {
  const trades = workspace.trades[symbol] ?? [];
  const position = calculatePosition(trades);
  const [editing, setEditing] = useState<Trade | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [scenario, setScenario] = useState<Omit<Trade, "id" | "side"> | null>(
    null,
  );
  const foreign = quote !== null && quote.data.currency !== "USD";
  const usableQuote =
    !foreign && quote && !quote.source.demo ? quote.data.price : null;
  let sale: ReturnType<typeof simulateSale> | null = null,
    saleError = "";
  try {
    if (scenario) sale = simulateSale(trades, scenario);
  } catch (e) {
    saleError = e instanceof Error ? e.message : "Invalid sale";
  }
  const hasLossHistory = position.disposals.some((d) => d.gain < 0);
  const taxBlocked = sale?.hasLoss || hasLossHistory;
  const tax =
    sale &&
    workspace.taxProfile &&
    !taxBlocked &&
    scenario?.date.startsWith("2026-")
      ? estimateTax(workspace.taxProfile, sale.shortGain, sale.longGain)
      : null;
  function commit(next: Trade[]) {
    try {
      calculatePosition(next);
      update((s) => {
        // Refuse stale overwrite if another widget/tab changed the ledger meanwhile.
        if (JSON.stringify(s.trades[symbol] ?? []) !== JSON.stringify(trades))
          throw new Error(
            "Trade history changed. Reopen this position and try again.",
          );
        return { ...s, trades: { ...s.trades, [symbol]: next } };
      });
      setError("");
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid trade history");
      return false;
    }
  }
  return (
    <>
      <div className="portfolio-summary">
        <div>
          <span>Shares held</span>
          <strong>{position.quantity}</strong>
        </div>
        <div>
          <span>Average cost / share</span>
          <strong>{usd(position.averageCost)}</strong>
        </div>
        <div>
          <span>Remaining cost basis</span>
          <strong>{usd(position.basis)}</strong>
        </div>
        <div>
          <span>Realized gain / loss</span>
          <strong>{usd(position.realizedGain)}</strong>
        </div>
        <div>
          <span>All-buy weighted average</span>
          <strong>{usd(position.purchaseAverage)}</strong>
        </div>
        <div>
          <span>Unrealized gain / loss</span>
          <strong>
            {usableQuote === null
              ? "—"
              : usd(position.quantity * usableQuote - position.basis)}
          </strong>
        </div>
      </div>
      <p className="muted">
        Average cost includes purchase fees and reflects remaining FIFO lots
        after sales. All-buy average includes shares already sold. Neither
        average replaces individual tax-lot basis. Positions are shared across
        watchlists; removing a ticker does not delete its trades.
      </p>
      {foreign ? (
        <p className="warning-banner">
          This quote is in {quote.data.currency}. This MVP supports USD trades
          only; foreign-currency tax conversion is not supported.
        </p>
      ) : (
        <>
          <h3>{editing ? "Edit trade" : "Record a trade"}</h3>
          <form
            key={formKey}
            className="form-stack"
            onSubmit={(e) => {
              e.preventDefault();
              const form = new FormData(e.currentTarget);
              const parsed = tradeSchema.safeParse({
                id: editing?.id ?? crypto.randomUUID(),
                side: form.get("side"),
                date: form.get("date"),
                quantity: Number(form.get("quantity")),
                price: Number(form.get("price")),
                fees: Number(form.get("fees")),
              });
              if (!parsed.success) {
                setError(
                  "Check date, positive shares, price, and fees. Sale fees cannot exceed proceeds.",
                );
                return;
              }
              if (parsed.data.date > today()) {
                setError(
                  "Recorded trades cannot be in the future. Use the sale scenario below.",
                );
                return;
              }
              const next = editing
                ? trades.map((t) => (t.id === editing.id ? parsed.data : t))
                : [...trades, parsed.data];
              if (commit(next)) {
                setEditing(null);
                setFormKey((k) => k + 1);
                setMessage("Trade saved.");
              }
            }}
          >
            <div className="portfolio-fields trade-fields">
              <label>
                Action
                <select
                  aria-label="Action"
                  name="side"
                  defaultValue={editing?.side ?? "buy"}
                >
                  <option value="buy">Buy</option>
                  <option value="sell">Sell</option>
                </select>
              </label>
              <label>
                Trade date
                <input
                  name="date"
                  type="date"
                  required
                  max={today()}
                  defaultValue={editing?.date ?? today()}
                />
              </label>
              <NumberField
                name="quantity"
                label="Shares"
                value={editing?.quantity}
                positive
              />
              <NumberField
                name="price"
                label="Price per share (USD)"
                value={editing?.price}
              />
              <NumberField
                name="fees"
                label="Trade fees (USD)"
                value={editing?.fees ?? 0}
              />
            </div>
            <div className="portfolio-actions">
              <button className="button primary" type="submit">
                {editing ? "Save trade changes" : "Add trade"}
              </button>
              {editing && (
                <button
                  className="button"
                  type="button"
                  onClick={() => {
                    setEditing(null);
                    setFormKey((k) => k + 1);
                  }}
                >
                  Cancel edit
                </button>
              )}
            </div>
          </form>
        </>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      <div className="table-scroll ledger-table">
        <table className="watch-table">
          <caption>Trade history · same-day entries use entry order</caption>
          <thead>
            <tr>
              <th>Date</th>
              <th>Action</th>
              <th>Shares</th>
              <th>Price</th>
              <th>Fees</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {[...trades]
              .sort((a, b) => a.date.localeCompare(b.date))
              .map((t, index) => (
                <tr key={t.id}>
                  <td>{t.date}</td>
                  <td>{t.side}</td>
                  <td>{t.quantity}</td>
                  <td>{usd(t.price)}</td>
                  <td>{usd(t.fees)}</td>
                  <td>
                    <button
                      className="text-button"
                      aria-label={`Edit trade ${index + 1}`}
                      disabled={foreign}
                      onClick={() => {
                        setEditing(t);
                        setFormKey((k) => k + 1);
                        setMessage("");
                      }}
                    >
                      Edit
                    </button>{" "}
                    <button
                      className="text-button"
                      aria-label={`Delete trade ${index + 1}`}
                      onClick={() => {
                        if (
                          window.confirm(
                            "Delete this trade record? This does not place or cancel an order.",
                          )
                        ) {
                          if (commit(trades.filter((x) => x.id !== t.id))) {
                            setEditing(null);
                            setFormKey((k) => k + 1);
                            setMessage(
                              "Trade deleted. Restore from a workspace export if needed.",
                            );
                          }
                        }
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
        {!trades.length && (
          <p className="empty-inline">
            No trades yet. Add your first purchase above.
          </p>
        )}
      </div>
      {!costBasisOnly && (
        <>
          <TaxSettings
            profile={workspace.taxProfile}
            onSave={(taxProfile) => update((s) => ({ ...s, taxProfile }))}
          />
          <section className="sale-scenario">
            <h3>What if I sell?</h3>
            <p className="muted">
              A hypothetical FIFO sale, not an order.{" "}
              {usableQuote === null
                ? "Enter your own USD sale price; demo or unavailable quotes are never used automatically."
                : `Prefilled from a delayed quote retrieved ${new Date(quote!.source.asOf).toLocaleString()}; edit the price for your scenario.`}
            </p>
            <form
              className="form-stack"
              onSubmit={(e) => {
                e.preventDefault();
                const form = new FormData(e.currentTarget);
                setScenario({
                  date: String(form.get("date")),
                  quantity: Number(form.get("quantity")),
                  price: Number(form.get("price")),
                  fees: Number(form.get("fees")),
                });
              }}
            >
              <div className="portfolio-fields">
                <label>
                  Sale date
                  <input
                    name="date"
                    type="date"
                    required
                    defaultValue={today()}
                  />
                </label>
                <NumberField
                  label="Shares to sell"
                  name="quantity"
                  value={position.quantity || ""}
                  positive
                />
                <NumberField
                  label="Hypothetical sale price (USD)"
                  name="price"
                  value={usableQuote ?? ""}
                />
                <NumberField label="Sale fees (USD)" name="fees" value={0} />
              </div>
              <button
                type="submit"
                className="button"
                disabled={!position.quantity || foreign}
              >
                Estimate sale
              </button>
            </form>
            {saleError && (
              <p role="alert" className="form-error">
                {saleError}
              </p>
            )}
            {sale && (
              <div className="sale-result" aria-live="polite">
                <div className="portfolio-summary">
                  <div>
                    <span>Short-term gain / loss</span>
                    <strong>{usd(sale.shortGain)}</strong>
                  </div>
                  <div>
                    <span>Long-term gain / loss</span>
                    <strong>{usd(sale.longGain)}</strong>
                  </div>
                </div>
                {!workspace.taxProfile && (
                  <p>Save tax assumptions above to estimate liability.</p>
                )}
                {!scenario?.date.startsWith("2026-") && (
                  <p className="warning-banner">
                    Tax tables currently support 2026 only. Gains are shown, but
                    no tax estimate is calculated for this date.
                  </p>
                )}
                {taxBlocked && (
                  <p className="warning-banner">
                    Tax estimate withheld: this scenario or history contains a
                    loss. Wash-sale basis adjustments and annual loss
                    carryforwards require broker/tax review, including purchases
                    within 30 days before or after a loss sale. Gains above are
                    unadjusted FIFO figures.
                  </p>
                )}
                {tax && (
                  <>
                    <div className="portfolio-summary tax-result">
                      <div>
                        <span>Federal regular tax increase</span>
                        <strong>{usd(tax.regular)}</strong>
                      </div>
                      <div>
                        <span>Additional NIIT</span>
                        <strong>{usd(tax.niit)}</strong>
                      </div>
                      <div>
                        <span>North Carolina tax increase</span>
                        <strong>{usd(tax.state)}</strong>
                      </div>
                      <div>
                        <span>Estimated additional tax</span>
                        <strong>{usd(tax.total)}</strong>
                      </div>
                      <div>
                        <span>Effective rate on gain</span>
                        <strong>{tax.effectiveRate.toFixed(2)}%</strong>
                      </div>
                      <div>
                        <span>Sale proceeds after fees & estimated tax</span>
                        <strong>
                          {usd(
                            scenario!.quantity * scenario!.price -
                              scenario!.fees -
                              tax.total,
                          )}
                        </strong>
                      </div>
                    </div>
                    <p className="muted">
                      2026 planning estimate only; incremental liability, not
                      your annual tax bill or a broker tax statement.
                    </p>
                  </>
                )}
                <details>
                  <summary>FIFO lots used</summary>
                  <div className="table-scroll">
                    <table className="watch-table">
                      <thead>
                        <tr>
                          <th>Acquired</th>
                          <th>Shares</th>
                          <th>Basis</th>
                          <th>Net proceeds</th>
                          <th>Gain / loss</th>
                          <th>Term</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sale.lots.map((l) => (
                          <tr key={l.lotId}>
                            <td>{l.acquired}</td>
                            <td>{l.quantity}</td>
                            <td>{usd(l.basis)}</td>
                            <td>{usd(l.proceeds)}</td>
                            <td>{usd(l.gain)}</td>
                            <td>{l.longTerm ? "Long" : "Short"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              </div>
            )}
          </section>
          <p className="warning-banner">
            Estimates assume complete, already split-adjusted USD share records
            and full-year NC residency. No automatic splits, transfers,
            dividends, specific-lot elections, wash-sale adjustments, annual
            loss netting, credits, AMT, deduction phaseouts, or NC income
            adjustments. Verify basis and assumptions with your broker or tax
            professional before relying on these amounts.
          </p>
          <p className="portfolio-sources">
            Sources:{" "}
            <a
              target="_blank"
              rel="noreferrer"
              href="https://www.irs.gov/irb/2025-45_IRB"
            >
              2026 IRS brackets
            </a>{" "}
            ·{" "}
            <a
              target="_blank"
              rel="noreferrer"
              href="https://www.irs.gov/publications/p550"
            >
              FIFO & holding periods
            </a>{" "}
            ·{" "}
            <a
              target="_blank"
              rel="noreferrer"
              href="https://www.irs.gov/individuals/net-investment-income-tax"
            >
              NIIT
            </a>{" "}
            ·{" "}
            <a
              target="_blank"
              rel="noreferrer"
              href="https://www.ncdor.gov/taxes-forms/individual-income-tax/tax-rate-schedules"
            >
              NC rates
            </a>{" "}
            ·{" "}
            <a
              target="_blank"
              rel="noreferrer"
              href="https://www.ncdor.gov/taxes-forms/individual-income-tax/filing-topics/north-carolina-standard-deduction-or-north-carolina-itemized-deductions"
            >
              NC deductions
            </a>
          </p>
        </>
      )}
    </>
  );
}
