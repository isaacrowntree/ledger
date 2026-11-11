import { categoryColor } from "../lib/categoryColors";
import { Link } from "@tanstack/react-router";
import type { AccountSummary, Holding } from "../api";
import { CumulativeSpend, MonthlyBars, RankedBars } from "../components/charts";
import { budgetStatus, CategoryLabel, Meter, StatusTag, TxnLink } from "../components/layout";
import { monthElapsed } from "./money/Budget";
import { Figure, Figures, Loaded, Money, Section } from "../components/ui";
import { displayDate, isoDate, money, monthLong } from "../lib/format";
import {
  useAccounts, useBudget, useCategories, usePeriod, useSchedules, useShared, useTransactionTotals, useTransactions,
} from "../lib/queries";

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
            <h1 className="sr-only">Overview</h1>
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

            <ThisMonth partner={partner} />

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
                          .map((c) => ({ label: c.category, value: -c.total, color: categoryColor(c.category), link: range }))}
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

// Bills that land once a month or less would swamp the day-to-day line, so
// housing is left out of it (it still counts in every total).
const NOT_DAY_TO_DAY = /^(Mortgage|Strata|Rent|Rental:)/;

function ThisMonth({ partner }: { partner: string }) {
  const now = new Date();
  const month = isoDate(now).slice(0, 7);
  const prevStart = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const prevMonth = isoDate(prevStart).slice(0, 7);
  const from = isoDate(prevStart);
  const today = isoDate(now);
  const txns = useTransactions({ from, to: today, limit: "5000" });
  const categories = useCategories();
  const budget = useBudget(month);
  const schedules = useSchedules();
  const review = useTransactionTotals({ review: "1" });
  const elapsed = monthElapsed(month, now);
  const monthEnd = isoDate(new Date(now.getFullYear(), now.getMonth() + 1, 0));

  const lines = (() => {
    if (!txns.data || !categories.data) return null;
    const income = new Set(categories.data.filter((c) => c.is_income).map((c) => c.name));
    const daysPrev = new Date(now.getFullYear(), now.getMonth(), 0).getDate();
    const daysCur = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    const cur = Array(daysCur).fill(0);
    const prev = Array(daysPrev).fill(0);
    for (const t of txns.data) {
      const cat = t.category_name ?? "Uncategorized";
      if (income.has(cat) || NOT_DAY_TO_DAY.test(cat)) continue;
      const day = Number(t.date.slice(8, 10)) - 1;
      if (t.date.startsWith(month)) cur[day] -= t.amount;
      else if (t.date.startsWith(prevMonth)) prev[day] -= t.amount;
    }
    const run = (a: number[]) => { let s = 0; return a.map((v) => (s += v)); };
    const curRun: (number | null)[] = run(cur).map((v, i) => (i < now.getDate() ? v : null));
    const prevRun = run(prev);
    return { cur: curRun, prev: prevRun, soFar: curRun[now.getDate() - 1] ?? 0, prevSameDay: prevRun[Math.min(now.getDate(), daysPrev) - 1] };
  })();

  const due = (schedules.data?.schedules ?? [])
    .filter((s) => s.next_due <= monthEnd)
    .map((s) => ({ name: s.name, date: s.next_due, amount: s.their_share }));

  return (
    <Section title={`This month: ${monthLong(month)}`} aside={<TxnLink from={`${month}-01`} to={monthEnd}>All this month's transactions</TxnLink>}>
      <div className="band">
        <div>
          {lines ? (
            <>
              <p className="band-lede">
                Day-to-day spending so far is <strong>{money(lines.soFar, { cents: false })}</strong>,{" "}
                {Math.abs(lines.soFar - lines.prevSameDay) < 1 ? "the same as" : (
                  <>{money(Math.abs(lines.soFar - lines.prevSameDay), { cents: false })} {lines.soFar > lines.prevSameDay ? "more than" : "less than"}</>
                )} by this day last month. Mortgage, strata and rent are left out of this line.
              </p>
              <CumulativeSpend current={lines.cur} previous={lines.prev}
                               currentLabel={monthLong(month)} previousLabel={monthLong(prevMonth)} />
            </>
          ) : <div className="loading">Loading…</div>}
        </div>
        <div className="band-side">
          <h3>Budgets to watch</h3>
          <Loaded query={budget}>
            {(rows) => {
              const watched = rows.filter((b) => b.budget > 0 && !b.is_income)
                .sort((a, b) => b.actual / b.budget - a.actual / a.budget).slice(0, 4);
              return watched.length ? (
                <div className="watch">
                  {watched.map((b) => (
                    <div key={b.category} className="watch-row">
                      <div className="watch-head">
                        <TxnLink category={b.category} from={`${month}-01`} to={monthEnd}><CategoryLabel category={b.category} /></TxnLink>
                        <StatusTag status={budgetStatus(b.actual, b.budget, elapsed)}
                                   label={`${money(b.actual, { cents: false })} of ${money(b.budget, { cents: false })}`} />
                      </div>
                      <Meter category={b.category} actual={b.actual} planned={b.budget} elapsed={elapsed} />
                    </div>
                  ))}
                  <Link to="/money/budget" className="more">Every budget</Link>
                </div>
              ) : <p className="hint">No monthly budgets set.</p>;
            }}
          </Loaded>
          {due.length > 0 && (
            <>
              <h3>Expected from {partner} this month</h3>
              {due.map((d) => (
                <div className="line" key={d.name}>
                  <span>{d.name}<div className="sub">next due {displayDate(d.date)}</div></span>
                  <Money value={d.amount} />
                </div>
              ))}
            </>
          )}
          {(review.data?.count ?? 0) > 0 && (
            <Link to="/money/transactions" search={{ q: "is:review" }} className="review-link">
              <i className="review-dot" aria-hidden /> {review.data!.count} new transaction{review.data!.count === 1 ? "" : "s"} to review
            </Link>
          )}
        </div>
      </div>
    </Section>
  );
}
