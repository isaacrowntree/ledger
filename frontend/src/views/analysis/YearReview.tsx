import { useState } from "react";
import { MonthlyBars, RankedBars } from "../../components/charts";
import { Figure, Figures, Loaded, Money, PageHead, Section } from "../../components/ui";
import { displayDate, money } from "../../lib/format";
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
              <Section title="Spending by category">
                <div className="grid-2">
                  <RankedBars limit={12} items={spendCats.map((c) => ({ label: c.category, value: -c.total }))} />
                  <table className="ledger">
                    <thead><tr><th>Category</th><th className="num">Count</th><th className="num">Total</th><th className="num">A month</th></tr></thead>
                    <tbody>
                      {spendCats.map((c) => (
                        <tr key={c.category}><td>{c.category}</td><td className="num muted">{c.count}</td>
                          <td className="num"><Money value={c.total} /></td><td className="num muted"><Money value={c.total / months} tone="plain" /></td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
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
