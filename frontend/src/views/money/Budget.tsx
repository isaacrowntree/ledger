import { useState } from "react";
import { budgetStatus, CategoryLabel, Meter, PageLayout, StatusTag, Summary, TxnLink } from "../../components/layout";
import { Loaded, Money } from "../../components/ui";
import { isoDate, monthLong } from "../../lib/format";
import { monthRange } from "../../lib/periods";
import { useBudget } from "../../lib/queries";

/** How far through a month we are: 1 for a past month, 0 for a future one. */
export function monthElapsed(month: string, now = new Date()): number {
  const current = isoDate(now).slice(0, 7);
  if (month < current) return 1;
  if (month > current) return 0;
  const daysIn = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  return now.getDate() / daysIn;
}

export function Budget() {
  const [month, setMonth] = useState(isoDate(new Date()).slice(0, 7));
  const budget = useBudget(month);
  const now = new Date();
  const elapsed = monthElapsed(month, now);
  const current = elapsed > 0 && elapsed < 1;
  const daysIn = new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0).getDate();
  const daysLeft = current ? daysIn - now.getDate() + 1 : 0;
  const range = monthRange(month);

  return (
    <Loaded query={budget}>
      {(rows) => {
        const set = rows.filter((b) => b.budget > 0 && !b.is_income)
          .sort((a, b) => b.actual / b.budget - a.actual / a.budget);
        const planned = set.reduce((s, b) => s + b.budget, 0);
        const spent = set.reduce((s, b) => s + b.actual, 0);
        const left = planned - spent;
        const over = set.filter((b) => b.actual > b.budget).length;
        return (
          <PageLayout
            title="Budget"
            lede={current
              ? `${monthLong(month)}, day ${now.getDate()} of ${daysIn}. The tick on each bar is where an even pace would be today.`
              : monthLong(month)}
            controls={<input type="month" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month" />}
            aside={set.length ? (
              <Summary title={monthLong(month)} rows={[
                { label: "Planned", value: <Money value={planned} cents={false} tone="plain" /> },
                { label: "Spent", value: <Money value={spent} cents={false} tone="plain" /> },
                { label: left >= 0 ? "Left" : "Over", value: <Money value={left} cents={false} />, strong: true },
                ...(current && left > 0 ? [{ label: "A day, to stay on plan", value: <Money value={left / daysLeft} cents={false} tone="plain" /> }] : []),
                { label: "Categories over", value: over ? <StatusTag status="over" label={String(over)} /> : <StatusTag status="ok" label="None" /> },
              ]}>
                <p className="hint">Only categories with a monthly budget are counted. Set them in config/categories.yaml.</p>
              </Summary>
            ) : undefined}
          >
            {!set.length ? (
              <p className="hint">No monthly budgets set. Add budget_monthly to categories in config/categories.yaml.</p>
            ) : (
              <div className="table-wrap">
                <table className="ledger budget">
                  <thead>
                    <tr><th>Category</th><th className="num">Planned</th><th className="num">Spent</th><th className="num">Left</th>
                      <th className="bar-col">Progress</th><th>Status</th></tr>
                  </thead>
                  <tbody>
                    {set.map((b) => (
                      <tr key={b.category}>
                        <td><TxnLink category={b.category} from={range.from} to={range.to}><CategoryLabel category={b.category} /></TxnLink></td>
                        <td className="num"><Money value={b.budget} cents={false} tone="plain" /></td>
                        <td className="num"><Money value={b.actual} cents={false} tone="plain" /></td>
                        <td className="num"><Money value={b.budget - b.actual} cents={false} /></td>
                        <td className="bar-col"><Meter category={b.category} actual={b.actual} planned={b.budget} elapsed={current ? elapsed : undefined} /></td>
                        <td><StatusTag status={budgetStatus(b.actual, b.budget, elapsed)} /></td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="total"><td>Total</td><td className="num"><Money value={planned} cents={false} tone="plain" /></td>
                      <td className="num"><Money value={spent} cents={false} tone="plain" /></td>
                      <td className="num"><Money value={left} cents={false} /></td><td className="bar-col" /><td /></tr>
                  </tfoot>
                </table>
              </div>
            )}
          </PageLayout>
        );
      }}
    </Loaded>
  );
}
