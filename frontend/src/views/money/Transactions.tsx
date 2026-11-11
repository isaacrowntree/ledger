import { useEffect, useMemo, useState } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import type { Transaction } from "../../api";
import { Loaded, Money, PageHead } from "../../components/ui";
import { displayDate } from "../../lib/format";
import { useAccountList, useCategories, useSharedMutations, useTransactions, useUpdateTransaction } from "../../lib/queries";

const col = createColumnHelper<Transaction>();

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function Transactions() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [category, setCategory] = useState("");
  const [account, setAccount] = useState("");
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(200);
  const debouncedSearch = useDebounced(search, 300);
  const filters = useMemo(
    () => ({ from, to, category, account, search: debouncedSearch, limit: String(limit) }),
    [from, to, category, account, debouncedSearch, limit],
  );
  const txns = useTransactions(filters);
  const categories = useCategories();
  const accounts = useAccountList();

  return (
    <>
      <PageHead title="Transactions" lede="Change a category or add a note and it saves straight away." />
      <div className="controls">
        <input type="search" placeholder="Search descriptions" value={search} onChange={(e) => setSearch(e.target.value)}
               aria-label="Search descriptions" />
        <label className="check">From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="check">To <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
          <option value="">All categories</option>
          {categories.data?.map((c) => <option key={c.id} value={c.name}>{c.name}</option>)}
        </select>
        <select value={account} onChange={(e) => setAccount(e.target.value)} aria-label="Account">
          <option value="">All accounts</option>
          {accounts.data?.map((a) => <option key={a.id} value={a.name}>{a.name}</option>)}
        </select>
      </div>
      <Loaded query={txns}>
        {(rows) => (
          <>
            <TxnTable rows={rows} categories={categories.data?.map((c) => c.name) ?? []} />
            {rows.length >= limit ? (
              <p className="hint">
                Showing the newest {limit}.{" "}
                <button type="button" className="quiet" onClick={() => setLimit((l) => l + 500)}>Show 500 more</button>
              </p>
            ) : (
              <p className="hint">{rows.length} transactions.</p>
            )}
          </>
        )}
      </Loaded>
    </>
  );
}

function TxnTable({ rows, categories }: { rows: Transaction[]; categories: string[] }) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const update = useUpdateTransaction();
  const shared = useSharedMutations();
  const [sharedIds, setSharedIds] = useState<Set<number>>(new Set());

  const columns = useMemo(
    () => [
      col.accessor("date", { header: "Date", cell: (c) => <span style={{ whiteSpace: "nowrap" }}>{displayDate(c.getValue())}</span> }),
      col.accessor("description", {
        header: "Description",
        cell: (c) => {
          const t = c.row.original;
          const fx = t.original_currency && t.original_amount && Math.abs(t.amount) !== Math.abs(t.original_amount);
          return (
            <span>
              {t.description}
              {fx && <div className="muted" style={{ fontSize: 12.5 }}>{t.original_currency} {t.original_amount!.toFixed(2)}</div>}
              {t.is_transfer ? <div className="muted" style={{ fontSize: 12.5 }}>Transfer</div> : null}
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
        enableSorting: true,
        cell: (c) => (
          <SavedSelect
            value={c.getValue() ?? ""}
            options={categories}
            onSave={(v) => update.mutateAsync({ id: c.row.original.id, data: { category_name: v } })}
          />
        ),
      }),
      col.accessor("account_name", { header: "Account", cell: (c) => <span className="muted">{c.getValue()}</span> }),
      col.accessor("notes", {
        header: "Note",
        enableSorting: false,
        cell: (c) => (
          <SavedInput
            value={c.getValue() ?? ""}
            onSave={(v) => update.mutateAsync({ id: c.row.original.id, data: { notes: v } })}
          />
        ),
      }),
      col.display({
        id: "share",
        header: "",
        cell: (c) => {
          const t = c.row.original;
          if (t.amount >= 0 || t.is_transfer) return null;
          const done = sharedIds.has(t.id);
          return (
            <button type="button" className="quiet" disabled={done}
                    title="Add to shared expenses"
                    onClick={async () => {
                      await shared.add.mutateAsync(t.id);
                      setSharedIds((s) => new Set(s).add(t.id));
                    }}>
              {done ? "Shared" : "Share"}
            </button>
          );
        },
      }),
    ],
    [categories, update, shared.add, sharedIds],
  );

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="table-wrap">
      <table className="ledger">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => {
                const num = (h.column.columnDef.meta as { num?: boolean } | undefined)?.num;
                const dir = h.column.getIsSorted();
                return (
                  <th key={h.id} className={num ? "num" : ""}
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
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => {
                const num = (cell.column.columnDef.meta as { num?: boolean } | undefined)?.num;
                return (
                  <td key={cell.id} className={num ? "num" : ""}
                      style={cell.column.id === "category_name" ? { minWidth: 170 } : cell.column.id === "notes" ? { minWidth: 140 } : undefined}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
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
  useEffect(() => setV(value), [value]);
  return (
    <select className={`cell-edit ${saved ? "saved" : ""}`} value={v} aria-label="Category"
            onChange={async (e) => {
              setV(e.target.value);
              await onSave(e.target.value);
              flash();
            }}>
      {!options.includes(v) && <option value={v}>{v || "Uncategorized"}</option>}
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function SavedInput({ value, onSave }: { value: string; onSave: (v: string) => Promise<unknown> }) {
  const [v, setV] = useState(value);
  const [saved, flash] = useSavedFlash();
  useEffect(() => setV(value), [value]);
  return (
    <input type="text" className={`cell-edit ${saved ? "saved" : ""}`} value={v} placeholder="Add a note" aria-label="Note"
           onChange={(e) => setV(e.target.value)}
           onBlur={async () => {
             if (v !== value) {
               await onSave(v);
               flash();
             }
           }} />
  );
}
