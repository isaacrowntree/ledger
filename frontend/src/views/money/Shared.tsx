import { useMemo, useState } from "react";
import type { SharedExpenseItem } from "../../api";
import { Figure, Figures, Loaded, Money, PageHead, Segmented } from "../../components/ui";
import { displayDate, money } from "../../lib/format";
import { useShared, useSharedMutations } from "../../lib/queries";

type Dim = "none" | "category" | "tag";

function keys(item: SharedExpenseItem, dim: Dim): string[] {
  if (dim === "category") return [item.category_name || "Uncategorized"];
  if (dim === "tag") return item.tags.length ? item.tags : ["(untagged)"];
  return [];
}

export function Shared() {
  const q = useShared();
  const [hideSettled, setHideSettled] = useState(true);
  const [groupBy, setGroupBy] = useState<Dim>("none");
  const [filter, setFilter] = useState("");
  const [exclude, setExclude] = useState(false);

  return (
    <>
      <PageHead title="Shared expenses" lede="Household costs you've paid, split with your partner. Tick a row once it's been paid back." />
      <Loaded query={q}>
        {(data) => {
          // The filter scopes every figure; Hide settled only declutters the table.
          const scoped = !filter ? data.items : data.items.filter((i) => {
            const sep = filter.indexOf(":");
            return keys(i, filter.slice(0, sep) as Dim).includes(filter.slice(sep + 1)) !== exclude;
          });
          const owed = scoped.reduce((s, i) => s + i.share_amount, 0);
          const settled = scoped.reduce((s, i) => s + (i.is_settled ? i.share_amount : 0), 0);
          const visible = hideSettled ? scoped.filter((i) => !i.is_settled) : scoped;
          return (
            <>
              <Figures>
                <Figure label="Their share of everything"><Money value={owed} /></Figure>
                <Figure label="Paid back"><Money value={settled} /></Figure>
                <Figure label="Still owing"><Money value={owed - settled} /></Figure>
              </Figures>
              <div className="controls" style={{ marginTop: 16 }}>
                <label className="check">
                  <input type="checkbox" checked={hideSettled} onChange={(e) => setHideSettled(e.target.checked)} />
                  Hide paid back
                </label>
                <span className="muted">Group by</span>
                <Segmented label="Group by" value={groupBy} onChange={setGroupBy}
                           options={[{ value: "none", label: "None" }, { value: "category", label: "Category" }, { value: "tag", label: "Tag" }]} />
                <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter">
                  <option value="">Everything</option>
                  <optgroup label="Category">
                    {data.by_category.map((g) => <option key={g.key} value={`category:${g.key}`}>{g.key}</option>)}
                  </optgroup>
                  <optgroup label="Tag">
                    {data.by_tag.map((g) => <option key={g.key} value={`tag:${g.key}`}>{g.key}</option>)}
                  </optgroup>
                </select>
                {filter && (
                  <label className="check">
                    <input type="checkbox" checked={exclude} onChange={(e) => setExclude(e.target.checked)} />
                    Exclude this instead
                  </label>
                )}
              </div>
              <SharedTable items={visible} groupBy={groupBy} />
            </>
          );
        }}
      </Loaded>
    </>
  );
}

function SharedTable({ items, groupBy }: { items: SharedExpenseItem[]; groupBy: Dim }) {
  const groups = useMemo(() => {
    if (groupBy === "none") return [{ key: "", rows: items }];
    const m = new Map<string, SharedExpenseItem[]>();
    for (const it of items) for (const k of keys(it, groupBy)) m.set(k, [...(m.get(k) ?? []), it]);
    const owing = (rows: SharedExpenseItem[]) => rows.reduce((s, i) => s + (i.is_settled ? 0 : i.share_amount), 0);
    return [...m.entries()].sort((a, b) => owing(b[1]) - owing(a[1])).map(([key, rows]) => ({ key, rows }));
  }, [items, groupBy]);

  if (!items.length) return <p className="hint">Nothing matches. Clear the filter or show paid-back rows.</p>;
  return (
    <div className="table-wrap">
      <table className="ledger">
        <thead>
          <tr>
            <th>Date</th><th>Description</th><th className="num">Full amount</th><th>Their %</th>
            <th className="num">Their share</th><th>Category</th><th>Paid back</th><th />
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <GroupRows key={g.key} label={g.key} rows={g.rows} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GroupRows({ label, rows }: { label: string; rows: SharedExpenseItem[] }) {
  const owing = rows.reduce((s, i) => s + (i.is_settled ? 0 : i.share_amount), 0);
  return (
    <>
      {label && (
        <tr className="group">
          <td colSpan={8}>{label} <span className="muted">· {money(owing)} owing</span></td>
        </tr>
      )}
      {rows.map((i) => <SharedRow key={i.id} item={i} />)}
    </>
  );
}

function SharedRow({ item }: { item: SharedExpenseItem }) {
  const { update, remove } = useSharedMutations();
  const [pct, setPct] = useState(String(item.split_pct));
  return (
    <tr className={item.is_settled ? "settled" : ""}>
      <td style={{ whiteSpace: "nowrap" }}>{displayDate(item.date)}</td>
      <td>
        {item.description}
        <div>{item.tags.map((t) => <span className="chip" key={t}>{t}</span>)}</div>
      </td>
      <td className="num"><Money value={Math.abs(item.amount)} tone="plain" /></td>
      <td>
        <input type="number" min={0} max={100} step={5} value={pct} aria-label="Their percentage"
               onChange={(e) => setPct(e.target.value)}
               onBlur={() => {
                 const n = parseFloat(pct);
                 if (n >= 0 && n <= 100 && n !== item.split_pct) update.mutate({ id: item.id, data: { split_pct: n } });
               }} />
      </td>
      <td className="num"><Money value={item.share_amount} /></td>
      <td className="muted">{item.category_name}</td>
      <td>
        <input type="checkbox" checked={!!item.is_settled} aria-label="Paid back"
               onChange={(e) => update.mutate({ id: item.id, data: { is_settled: e.target.checked } })} />
      </td>
      <td>
        <button type="button" className="quiet" title="Remove from shared expenses"
                onClick={() => remove.mutate(item.id)}>Remove</button>
      </td>
    </tr>
  );
}
