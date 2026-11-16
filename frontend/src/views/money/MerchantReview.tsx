import { useState } from "react";
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
  const decide = useMutation({
    mutationFn: ({ id, category }: { id: number; category: string }) => api.decideMerchant(id, category),
    onSuccess: () => {
      setDone((n) => n + 1);
      for (const key of ["merchants-review", "transactions", "period", "budget", "trends", "year-review", "spreadsheet"])
        qc.invalidateQueries({ queryKey: [key] });
    },
  });
  const names = (cats.data ?? []).map((c) => c.name).filter((n) => n !== "Uncategorized");

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
          {list.length === 0 ? <p className="lede">Nothing to check. Every merchant has a settled category.</p> : (
            <ol className="merchant-review">
              {list.slice(0, 50).map((m) => (
                <MerchantCard key={m.id} m={m} categories={names} busy={decide.isPending}
                              onPick={(category) => decide.mutate({ id: m.id, category })} />
              ))}
            </ol>
          )}
          {list.length > 50 ? <p className="muted">Showing the 50 with the most money; the rest appear as you go.</p> : null}
        </PageLayout>
      )}
    </Loaded>
  );
}

function MerchantCard({ m, categories, busy, onPick }: {
  m: MerchantToReview; categories: string[]; busy: boolean; onPick: (category: string) => void;
}) {
  return (
    <li className="merchant-card">
      <div className="merchant-head">
        <strong>{m.merchant}</strong>
        <span className="muted">
          {m.direction === "in" ? "money in" : "money out"} · {m.rows} row{m.rows === 1 ? "" : "s"} · <Money value={m.total} />
          {m.first ? <> · {displayDate(m.first)}{m.last !== m.first ? <>–{displayDate(m.last!)}</> : null}</> : null}
        </span>
      </div>
      <ul className="merchant-examples">
        {m.examples.map((e) => <li key={e}><code>{e}</code></li>)}
      </ul>
      {m.note ? <p className="muted merchant-note">{m.note}</p> : null}
      <div className="merchant-choices">
        <button type="button" disabled={busy} onClick={() => onPick(m.category)}>
          {m.category} <span className="muted">filed now</span>
        </button>
        {m.alternative && m.alternative !== m.category ? (
          <button type="button" disabled={busy} onClick={() => onPick(m.alternative!)}>
            {m.alternative} <span className="muted">alternative</span>
          </button>
        ) : null}
        <select value="" aria-label={`Another category for ${m.merchant}`} disabled={busy}
                onChange={(e) => e.target.value && onPick(e.target.value)}>
          <option value="">Something else…</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
    </li>
  );
}
