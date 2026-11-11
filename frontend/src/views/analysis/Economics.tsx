import { useState } from "react";
import { LineChart, RankedBars, Waterfall } from "../../components/charts";
import { Figure, Figures, Loaded, Money, PageHead, Section } from "../../components/ui";
import { money, pct } from "../../lib/format";
import { useEconomics, useSyncCpi } from "../../lib/queries";

export function Economics() {
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear);
  const q = useEconomics(String(year));
  const sync = useSyncCpi();

  return (
    <>
      <PageHead title="Tax & inflation" lede="What your salary buys after income tax, after the taxes paid through spending, and after prices have moved.">
        <select value={year} onChange={(e) => setYear(Number(e.target.value))} aria-label="Year">
          {Array.from({ length: 11 }, (_, i) => thisYear - i).map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
        <button type="button" disabled={sync.isPending} onClick={() => sync.mutate()}>
          {sync.isPending ? "Fetching CPI…" : sync.isSuccess ? `CPI updated (${sync.data.rows_synced} rows)` : "Fetch CPI from the ABS"}
        </button>
      </PageHead>
      {sync.isError && <div className="error">Couldn't fetch CPI from the ABS: {(sync.error as Error).message}</div>}
      <Loaded query={q}>
        {(d) => {
          const sp = d.spending_power;
          const tax = d.tax_analysis;
          const hasCpi = d.cpi.current_index != null;
          const ind = d.indirect_taxes ?? { lines: [], total_tax: 0, total: 0 };
          return (
            <>
              {!hasCpi && (
                <div className="notice">No CPI figures loaded, so inflation-adjusted values are blank. Use “Fetch CPI from the ABS” above.</div>
              )}
              <Figures>
                <Figure label="Salary" note={sp.salary_real != null ? `${money(sp.salary_real, { cents: false })} in ${d.cpi.base_year} dollars` : undefined}>
                  <Money value={sp.salary} cents={false} />
                </Figure>
                <Figure label="Income tax and Medicare" note={`${tax.effective_rate}% of salary`}><Money value={tax.total_tax} cents={false} /></Figure>
                <Figure label="After tax"><Money value={tax.after_tax} cents={false} /></Figure>
                <Figure label="CPI, year on year" note={d.cpi.current_index != null ? `Index ${d.cpi.current_index}` : undefined}>{pct(d.cpi.yoy_change)}</Figure>
                <Figure label="After-tax savings rate"
                        note="Share of after-tax salary left once spending is paid, in today's prices. Not the same as the overview's figure, which counts all income before tax.">
                  {pct(sp.real_savings_rate)}
                </Figure>
              </Figures>

              <Section title="From salary to what's left after every tax"
                       aside={sp.salary > 0
                         ? `All taxes ${money(tax.total_tax + ind.total_tax, { cents: false })}, ${((tax.total_tax + ind.total_tax) / sp.salary * 100).toFixed(0)}% of salary`
                         : undefined}>
                {sp.salary > 0 ? (
                  <Waterfall steps={[
                    { label: "Salary", value: sp.salary, kind: "in" },
                    { label: "Income tax", value: -tax.payg, kind: "out" },
                    { label: "Medicare levy", value: -tax.medicare, kind: "out" },
                    { label: "After income tax", value: tax.after_tax, kind: "result" },
                    ...ind.lines.map((l) => ({ label: `${l.name}${l.estimate ? " (est.)" : ""}`, value: -l.amount, kind: "out" as const })),
                    { label: "Left after taxes and charges", value: tax.after_tax - ind.total, kind: "result" },
                  ]} />
                ) : <p className="hint">No salary recorded for {year}.</p>}
                {ind.lines.length > 0 && (
                  <>
                    <p className="hint">
                      Taxes paid through spending, worked out from {year}'s transactions. Spending includes shared
                      household bills at the full amount you paid, so these cover the household, not just you.
                    </p>
                    <table className="ledger">
                      <thead><tr><th>Tax or charge</th><th className="num">Amount</th><th>How it was worked out</th></tr></thead>
                      <tbody>
                        {ind.lines.map((l) => (
                          <tr key={l.name}>
                            <td>{l.name}<div className="muted" style={{ fontSize: 12.5 }}>{l.estimate ? "Estimate" : "Actual"}{l.kind === "charge" ? " · charge, not a tax" : ""}</div></td>
                            <td className="num"><Money value={-l.amount} /></td>
                            <td className="muted">{l.basis}</td>
                          </tr>
                        ))}
                        <tr className="total"><td>Taxes through spending</td><td className="num"><Money value={-ind.total_tax} /></td><td /></tr>
                        {ind.total !== ind.total_tax && (
                          <tr className="total"><td>With tolls and other charges</td><td className="num"><Money value={-ind.total} /></td><td /></tr>
                        )}
                      </tbody>
                    </table>
                  </>
                )}
              </Section>

              {tax.tax_breakdown.length > 0 && (
                <Section title="Where your tax goes" aside="Federal budget shares applied to your tax">
                  <RankedBars limit={12} items={tax.tax_breakdown.map((b) => ({ label: b.category, value: b.amount, note: `${b.pct}%` }))} />
                </Section>
              )}

              {d.inflation_adjusted_spending.length > 0 && (
                <Section title="Spending, adjusted for inflation" aside={hasCpi ? `In ${d.cpi.base_year} dollars` : undefined}>
                  <table className="ledger">
                    <thead><tr><th>Category</th><th className="num">As spent</th>{hasCpi && <><th className="num">In {d.cpi.base_year} dollars</th><th className="num">Real change</th></>}</tr></thead>
                    <tbody>
                      {d.inflation_adjusted_spending.map((s) => (
                        <tr key={s.category}><td>{s.category}</td><td className="num"><Money value={-s.nominal} /></td>
                          {hasCpi && <><td className="num">{s.real != null ? money(s.real) : "—"}</td>
                            <td className="num">{s.real_change_pct != null ? `${s.real_change_pct > 0 ? "+" : ""}${s.real_change_pct.toFixed(1)}%` : "—"}</td></>}</tr>
                      ))}
                    </tbody>
                  </table>
                </Section>
              )}

              {d.cpi_history.length > 1 && (
                <Section title="Consumer price index, Australia">
                  <LineChart label="CPI index over time" format={(v) => v.toFixed(1)}
                             points={d.cpi_history.map((h) => ({ x: h.period, y: h.index_value }))} />
                </Section>
              )}
            </>
          );
        }}
      </Loaded>
    </>
  );
}
