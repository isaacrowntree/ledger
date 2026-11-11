import { categoryColor } from "../lib/categoryColors";
import { Link } from "@tanstack/react-router";
import type { AccountSummary, Holding } from "../api";
import { MonthlyBars, RankedBars } from "../components/charts";
import { Figure, Figures, Loaded, Money, Section } from "../components/ui";
import { displayDate, isoDate, money } from "../lib/format";
import { useAccounts, useBudget, usePeriod, useSchedules, useShared } from "../lib/queries";

const HOLDING_LABELS: Record<string, string> = {
  property: "Property", vehicle: "Vehicle", shares: "Shares", super: "Super", crypto: "Crypto",
};

/** The last twelve complete months, so every month on the chart compares like for like. */
function last12(): { from: string; to: string } {
  const now = new Date();
  return {
    from: isoDate(new Date(now.getFullYear() - 1, now.getMonth(), 1)),
    to: isoDate(new Date(now.getFullYear(), now.getMonth(), 0)),
  };
}

const share = (v: number, pct: number) => (v * pct) / 100;

export function Overview() {
  const accounts = useAccounts();
  const range = last12();
  const period = usePeriod(range.from, range.to);
  const shared = useShared();
  const schedules = useSchedules();
  const thisMonth = isoDate(new Date()).slice(0, 7);
  const budget = useBudget(thisMonth);

  return (
    <Loaded query={accounts}>
      {({ accounts: accts, holdings }) => {
        const cash = accts.filter((a) => !["credit", "loan"].includes(a.account_type) && a.balance > 0.004);
        const cards = accts.filter((a) => a.account_type === "credit" && Math.abs(a.balance) > 0.004);
        const loans = accts.filter((a) => a.account_type === "loan" && Math.abs(a.balance) > 0.004);
        const cashTotal = cash.reduce((s, a) => s + a.balance, 0);
        const cardTotal = cards.reduce((s, a) => s + a.balance, 0);
        const sharedOwing = shared.data?.balance_owing ?? 0;
        const scheduleOwing = schedules.data?.total_owing ?? 0;
        const owedToYou = sharedOwing + scheduleOwing;
        const partner = period.data?.totals.partner_name ?? "Your partner";

        const assetsYours = cashTotal + holdings.reduce((s, h) => s + share(h.current_value || 0, h.ownership_pct), 0);
        const debtsYours = cardTotal + loans.reduce((s, a) => s + share(a.balance, a.ownership_pct), 0);
        const netYours = assetsYours + debtsYours;
        const netHousehold = cashTotal + cardTotal + holdings.reduce((s, h) => s + (h.current_value || 0), 0)
          + loans.reduce((s, a) => s + a.balance, 0);
        const asOf = accts.map((a) => a.as_of).filter(Boolean).sort().at(-1) ?? "";

        return (
          <>
            <p className="muted" style={{ margin: 0 }}>Balances as of {displayDate(asOf)}</p>
            <p className="position">
              You have <strong>{money(cashTotal, { cents: false })}</strong> in your accounts
              {cards.length > 0 && (
                <> and owe <strong className="money owe">{money(-cardTotal, { cents: false })}</strong> on{" "}
                  {cards.length === 1 ? "one card" : `${cards.length} cards`}</>
              )}
              .{owedToYou > 0.5 && (
                <> {partner} owes you <strong>{money(owedToYou, { cents: false })}</strong>.</>
              )}
            </p>

            <div className="ledger-cols">
              <Column title="What you have" total={assetsYours}>
                {cash.map((a) => <AccountLine key={a.id} a={a} />)}
                {holdings.map((h) => <HoldingLine key={h.id} h={h} />)}
              </Column>
              <Column title="What you owe" total={debtsYours}>
                {cards.map((a) => <AccountLine key={a.id} a={a} />)}
                {loans.map((a) => <AccountLine key={a.id} a={a} />)}
              </Column>
              <Column title="Owed to you" total={owedToYou}>
                <div className="line">
                  <span>
                    Shared bills <span className="sub">· unsettled</span>
                  </span>
                  <Money value={sharedOwing} />
                </div>
                {schedules.data?.schedules.map((s) => (
                  <div className="line" key={s.name}>
                    <span>
                      {s.name}
                      <div className="sub">
                        {s.settle_enabled
                          ? `${s.payments.length} payment${s.payments.length === 1 ? "" : "s"} matched`
                          : "tracked by hand"}
                      </div>
                    </span>
                    <Money value={s.balance_owing} />
                  </div>
                ))}
                {schedules.data?.schedules.some((s) => s.settle_enabled && s.payments.length === 0 && s.balance_owing > 0) && (
                  <p className="hint">
                    No payments have been matched to this schedule. If {partner} has been paying, tag the transfers so
                    they match (see <Link to="/money/recurring">Recurring</Link>).
                  </p>
                )}
              </Column>
            </div>

            <Figures>
              <Figure label="Net worth, your share" note="Joint property and mortgage at your ownership share">
                <Money value={netYours} cents={false} />
              </Figure>
              <Figure label="Net worth, household" note="Everything at full value">
                <Money value={netHousehold} cents={false} />
              </Figure>
            </Figures>

            <Section title="This month" aside={<Link to="/money/budget">Budget</Link>}>
              <Loaded query={budget}>
                {(rows) => {
                  const watched = [...rows]
                    .filter((b) => b.budget > 0)
                    .sort((a, b) => b.actual / b.budget - a.actual / a.budget)
                    .slice(0, 4);
                  return watched.length ? (
                    <div className="grid-2">
                      {watched.map((b) => {
                        const over = b.actual > b.budget;
                        return (
                          <div key={b.category} className={`meter ${over ? "over" : ""}`}>
                            <div className="meter-head">
                              <span className="cat-cell"><i className="cat-dot" style={{ background: categoryColor(b.category) }} />{b.category}</span>
                              <span className="state">
                                {money(b.actual, { cents: false })} of {money(b.budget, { cents: false })}
                                {over ? ` · ${money(b.actual - b.budget, { cents: false })} over` : ""}
                              </span>
                            </div>
                            <div className="meter-track">
                              <div className="meter-fill cat" style={{ width: `${Math.min(100, (b.actual / b.budget) * 100)}%`, ["--cat" as string]: categoryColor(b.category) }} />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : <p className="hint">No monthly budgets set. Add them to config/categories.yaml.</p>;
                }}
              </Loaded>
            </Section>

            <Section title="The last 12 complete months" aside={`${displayDate(range.from)} to ${displayDate(range.to)}`}>
              <Loaded query={period}>
                {(p) => (
                  <>
                    <Figures>
                      <Figure label="Income"><Money value={p.totals.income} cents={false} /></Figure>
                      <Figure label="Spending" note="Includes mortgage interest and shared bills in full">
                        <Money value={-p.totals.spending} cents={false} />
                      </Figure>
                      {p.totals.partner_name && (
                        <Figure label={`${p.totals.partner_name}'s contributions`} note="Transfers towards shared costs">
                          <Money value={p.totals.partner_contributions} cents={false} />
                        </Figure>
                      )}
                      <Figure
                        label={p.totals.partner_name ? "Left over, counting contributions" : "Left over"}
                        note={p.totals.partner_name
                          ? `Before contributions: ${money(p.totals.net, { cents: false, sign: true })}`
                          : p.totals.savings_rate != null ? `${p.totals.savings_rate.toFixed(1)}% of income` : undefined}
                      >
                        <Money value={p.totals.net_with_partner} cents={false} sign />
                      </Figure>
                    </Figures>
                    <div style={{ marginTop: 18 }}>
                      <MonthlyBars
                        rows={p.months}
                        series={[
                          { key: "income", label: "Income", color: "var(--series-income)" },
                          { key: "spending", label: "Spending", color: "var(--series-spending)" },
                          ...(p.totals.partner_name
                            ? [{ key: "partner", label: `${p.totals.partner_name}'s contributions`, color: "var(--series-partner)" }]
                            : []),
                        ]}
                      />
                    </div>
                    <Section title="Where it went" aside={<Link to="/analysis/trends">Trends</Link>}>
                      <RankedBars
                        items={p.categories
                          .filter((c) => !c.is_income && c.total < 0)
                          .map((c) => ({ label: c.category, value: -c.total, color: categoryColor(c.category) }))}
                      />
                    </Section>
                  </>
                )}
              </Loaded>
            </Section>
          </>
        );
      }}
    </Loaded>
  );
}

function Column({ title, total, children }: { title: string; total: number; children: React.ReactNode }) {
  return (
    <div className="ledger-col">
      <h3>
        <span>{title}</span>
        <Money value={total} cents={false} />
      </h3>
      {children}
    </div>
  );
}

function AccountLine({ a }: { a: AccountSummary }) {
  const joint = a.ownership_pct < 100;
  return (
    <div className="line">
      <span>
        {a.name}
        {joint && <div className="sub">your {a.ownership_pct}% of {money(a.balance, { cents: false })}</div>}
        {a.balance_source !== "statement" && <div className="sub">from the last export, not a statement</div>}
      </span>
      <Money value={joint ? share(a.balance, a.ownership_pct) : a.balance} />
    </div>
  );
}

function HoldingLine({ h }: { h: Holding }) {
  const joint = h.ownership_pct < 100;
  return (
    <div className="line">
      <span>
        {h.name}
        <div className="sub">
          {HOLDING_LABELS[h.asset_type] ?? h.asset_type}
          {joint ? ` · your ${h.ownership_pct}% of ${money(h.current_value, { cents: false })}` : ""}
          {h.as_at_date ? ` · valued ${displayDate(h.as_at_date)}` : ""}
        </div>
      </span>
      <Money value={share(h.current_value || 0, h.ownership_pct)} cents={false} />
    </div>
  );
}
