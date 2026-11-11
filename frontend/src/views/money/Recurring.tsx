import { Loaded, Money, PageHead, Section, Figure, Figures } from "../../components/ui";
import { displayDate } from "../../lib/format";
import { useSchedules } from "../../lib/queries";

export function Recurring() {
  const q = useSchedules();
  return (
    <>
      <PageHead title="Recurring" lede="Regular shared costs, what's been expected so far, and what has been paid against them." />
      <Loaded query={q}>
        {(data) => (
          <>
            <Figures>
              <Figure label="Expected to date"><Money value={data.total_expected} /></Figure>
              <Figure label="Paid"><Money value={data.total_paid} /></Figure>
              <Figure label="Still owing"><Money value={data.total_owing} /></Figure>
            </Figures>
            {!data.schedules.length && (
              <p className="hint">No recurring schedules configured. Add them to config/schedules.yaml.</p>
            )}
            {data.schedules.map((s) => (
              <Section key={s.name} title={s.name} aside={s.counterparty ?? undefined}>
                {s.settle_enabled && s.payments.length === 0 && s.balance_owing > 0 && (
                  <div className="notice">
                    No payments have matched this schedule since {displayDate(s.start)}. If payments have been made,
                    tag them with the schedule's matching tag in Transactions so they count here.
                  </div>
                )}
                <div className="grid-2">
                  <table className="ledger">
                    <tbody>
                      <tr><td>{s.frequency.charAt(0).toUpperCase() + s.frequency.slice(1)} amount</td><td className="num"><Money value={s.amount} /></td></tr>
                      <tr><td>Their share ({s.their_pct}%)</td><td className="num"><Money value={s.their_share} /></td></tr>
                      <tr><td>Due since {displayDate(s.start)}</td><td className="num">{s.num_due}</td></tr>
                      <tr><td>Expected to date</td><td className="num"><Money value={s.expected_to_date} /></td></tr>
                      <tr><td>Paid</td><td className="num"><Money value={s.paid} /></td></tr>
                      <tr className="total"><td>Still owing</td><td className="num"><Money value={s.balance_owing} /></td></tr>
                      <tr><td>Next due</td><td className="num">{displayDate(s.next_due)}</td></tr>
                    </tbody>
                  </table>
                  <div>
                    <p className="hint" style={{ marginTop: 0 }}>
                      {s.settle_enabled
                        ? `${s.payments.length} payment${s.payments.length === 1 ? "" : "s"} matched`
                        : "Tracked by hand; no payment matching configured"}
                      {s.notes ? `. ${s.notes}` : ""}
                    </p>
                    {s.payments.length > 0 && (
                      <table className="ledger">
                        <thead><tr><th>Paid</th><th>Description</th><th className="num">Amount</th></tr></thead>
                        <tbody>
                          {s.payments.map((p, i) => (
                            <tr key={i}><td>{displayDate(p.date)}</td><td>{p.description}</td><td className="num"><Money value={p.amount} /></td></tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
                <details style={{ marginTop: 12 }}>
                  <summary className="muted">All {s.occurrences.length} due dates</summary>
                  <table className="ledger" style={{ maxWidth: 520 }}>
                    <thead><tr><th>Due</th><th className="num">Full amount</th><th className="num">Their share</th></tr></thead>
                    <tbody>
                      {s.occurrences.map((o) => (
                        <tr key={o.date}><td>{displayDate(o.date)}</td><td className="num muted"><Money value={o.amount} tone="plain" /></td>
                          <td className="num"><Money value={o.their_share} /></td></tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              </Section>
            ))}
          </>
        )}
      </Loaded>
    </>
  );
}
