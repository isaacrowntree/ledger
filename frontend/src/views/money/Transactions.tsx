import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type RowSelectionState,
  type SortingState,
} from "@tanstack/react-table";
import { api, type Transaction } from "../../api";
import { PageLayout, PeriodPicker, Summary } from "../../components/layout";
import { Loaded, Money } from "../../components/ui";
import { displayDate } from "../../lib/format";
import { categoryColor } from "../../lib/categoryColors";
import { parseQuery, tokenValue, withToken, type TxnSearch } from "../../lib/txnQuery";
import {
  useAccountList,
  useCategories,
  useSharedMutations,
  useTransactionTotals,
  useTransactions,
  useUpdateTransaction,
} from "../../lib/queries";

const col = createColumnHelper<Transaction>();

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

// Quick filters: each is a typed `is:` token in the search box, so what the
// buttons do is visible, editable and shareable in the URL.
const QUICK: { value: string; label: string; excludes?: string }[] = [
  { value: "review", label: "To review" },
  { value: "shared", label: "Shared" },
  { value: "in", label: "Money in", excludes: "out" },
  { value: "out", label: "Money out", excludes: "in" },
];
const hasIs = (q: string, v: string) => new RegExp(`(^|\\s)is:${v}(\\s|$)`, "i").test(q);
const dropIs = (q: string, v: string) => q.replace(new RegExp(`(^|\\s)is:${v}(?=\\s|$)`, "gi"), " ").replace(/\s+/g, " ").trim();
const toggleIs = (q: string, v: string, excludes?: string) => {
  if (hasIs(q, v)) return dropIs(q, v);
  return `${excludes ? dropIs(q, excludes) : q} is:${v}`.trim();
};

export function Transactions() {
  const search = useSearch({ from: "/money/transactions" });
  const navigate = useNavigate({ from: "/money/transactions" });
  const setSearch = (next: Partial<TxnSearch>) =>
    navigate({ search: (prev: TxnSearch) => clean({ ...prev, ...next }), replace: true });

  // The box is edited locally and written to the URL once typing pauses.
  const [draft, setDraft] = useState(search.q ?? "");
  useEffect(() => setDraft(search.q ?? ""), [search.q]);
  const debounced = useDebounced(draft, 350);
  useEffect(() => {
    if (debounced !== (search.q ?? "")) setSearch({ q: debounced });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const [limit, setLimit] = useState(300);
  const filters = useMemo(() => {
    const f: Record<string, string> = { ...parseQuery(search.q) };
    if (search.from) f.from = search.from;
    if (search.to) f.to = search.to;
    return f;
  }, [search.q, search.from, search.to]);
  const txns = useTransactions({ ...filters, limit: String(limit) });
  const totals = useTransactionTotals(filters);
  const toReview = useTransactionTotals({ review: "1" });
  const categories = useCategories();
  const accounts = useAccountList();
  const q = search.q ?? "";

  return (
    <PageLayout
      title="Transactions"
      lede="Change a category or add a note and it saves straight away."
      aside={
        <Loaded query={totals}>
          {(t) => (
            <Summary
              title={t.count === 1 ? "1 transaction" : `${t.count.toLocaleString("en-AU")} transactions`}
              rows={[
                { label: "Money in", value: <Money value={t.money_in} tone="hold" /> },
                { label: "Money out", value: <Money value={t.money_out} /> },
                { label: "Net", value: <Money value={t.net} sign />, strong: true },
                ...(t.first ? [{ label: "Dates", value: <span className="muted">{displayDate(t.first)} to {displayDate(t.last ?? "")}</span> }] : []),
              ]}
            >
              {!filters.transfers && <p className="hint">Transfers between your own accounts are left out. Add <code>is:transfer</code> to include them.</p>}
              {(toReview.data?.count ?? 0) > 0 && !filters.review && (
                <button type="button" className="review-link" onClick={() => setSearch({ q: toggleIs(q, "review") })}>
                  <i className="review-dot" aria-hidden /> {toReview.data!.count} recent transaction{toReview.data!.count === 1 ? "" : "s"} to review
                </button>
              )}
            </Summary>
          )}
        </Loaded>
      }
    >
      <div className="toolbar">
        <input type="search" className="query" value={draft} onChange={(e) => setDraft(e.target.value)}
               placeholder='Search, or filter: category:"Eating Out" is:shared amount>50'
               aria-label="Search and filter transactions" />
        <PeriodPicker from={search.from} to={search.to} onChange={(r) => setSearch(r)} />
        <select value={tokenValue(q, "category")} aria-label="Category"
                onChange={(e) => setSearch({ q: withToken(q, "category", e.target.value) })}>
          <option value="">All categories</option>
          {categories.data?.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}
        </select>
        <select value={tokenValue(q, "account")} aria-label="Account"
                onChange={(e) => setSearch({ q: withToken(q, "account", e.target.value) })}>
          <option value="">All accounts</option>
          {accounts.data?.map((a) => <option key={a.id} value={a.name}>{a.name}</option>)}
        </select>
      </div>
      <div className="chips" role="group" aria-label="Quick filters">
        {QUICK.map((f) => (
          <button key={f.value} type="button" className="chip-toggle" aria-pressed={hasIs(q, f.value)}
                  onClick={() => setSearch({ q: toggleIs(q, f.value, f.excludes) })}>
            {f.label}
          </button>
        ))}
        {(q || search.from || search.to) && (
          <button type="button" className="quiet" onClick={() => navigate({ search: {}, replace: true })}>Clear filters</button>
        )}
      </div>
      <Loaded query={txns}>
        {(rows) => (
          <>
            <TxnTable rows={rows} categories={categories.data?.map((c) => c.name) ?? []} grouped />
            {rows.length >= limit ? (
              <p className="hint">
                Showing the newest {limit} of {totals.data?.count.toLocaleString("en-AU") ?? "many"}.{" "}
                <button type="button" className="quiet" onClick={() => setLimit((l) => l + 500)}>Show 500 more</button>
              </p>
            ) : rows.length === 0 ? (
              <p className="hint">Nothing matches. Try a wider period or remove a filter.</p>
            ) : null}
          </>
        )}
      </Loaded>
    </PageLayout>
  );
}

function clean(s: TxnSearch): TxnSearch {
  return Object.fromEntries(Object.entries(s).filter(([, v]) => v)) as TxnSearch;
}

/** After a category change: offer to file the merchant's other transactions the same way. */
interface Offer { description: string; category: string; ids: number[] }

export function TxnTable({ rows, categories, grouped = false }: { rows: Transaction[]; categories: string[]; grouped?: boolean }) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [offer, setOffer] = useState<Offer | null>(null);
  const [busy, setBusy] = useState("");
  const update = useUpdateTransaction();
  const shared = useSharedMutations();

  const changeCategory = async (t: Transaction, category: string) => {
    await update.mutateAsync({ id: t.id, data: { category_name: category } });
    const same = await api.transactions({ search: t.description, transfers: "1", limit: "1000" });
    const ids = same.filter((o) => o.id !== t.id && o.description === t.description && o.category_name !== category).map((o) => o.id);
    setOffer(ids.length ? { description: t.description, category, ids } : null);
  };

  const columns = useMemo(
    () => [
      col.display({
        id: "select",
        header: ({ table }) => (
          <input type="checkbox" aria-label="Select all shown" checked={table.getIsAllRowsSelected()}
                 ref={(el) => { if (el) el.indeterminate = table.getIsSomeRowsSelected(); }}
                 onChange={table.getToggleAllRowsSelectedHandler()} />
        ),
        cell: ({ row }) => (
          <input type="checkbox" aria-label={`Select ${row.original.description}`} checked={row.getIsSelected()}
                 onChange={row.getToggleSelectedHandler()} />
        ),
      }),
      col.accessor("date", { header: "Date", sortDescFirst: true, cell: (c) => <span style={{ whiteSpace: "nowrap" }}>{displayDate(c.getValue())}</span> }),
      col.accessor("description", {
        header: "Description",
        cell: (c) => {
          const t = c.row.original;
          const fx = t.original_currency && t.original_amount && Math.abs(t.amount) !== Math.abs(t.original_amount);
          return (
            <span className="desc">
              {t.needs_review ? <i className="review-dot" title="New, and the category hasn't been checked" /> : null}
              {t.description}
              <span className="desc-meta">
                {fx && <span>{t.original_currency} {t.original_amount!.toFixed(2)}</span>}
                {t.is_transfer ? <span>Transfer</span> : null}
                {t.is_shared ? <span className="tag-shared">Shared</span> : null}
                {t.tags.map((g) => <span key={g} className="tag">{g}</span>)}
              </span>
            </span>
          );
        },
      }),
      col.accessor("amount", {
        header: () => <span>Amount</span>,
        cell: (c) => <Money value={c.getValue()} tone="hold" />,
        meta: { num: true },
      }),
      col.accessor("category_name", {
        header: "Category",
        cell: (c) => (
          <span className="cat-cell">
            <i className="cat-dot" style={{ background: categoryColor(c.getValue()) }} />
            <SavedSelect value={c.getValue() ?? ""} options={categories}
                         onSave={(v) => changeCategory(c.row.original, v)} />
          </span>
        ),
      }),
      col.accessor("account_name", { header: "Account", cell: (c) => <span className="muted">{c.getValue()}</span> }),
      col.accessor("notes", {
        header: "Note",
        enableSorting: false,
        cell: (c) => (
          <SavedInput value={c.getValue() ?? ""}
                      onSave={(v) => update.mutateAsync({ id: c.row.original.id, data: { notes: v } })} />
        ),
      }),
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [categories],
  );

  // Group rows under their date while the list is in date order; the date
  // column is then redundant and hidden.
  const byDate = grouped && sorting.length === 0;
  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting, rowSelection: selection, columnVisibility: { date: !byDate } },
    getRowId: (t) => String(t.id),
    enableRowSelection: true,
    onRowSelectionChange: setSelection,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const picked = table.getSelectedRowModel().rows.map((r) => r.original);
  const pickedTotal = picked.reduce((s, t) => s + t.amount, 0);
  const each = async (label: string, fn: (t: Transaction) => Promise<unknown>, list = picked) => {
    setBusy(label);
    try {
      for (const t of list) await fn(t);
    } finally {
      setBusy("");
    }
  };

  const visible = table.getRowModel().rows;
  const dayTotals = useMemo(() => {
    const m = new Map<string, number>();
    for (const t of rows) m.set(t.date, (m.get(t.date) ?? 0) + t.amount);
    return m;
  }, [rows]);

  return (
    <>
      {offer && (
        <div className="notice offer">
          <span>
            {offer.ids.length} other “{offer.description}” transaction{offer.ids.length === 1 ? " is" : "s are"} filed differently.
            Move {offer.ids.length === 1 ? "it" : "them"} to {offer.category} too?
          </span>
          <span>
            <button type="button" disabled={!!busy}
                    onClick={async () => {
                      await each("apply", (t) => update.mutateAsync({ id: t.id, data: { category_name: offer.category } }),
                        offer.ids.map((id) => ({ id }) as Transaction));
                      setOffer(null);
                    }}>
              {busy === "apply" ? "Moving…" : `Move ${offer.ids.length}`}
            </button>{" "}
            <button type="button" className="quiet" onClick={() => setOffer(null)}>Leave them</button>
          </span>
        </div>
      )}
      {picked.length > 0 && (
        <div className="bulk-bar" role="region" aria-label="Selected transactions">
          <strong>{picked.length} selected</strong>
          <span>totalling <Money value={pickedTotal} sign /></span>
          <select value="" aria-label="Set category for selected" disabled={!!busy}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v) each("category", (t) => update.mutateAsync({ id: t.id, data: { category_name: v } }));
                  }}>
            <option value="">Set category…</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <button type="button" disabled={!!busy}
                  onClick={() => each("share", (t) => shared.add.mutateAsync(t.id),
                    picked.filter((t) => t.amount < 0 && !t.is_shared && !t.is_transfer))}>
            {busy === "share" ? "Sharing…" : "Add to shared"}
          </button>
          <button type="button" disabled={!!busy}
                  onClick={() => each("review", (t) => update.mutateAsync({ id: t.id, data: { reviewed: true } }),
                    picked.filter((t) => t.needs_review))}>
            {busy === "review" ? "Saving…" : "Mark reviewed"}
          </button>
          <button type="button" className="quiet" onClick={() => setSelection({})}>Clear</button>
        </div>
      )}
      <div className="table-wrap">
        <table className="ledger txns">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const num = (h.column.columnDef.meta as { num?: boolean } | undefined)?.num;
                  const dir = h.column.getIsSorted();
                  return (
                    <th key={h.id} className={`${num ? "num" : ""} ${h.column.id === "select" ? "pick" : ""}`}
                        aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined}>
                      {h.column.getCanSort() ? (
                        <button type="button" className="sort" onClick={h.column.getToggleSortingHandler()}>
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {dir === "asc" ? " ↑" : dir === "desc" ? " ↓" : ""}
                        </button>
                      ) : flexRender(h.column.columnDef.header, h.getContext())}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {visible.map((row, i) => {
              const date = row.original.date;
              const newDay = byDate && (i === 0 || visible[i - 1].original.date !== date);
              return [
                newDay ? (
                  <tr key={`d-${date}`} className="day">
                    <td colSpan={2}>{dayLabel(date)}</td>
                    <td className="num"><Money value={dayTotals.get(date) ?? 0} tone="plain" sign /></td>
                    <td colSpan={3} />
                  </tr>
                ) : null,
                <tr key={row.id} className={row.getIsSelected() ? "picked" : ""}>
                  {row.getVisibleCells().map((cell) => {
                    const num = (cell.column.columnDef.meta as { num?: boolean } | undefined)?.num;
                    return (
                      <td key={cell.id} className={`${num ? "num" : ""} ${cell.column.id === "select" ? "pick" : ""}`}
                          style={cell.column.id === "category_name" ? { minWidth: 170 } : cell.column.id === "notes" ? { minWidth: 140 } : undefined}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    );
                  })}
                </tr>,
              ];
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function dayLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return `${WEEKDAYS[d.getDay()]} ${displayDate(iso)}`;
}

function useSavedFlash() {
  const [saved, setSaved] = useState(false);
  const flash = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 900);
  };
  return [saved, flash] as const;
}

function SavedSelect({ value, options, onSave }: { value: string; options: string[]; onSave: (v: string) => Promise<unknown> }) {
  const [v, setV] = useState(value);
  const [saved, flash] = useSavedFlash();
  const [failed, setFailed] = useState("");
  useEffect(() => setV(value), [value]);
  return (
    <select className={`cell-edit ${saved ? "saved" : ""} ${failed ? "failed" : ""}`} value={v} aria-label="Category"
            title={failed || undefined}
            onChange={async (e) => {
              const next = e.target.value;
              setV(next);
              try {
                await onSave(next);
                setFailed("");
                flash();
              } catch (err) {
                setV(value);
                setFailed(`Not saved: ${(err as Error).message}`);
              }
            }}>
      {!options.includes(v) && <option value={v}>{v || "Uncategorized"}</option>}
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function SavedInput({ value, onSave }: { value: string; onSave: (v: string) => Promise<unknown> }) {
  const [v, setV] = useState(value);
  const [saved, flash] = useSavedFlash();
  const [failed, setFailed] = useState("");
  useEffect(() => setV(value), [value]);
  return (
    <input type="text" className={`cell-edit ${saved ? "saved" : ""} ${failed ? "failed" : ""}`} value={v}
           placeholder="Add a note" aria-label="Note" title={failed || undefined}
           onChange={(e) => setV(e.target.value)}
           onBlur={async () => {
             if (v === value) return;
             try {
               await onSave(v);
               setFailed("");
               flash();
             } catch (err) {
               setFailed(`Not saved: ${(err as Error).message}`);
             }
           }} />
  );
}

