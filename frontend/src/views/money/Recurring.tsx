import { useState } from "react";
import type { RecurringSchedule } from "../../api";
import { MonthGrid } from "../../components/charts";
import { PageLayout, StatusTag, Summary } from "../../components/layout";
import { Loaded, Money, Section, Segmented } from "../../components/ui";
import { displayDate, isoDate, money, monthLong } from "../../lib/format";
import { useSchedules } from "../../lib/queries";

/** What a schedule expected, and what was paid against it, within one month. */
function inMonth(s: RecurringSchedule, month: string) {
  const due = s.occurrences.filter((o) => o.date.startsWith(month));
  const paid = s.payments.filter((p) => p.date.startsWith(month));
  const expected = due.reduce((t, o) => t + o.their_share, 0);
  const received = paid.reduce((t, p) => t + p.amount, 0);
  // Occurrences only run to today, so add the rest of this month's.
  const upcoming: string[] = [];
  if (month === isoDate(new Date()).slice(0, 7)) {
    const step = s.frequency === "weekly" ? 7 : s.frequency === "fortnightly" ? 14 : 0;
    let d = new Date(`${s.next_due}T00:00:00`);
    while (isoDate(d).startsWith(month)) {
      upcoming.push(isoDate(d));
      if (!step) break;
      d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + step);
    }
  }
  return { due, paid, expected, received, upcoming, upcomingAmount: upcoming.length * s.their_share };
}

export function Recurring() {
  const q = useSchedules();
  const [month, setMonth] = useState(isoDate(new Date()).slice(0, 7));
  const [view, setView] = useState<"list" | "calendar">("list");

  return (
    <Loaded query={q}>
      {(data) => (
        <PageLayout
          title="Recurring"
          lede="Regular shared costs: what's been expected, what's been paid against them, and what's still to come."
          controls={
            <>
              <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month" />
              <Segmented label="View" value={view} onChange={setView}
                         options={[{ value: "list", label: "List" }, { value: "calendar", label: "Calendar" }]} />
            </>
          }
          aside={
            <Summary title="Since each schedule began" rows={[
              { label: "Expected to date", value: <Money value={data.total_expected} tone="plain" /> },
              { label: "Paid", value: <Money value={data.total_paid} tone="plain" /> },
              { label: data.total_owing >= 0 ? "Still owing" : "Paid ahead", value: <Money value={data.total_owing} />, strong: true },
            ]} />
          }
        >
          {!data.schedules.length && (
            <p className="hint">No recurring schedules configured. Add them to config/schedules.yaml.</p>
          )}
          {data.schedules.map((s) => {
            const m = inMonth(s, month);
            const short = m.received + 0.005 < m.expected;
            return (
              <Section key={s.name} title={s.name}
                       aside={`${s.counterparty ? `${s.counterparty} · ` : ""}${s.their_pct}% of ${money(s.amount)} ${s.frequency}`}>
                <div className="month-strip">
                  <div><span className="figure-label">Due in {monthLong(month)}</span>
                    <strong><Money value={m.expected} tone="plain" /></strong>
                    <span className="muted">{m.due.length} × {money(s.their_share)}</span></div>
                  <div><span className="figure-label">Paid in {monthLong(month)}</span>
                    <strong><Money value={m.received} tone="plain" /></strong>
                    <span className="muted">{m.paid.length} payment{m.paid.length === 1 ? "" : "s"}</span></div>
                  {m.upcoming.length > 0 && (
                    <div><span className="figure-label">Still to come this month</span>
                      <strong><Money value={m.upcomingAmount} tone="plain" /></strong>
                      <span className="muted">{m.upcoming.map((d) => displayDate(d).replace(/ \d{4}$/, "")).join(", ")}</span></div>
                  )}
                  <div className="strip-status">
                    {m.due.length === 0 && m.paid.length === 0 ? <StatusTag status="none" label="Nothing due" />
                      : short ? <StatusTag status="due" label={`${money(m.expected - m.received)} short`} />
                        : <StatusTag status="ok" label="Covered" />}
                  </div>
                </div>

                {view === "calendar" ? (
                  <MonthGrid month={month} marks={[
                    ...m.due.map((o) => ({ date: o.date, label: `Due ${money(o.their_share, { cents: false })}`, kind: "expected" as const })),
                    ...m.upcoming.map((d) => ({ date: d, label: `Due ${money(s.their_share, { cents: false })}`, kind: "expected" as const })),
                    ...m.paid.map((p) => ({ date: p.date, label: `Paid ${money(p.amount, { cents: false })}`, kind: "done" as const })),
                  ]} />
                ) : (
                  <table className="ledger">
                    <thead><tr><th>Date</th><th>What</th><th className="num">Amount</th></tr></thead>
                    <tbody>
                      {[
                        ...m.due.map((o) => ({ date: o.date, what: <StatusTag status="due" label="Due" />, amount: o.their_share })),
                        ...m.upcoming.map((d) => ({ date: d, what: <StatusTag status="due" label="Coming up" />, amount: s.their_share })),
                        ...m.paid.map((p) => ({ date: p.date, what: <span><StatusTag status="paid" /> <span className="muted">{p.description}</span></span>, amount: p.amount })),
                      ].sort((a, b) => a.date.localeCompare(b.date)).map((r, i) => (
                        <tr key={i}><td style={{ whiteSpace: "nowrap" }}>{displayDate(r.date)}</td><td>{r.what}</td>
                          <td className="num"><Money value={r.amount} tone="plain" /></td></tr>
                      ))}
                      {m.due.length + m.upcoming.length + m.paid.length === 0 && (
                        <tr><td colSpan={3} className="muted">Nothing due or paid in {monthLong(month)}.</td></tr>
                      )}
                    </tbody>
                  </table>
                )}

                {s.settle_enabled && s.payments.length === 0 && s.balance_owing > 0 && (
                  <div className="notice">
                    No payments have matched this schedule since {displayDate(s.start)}. If payments have been made,
                    tag them with the schedule's matching tag in Transactions so they count here.
                  </div>
                )}
                <p className="hint">
                  Since {displayDate(s.start)}: {s.num_due} due, {money(s.expected_to_date)} expected,{" "}
                  {money(s.paid)} paid across {s.payments.length} payment{s.payments.length === 1 ? "" : "s"},{" "}
                  <strong className="money">
                    {s.balance_owing >= 0 ? `${money(s.balance_owing)} owing` : `${money(-s.balance_owing)} paid ahead`}
                  </strong>.
                  {s.notes ? ` ${s.notes}` : ""}
                </p>
              </Section>
            );
          })}
        </PageLayout>
      )}
    </Loaded>
  );
}
