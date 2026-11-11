import { useState, type ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { money } from "../lib/format";

/** A dollar figure. Owed money is madder with a minus sign; `tone="hold"`
 * colours a positive figure green where holding it is the point. */
export function Money({
  value,
  tone = "auto",
  cents = true,
  sign = false,
}: {
  value: number;
  tone?: "auto" | "hold" | "plain";
  cents?: boolean;
  sign?: boolean;
}) {
  const owe = value < -0.004;
  const cls = tone === "plain" ? "" : owe ? "owe" : tone === "hold" && value > 0.004 ? "hold" : "";
  return <span className={`money ${cls}`}>{money(value, { cents, sign })}</span>;
}

export function Figures({ children }: { children: ReactNode }) {
  return <div className="figures">{children}</div>;
}

export function Figure({ label, children, note }: { label: string; children: ReactNode; note?: ReactNode }) {
  return (
    <div className="figure">
      <div className="figure-label">{label}</div>
      <div className="figure-value">{children}</div>
      {note ? <div className="figure-note">{note}</div> : null}
    </div>
  );
}

export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
        {aside ? <div className="muted">{aside}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function PageHead({ title, lede, children }: { title: string; lede?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {lede ? <p className="lede">{lede}</p> : null}
      </div>
      {children ? <div className="controls" style={{ marginBottom: 0 }}>{children}</div> : null}
    </div>
  );
}

/** Renders a query's data, or says plainly what went wrong and how to fix it. */
export function Loaded<T>({ query, children }: { query: UseQueryResult<T>; children: (data: T) => ReactNode }) {
  if (query.isError) {
    return (
      <div className="error">
        Could not load this from the ledger API: {(query.error as Error).message}. Check the server is running
        (<code>python -m api</code>) and reload.
      </div>
    );
  }
  if (query.data === undefined) return <div className="loading">Loading…</div>;
  return <>{children(query.data)}</>;
}

/** Click to copy an exact value (for typing into myTax). Says when it fails. */
export function CopyValue({ copy, children }: { copy: string; children: ReactNode }) {
  const [state, setState] = useState<"" | "done" | "failed">("");
  return (
    <button
      type="button"
      className={`copy ${state === "done" ? "done" : ""}`}
      title={`Copy ${copy}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(copy);
          setState("done");
        } catch {
          setState("failed");
        }
        setTimeout(() => setState(""), 1000);
      }}
    >
      {state === "done" ? "Copied" : state === "failed" ? "Copy failed" : children}
    </button>
  );
}

/** A sensitive identifier (TFN, date of birth): hidden until asked for. */
export function Masked({ value, copy }: { value: string; copy?: string }) {
  const [shown, setShown] = useState(false);
  if (!shown) {
    return (
      <span>
        <span className="muted">{value.replace(/[0-9A-Za-z]/g, "•")}</span>{" "}
        <button type="button" className="quiet" onClick={() => setShown(true)}>
          Show
        </button>
      </span>
    );
  }
  return copy ? <CopyValue copy={copy}>{value}</CopyValue> : <span>{value}</span>;
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
