// Page structure shared by every view: a header with the page's controls, an
// optional summary panel beside the main content on wide screens, a period
// picker, a status tag, a budget meter, and links that drill through to the
// matching transactions.
import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import { categoryColor } from "../lib/categoryColors";
import { money } from "../lib/format";
import { PERIODS, periodOf, periodRange, type PeriodKey } from "../lib/periods";
import { txnSearch } from "../lib/txnQuery";

export function PageLayout({
  title,
  lede,
  controls,
  aside,
  children,
}: {
  title: string;
  lede?: ReactNode;
  controls?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={`page ${aside ? "has-aside" : ""}`}>
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          {lede ? <p className="lede">{lede}</p> : null}
        </div>
        {controls ? <div className="controls">{controls}</div> : null}
      </header>
      <div className="page-body">
        <div className="page-main">{children}</div>
        {aside ? <aside className="page-aside">{aside}</aside> : null}
      </div>
    </div>
  );
}

/** A labelled list of figures for a summary panel. */
export function Summary({ title, rows, children }: {
  title: string;
  rows: { label: ReactNode; value: ReactNode; strong?: boolean }[];
  children?: ReactNode;
}) {
  return (
    <div className="summary">
      <h2>{title}</h2>
      <dl>
        {rows.map((r, i) => (
          <div key={i} className={r.strong ? "strong" : ""}>
            <dt>{r.label}</dt>
            <dd>{r.value}</dd>
          </div>
        ))}
      </dl>
      {children}
    </div>
  );
}

/** Named periods plus a custom range. Calls back with the dates. */
export function PeriodPicker({ from = "", to = "", onChange }: {
  from?: string;
  to?: string;
  onChange: (r: { from: string; to: string }) => void;
}) {
  const key = periodOf(from, to);
  return (
    <span className="period-picker">
      <select value={key} aria-label="Period"
              onChange={(e) => {
                const k = e.target.value as PeriodKey | "custom";
                if (k !== "custom") onChange(periodRange(k));
              }}>
        {PERIODS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        <option value="custom">Custom dates</option>
      </select>
      {key === "custom" && (
        <>
          <input type="date" value={from} aria-label="From" onChange={(e) => onChange({ from: e.target.value, to })} />
          <span className="muted">to</span>
          <input type="date" value={to} aria-label="To" onChange={(e) => onChange({ from, to: e.target.value })} />
        </>
      )}
    </span>
  );
}

export type Status = "ok" | "pace" | "over" | "due" | "paid" | "none";

const STATUS: Record<Status, { icon: string; label: string }> = {
  ok: { icon: "✓", label: "On track" },
  pace: { icon: "!", label: "Ahead of pace" },
  over: { icon: "✕", label: "Over" },
  due: { icon: "○", label: "Due" },
  paid: { icon: "●", label: "Paid" },
  none: { icon: "–", label: "Not set" },
};

/** A state with an icon and a word, never colour alone. */
export function StatusTag({ status, label }: { status: Status; label?: string }) {
  const s = STATUS[status];
  return (
    <span className={`status status-${status}`}>
      <span aria-hidden className="status-icon">{s.icon}</span>
      {label ?? s.label}
    </span>
  );
}

/** Where spending stands against a plan: on track, ahead of an even pace, or over. */
export function budgetStatus(actual: number, planned: number, elapsed: number): Status {
  if (planned <= 0) return "none";
  if (actual > planned) return "over";
  return actual / planned > elapsed + 0.1 ? "pace" : "ok";
}

/** A bar of spending against a plan, in the category's colour, with a tick at an even pace. */
export function Meter({ category, actual, planned, elapsed }: {
  category: string;
  actual: number;
  planned: number;
  elapsed?: number;
}) {
  const over = actual > planned;
  return (
    <div className="meter-track" role="img"
         aria-label={`${category}: ${money(actual, { cents: false })} of ${money(planned, { cents: false })}`}>
      <div className="meter-fill" style={{
        width: `${Math.min(100, (actual / Math.max(planned, 0.01)) * 100)}%`,
        background: over ? "var(--owe)" : categoryColor(category),
      }} />
      {elapsed !== undefined && elapsed < 1 && (
        <span className="meter-pace" style={{ left: `${elapsed * 100}%` }} aria-hidden />
      )}
    </div>
  );
}

/** A link to the transactions behind a figure. */
export function TxnLink({ children, className, ...filter }: {
  children: ReactNode;
  className?: string;
  category?: string;
  account?: string;
  tag?: string;
  text?: string;
  from?: string;
  to?: string;
}) {
  return (
    <Link to="/money/transactions" search={txnSearch(filter)} className={className}>
      {children}
    </Link>
  );
}

export function CategoryLabel({ category }: { category: string }) {
  return (
    <span className="cat-cell">
      <i className="cat-dot" style={{ background: categoryColor(category) }} />
      {category}
    </span>
  );
}
