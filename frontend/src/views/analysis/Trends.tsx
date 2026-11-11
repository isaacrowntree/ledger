import { categoryColor } from "../../lib/categoryColors";
import { useMemo, useState } from "react";
import type { PeriodSummary } from "../../api";
import { SmallMultiples } from "../../components/charts";
import { Loaded, PageHead, Segmented } from "../../components/ui";
import { isoDate } from "../../lib/format";
import { usePeriod } from "../../lib/queries";

type Span = "12" | "24" | "60";

export function Trends() {
  const [span, setSpan] = useState<Span>("12");
  const [count, setCount] = useState(9);
  const now = new Date();
  // Complete months only, on the same definition as the overview.
  const from = isoDate(new Date(now.getFullYear(), now.getMonth() - Number(span), 1));
  const to = isoDate(new Date(now.getFullYear(), now.getMonth(), 0));
  const q = usePeriod(from, to);

  return (
    <>
      <PageHead title="Trends" lede="Monthly spending in your biggest categories, all on the same scale so the heights compare. Refunds count against the category they refund.">
        <Segmented label="Period" value={span} onChange={setSpan}
                   options={[{ value: "12", label: "12 months" }, { value: "24", label: "2 years" }, { value: "60", label: "5 years" }]} />
      </PageHead>
      <Loaded query={q}>
        {(p) => <TrendsBody p={p} count={count} more={() => setCount((c) => c + 9)} />}
      </Loaded>
    </>
  );
}

function TrendsBody({ p, count, more }: { p: PeriodSummary; count: number; more: () => void }) {
  const { groups, months } = useMemo(() => {
    const months: string[] = [];
    for (let d = new Date(`${p.from.slice(0, 7)}-01T00:00:00`); isoDate(d).slice(0, 7) <= p.to.slice(0, 7); d.setMonth(d.getMonth() + 1))
      months.push(isoDate(d).slice(0, 7));
    const by = new Map<string, Record<string, number>>();
    for (const r of p.series) {
      if (r.is_income) continue;
      const g = by.get(r.category) ?? {};
      g[r.month] = Math.max(0, -r.total);
      by.set(r.category, g);
    }
    const total = new Map(p.categories.map((c) => [c.category, -c.total]));
    const groups = [...by.entries()]
      .map(([label, values]) => ({ label, values, total: total.get(label) ?? 0, color: categoryColor(label) }))
      .filter((g) => g.total > 0)
      .sort((a, b) => b.total - a.total);
    return { groups, months };
  }, [p]);

  if (!groups.length) return <p className="hint">No spending in this period.</p>;
  return (
    <>
      <SmallMultiples groups={groups.slice(0, count)} months={months} />
      {groups.length > count && (
        <p className="hint"><button type="button" className="quiet" onClick={more}>Show {Math.min(9, groups.length - count)} more categories</button></p>
      )}
    </>
  );
}
