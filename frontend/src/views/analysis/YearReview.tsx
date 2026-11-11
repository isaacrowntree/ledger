import { categoryColor } from "../../lib/categoryColors";
import { useState } from "react";
import { MonthlyBars, RankedBars } from "../../components/charts";
import { Figure, Figures, Loaded, Money, PageHead, Section } from "../../components/ui";
import { displayDate, money, monthShort } from "../../lib/format";
import { CategoryLabel, TxnLink } from "../../components/layout";
import { monthRange } from "../../lib/periods";
import type { PeriodSummary } from "../../api";
import { usePeriod, useYearReview } from "../../lib/queries";

export function YearReview() {
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear - 1);
  // Totals come from the same definition as the overview, so the two agree.
  const period = usePeriod(`${year}-01-01`, `${year}-12-31`);
  const prev = usePeriod(`${year - 1}-01-01`, `${year - 1}-12-31`);
  const extras = useYearReview(String(year));

  return (
    <>
      <PageHead title={`${year} in review`}>
        <select value={year} onChange={(e) => setYear(Number(e.target.value))} aria-label="Year">
          {Array.from({ length: 11 }, (_, i) => thisYear - i).map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
      </PageHead>
      <Loaded query={period}>
        {(p) => {
          const t = p.totals;
          const pt = prev.data?.totals;
          const change = (a: number, b?: number) => (b ? `${a >= b ? "+" : "−"}${Math.abs(((a - b) / b) * 100).toFixed(1)}% on ${year - 1}` : undefined);
          const spendCats = p.categories.filter((c) => !c.is_income && c.total < 0);
          const months = Math.max(t.months, 1);
          return (
            <>
              <Figures>
                <Figure label="Income" note={change(t.income, pt?.income)}><Money value={t.income} cents={false} /></Figure>
                <Figure label="Spending" note={change(t.spending, pt?.spending)}><Money value={-t.spending} cents={false} /></Figure>
                {t.partner_name && (
                  <Figure label={`${t.partner_name}'s contributions`}><Money value={t.partner_contributions} cents={false} /></Figure>
                )}
                <Figure label="Left over, counting contributions" note={t.partner_name ? `Before contributions: ${money(t.net, { cents: false, sign: true })}` : undefined}>
                  <Money value={t.net_with_partner} cents={false} sign />
                </Figure>
                <Figure label="Average month's spending"><Money value={-t.spending / months} cents={false} /></Figure>
              </Figures>
              <div style={{ marginTop: 18 }}>
                <MonthlyBars rows={p.months} series={[
                  { key: "income", label: "Income", color: "var(--series-income)" },
                  { key: "spending", label: "Spending", color: "var(--series-spending)" },
                ]} />
              </div>
              <Section title="Spending by category" aside="Each cell opens its transactions">
                <CategoryMatrix series={p.series} months={p.months.map((m) => m.month)} order={spendCats.map((c) => c.category)} />
              </Section>
              <Section title="Biggest categories">
                <RankedBars limit={12} items={spendCats.map((c) => ({ label: c.category, value: -c.total, color: categoryColor(c.category),
                  link: { from: `${year}-01-01`, to: `${year}-12-31` } }))} />
              </Section>
            </>
          );
        }}
      </Loaded>
      <Loaded query={extras}>
        {(d) => (
          <>
            {d.business.length > 0 && (
              <Section title="Business expenses">
                <table className="ledger" style={{ maxWidth: 560 }}><tbody>
                  {d.business.map((b) => <tr key={b.category}><td>{b.category}</td><td className="num muted">{b.count}</td><td className="num"><Money value={-Math.abs(b.total)} /></td></tr>)}
                </tbody></table>
              </Section>
            )}
            <div className="grid-2">
              <Section title="Where you spent most often">
                <table className="ledger"><thead><tr><th>Merchant</th><th className="num">Visits</th><th className="num">Total</th></tr></thead><tbody>
                  {d.top_merchants.map((m) => <tr key={m.description}><td>{m.description}</td><td className="num muted">{m.count}</td><td className="num"><Money value={-Math.abs(m.total)} /></td></tr>)}
                </tbody></table>
              </Section>
              <Section title="Biggest single expenses">
                <table className="ledger"><tbody>
                  {d.biggest_expenses.map((t, i) => <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{displayDate(t.date)}</td><td>{t.description}</td><td className="num"><Money value={-Math.abs(t.amount)} /></td></tr>)}
                </tbody></table>
              </Section>
            </div>
            <Section title="Biggest money in">
              <table className="ledger"><tbody>
                {d.biggest_income.map((t, i) => <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{displayDate(t.date)}</td><td>{t.description}</td><td className="num"><Money value={t.amount} /></td></tr>)}
              </tbody></table>
            </Section>
            <p className="hint">Sources: {d.sources.map((s) => `${s.source_type} (${s.count.toLocaleString("en-AU")})`).join(", ")}.</p>
          </>
        )}
      </Loaded>
    </>
  );
}

/** Months across, categories down: where the money went, and when. Shading
 * runs light to dark within each row, so a category's heavy months stand out;
 * the figures carry the actual amounts. */
function CategoryMatrix({ series, months, order }: { series: PeriodSummary["series"]; months: string[]; order: string[] }) {
  const cell = new Map<string, number>();
  for (const s of series) if (!s.is_income) cell.set(`${s.category}|${s.month}`, -s.total);
  const n = Math.max(months.length, 1);
  return (
    <div className="table-wrap">
      <table className="ledger matrix">
        <thead>
          <tr><th>Category</th>{months.map((m) => <th key={m} className="num">{monthShort(m)}</th>)}
            <th className="num">A month</th><th className="num">Total</th></tr>
        </thead>
        <tbody>
          {order.map((cat) => {
            const vals = months.map((m) => cell.get(`${cat}|${m}`) ?? 0);
            const total = vals.reduce((a, b) => a + b, 0);
            const max = Math.max(...vals, 1);
            return (
              <tr key={cat}>
                <td><CategoryLabel category={cat} /></td>
                {months.map((m, i) => {
                  const v = vals[i];
                  const r = monthRange(m);
                  return (
                    <td key={m} className="num heat"
                        style={{ ["--heat" as string]: `${Math.round((Math.max(v, 0) / max) * 38)}%` }}>
                      {Math.abs(v) >= 0.5 ? (
                        <TxnLink category={cat} from={r.from} to={r.to}>{money(v, { cents: false })}</TxnLink>
                      ) : <span className="muted">–</span>}
                    </td>
                  );
                })}
                <td className="num muted">{money(total / n, { cents: false })}</td>
                <td className="num"><strong>{money(total, { cents: false })}</strong></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
