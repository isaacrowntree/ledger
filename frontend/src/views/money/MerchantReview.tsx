import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type MerchantToReview } from "../../api";
import { PageLayout, Summary } from "../../components/layout";
import { Loaded, Money } from "../../components/ui";
import { displayDate } from "../../lib/format";
import { useCategories } from "../../lib/queries";

/**
 * Merchants waiting for a person's call: Claude filed them one way and the old
 * rules (or Claude's own doubt) suggest another. Choosing files the merchant,
 * refiles every unconfirmed row from it, and teaches it for next time.
 */
export function MerchantReview() {
  const q = useQuery({ queryKey: ["merchants-review"], queryFn: api.merchantsToReview });
  const cats = useCategories();
  const qc = useQueryClient();
  const [done, setDone] = useState(0);
  const [status, setStatus] = useState("");
  // After a decision the card disappears; focus moves to the card now in its place.
  const [focusIndex, setFocusIndex] = useState<number | null>(null);
  const headings = useRef<(HTMLHeadingElement | null)[]>([]);

  const decide = useMutation({
    mutationFn: ({ m, category }: { m: MerchantToReview; category: string; index: number }) =>
      api.decideMerchant(m.id, category),
    onSuccess: (res, { m, category, index }) => {
      setDone((n) => n + 1);
      setStatus(`Filed ${m.merchant} as ${category}: ${res.refiled} transaction${res.refiled === 1 ? "" : "s"} moved.`);
      setFocusIndex(index);
      for (const key of ["merchants-review", "transactions", "period", "budget", "trends", "year-review", "spreadsheet"])
        qc.invalidateQueries({ queryKey: [key] });
    },
  });

  useEffect(() => {
    if (focusIndex === null || q.isFetching) return;
    const n = Math.min(focusIndex, (q.data?.length ?? 1) - 1);
    headings.current[n]?.focus();
    setFocusIndex(null);
  }, [focusIndex, q.isFetching, q.data]);

  const names = (cats.data ?? []).map((c) => c.name).filter((n) => n !== "Uncategorized");
  const savingId = decide.isPending ? decide.variables?.m.id : undefined;

  return (
    <Loaded query={q}>
      {(list) => (
        <PageLayout
          title="Merchants to check"
          lede="Claude filed every merchant. These are the calls it left to you: it couldn't tell what was bought, or the change would move real money or tax figures. Pick the right category; every row from that merchant follows, and it's remembered."
          aside={
            <Summary title="Progress" rows={[
              { label: "Left to check", value: list.length, strong: true },
              { label: "Decided this visit", value: done },
              { label: "Money involved", value: <Money value={list.reduce((t, m) => t + Math.abs(m.total), 0)} tone="plain" /> },
            ]} />
          }
        >
          <p className="merchant-status muted" role="status" aria-live="polite">{status}</p>
          {decide.isError ? (
            <p className="error" role="alert">
              Couldn't file {decide.variables?.m.merchant}: {(decide.error as Error).message}. Nothing was changed; try again.
            </p>
          ) : null}
          {list.length === 0 ? <p className="lede">Nothing to check. Every merchant has a settled category.</p> : (
            <ol className="merchant-review">
              {list.slice(0, 50).map((m, i) => (
                <MerchantCard key={m.id} m={m} categories={names} busy={savingId === m.id}
                              headingRef={(el) => { headings.current[i] = el; }}
                              onPick={(category) => decide.mutate({ m, category, index: i })} />
              ))}
            </ol>
          )}
          {list.length > 50 ? <p className="muted">Showing the 50 with the most money; the rest appear as you go.</p> : null}
        </PageLayout>
      )}
    </Loaded>
  );
}

function MerchantCard({ m, categories, busy, onPick, headingRef }: {
  m: MerchantToReview; categories: string[]; busy: boolean;
  onPick: (category: string) => void; headingRef: (el: HTMLHeadingElement | null) => void;
}) {
  const [other, setOther] = useState("");
  const headingId = `merchant-${m.id}`;
  return (
    <li className="merchant-card" aria-labelledby={headingId} aria-busy={busy}>
      <div className="merchant-head">
        <h3 className="merchant-name" id={headingId} tabIndex={-1} ref={headingRef}>{m.merchant}</h3>
        <span className="muted">
          {m.direction === "in" ? "money in" : "money out"} · {m.rows} row{m.rows === 1 ? "" : "s"} · <Money value={m.total} />
          {m.first ? <> · {displayDate(m.first)}{m.last !== m.first ? <>–{displayDate(m.last!)}</> : null}</> : null}
        </span>
      </div>
      <ul className="merchant-examples" aria-label="How the bank printed it">
        {m.examples.map((e) => <li key={e}><code>{e}</code></li>)}
      </ul>
      {m.note ? <p className="muted merchant-note">{m.note}</p> : null}
      <div className="merchant-choices" role="group" aria-label={`File ${m.merchant} as`}>
        <button type="button" className="current" disabled={busy} onClick={() => onPick(m.category)}>
          {busy ? "Filing…" : m.category} <span className="muted">filed now</span>
        </button>
        {m.alternative && m.alternative !== m.category ? (
          <button type="button" disabled={busy} onClick={() => onPick(m.alternative!)}>
            {m.alternative} <span className="muted">alternative</span>
          </button>
        ) : null}
        <span className="other">
          <select value={other} aria-label={`Another category for ${m.merchant}`} disabled={busy}
                  onChange={(e) => setOther(e.target.value)}>
            <option value="">Something else…</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <button type="button" disabled={busy || !other} onClick={() => onPick(other)}>File</button>
        </span>
      </div>
    </li>
  );
}
