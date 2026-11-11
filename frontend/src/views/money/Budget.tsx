import { categoryColor } from "../../lib/categoryColors";
import { useState } from "react";
import { Loaded, PageHead } from "../../components/ui";
import { isoDate, monthLong, money } from "../../lib/format";
import { useBudget } from "../../lib/queries";

export function Budget() {
  const [month, setMonth] = useState(isoDate(new Date()).slice(0, 7));
  const budget = useBudget(month);
  const now = new Date();
  const current = month === isoDate(now).slice(0, 7);
  const daysIn = new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0).getDate();
  const elapsed = current ? now.getDate() / daysIn : 1;

  return (
    <>
      <PageHead
        title="Budget"
        lede={current
          ? `${monthLong(month)}, ${now.getDate()} of ${daysIn} days in. The tick on each bar marks where you'd be at an even pace.`
          : monthLong(month)}
      >
        <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month" />
      </PageHead>
      <Loaded query={budget}>
        {(rows) => {
          const set = rows.filter((b) => b.budget > 0);
          if (!set.length) return <p className="hint">No monthly budgets set. Add budget_monthly to categories in config/categories.yaml.</p>;
          return (
            <div style={{ maxWidth: 720 }}>
              {set.map((b) => {
                const over = b.actual > b.budget;
                const ahead = !over && b.actual / b.budget > elapsed + 0.1;
                return (
                  <div key={b.category} className={`meter ${over ? "over" : ""}`}>
                    <div className="meter-head">
                      <span className="cat-cell"><i className="cat-dot" style={{ background: categoryColor(b.category) }} />{b.category}</span>
                      <span className="state">
                        {money(b.actual, { cents: false })} of {money(b.budget, { cents: false })}
                        {over ? ` · ${money(b.actual - b.budget, { cents: false })} over`
                          : ahead ? " · ahead of pace" : ` · ${money(b.budget - b.actual, { cents: false })} left`}
                      </span>
                    </div>
                    <div className="meter-track" style={{ position: "relative" }}>
                      <div className="meter-fill cat" style={{ width: `${Math.min(100, (b.actual / b.budget) * 100)}%`, ["--cat" as string]: categoryColor(b.category) }} />
                      {current && (
                        <span aria-hidden style={{ position: "absolute", left: `${elapsed * 100}%`, top: -2, bottom: -2,
                          width: 2, background: "var(--ink)" }} />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          );
        }}
      </Loaded>
    </>
  );
}
