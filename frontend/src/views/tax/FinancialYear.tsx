import { useState } from "react";
import type { RentalProperty, SpreadsheetIncoming, SpreadsheetOutgoing, WorkTripsResponse } from "../../api";
import { Figure, Figures, Loaded, Money, PageHead, Section, Segmented } from "../../components/ui";
import { displayDate, fyOf } from "../../lib/format";
import { useSpreadsheet } from "../../lib/queries";
import { FySelect } from "./AtoReturn";

type Tab = "outgoing" | "incoming" | "rental" | "work-trips";

export function FinancialYear() {
  const [fy, setFy] = useState(fyOf() - 1);
  const [tab, setTab] = useState<Tab>("outgoing");
  const q = useSpreadsheet(tab, String(fy));
  return (
    <>
      <PageHead title="Financial year detail" lede="Every line behind the return, the way an accountant asks for it.">
        <FySelect value={fy} onChange={setFy} />
      </PageHead>
      <div className="controls">
        <Segmented label="Show" value={tab} onChange={setTab} options={[
          { value: "outgoing", label: "Money out" }, { value: "incoming", label: "Money in" },
          { value: "rental", label: "Rental" }, { value: "work-trips", label: "Work trips & WFH" },
        ]} />
      </div>
      <Loaded query={q}>
        {(data) => {
          switch (tab) {
            case "outgoing": return <Outgoing rows={data as SpreadsheetOutgoing[]} />;
            case "incoming": return <Incoming rows={data as SpreadsheetIncoming[]} />;
            case "rental": return <Rental props={data as RentalProperty[]} />;
            case "work-trips": return <WorkTrips data={data as WorkTripsResponse} />;
          }
        }}
      </Loaded>
    </>
  );
}

function Outgoing({ rows }: { rows: SpreadsheetOutgoing[] }) {
  if (!Array.isArray(rows)) return null;
  const total = rows.reduce((s, t) => s + Math.abs(t.amount), 0);
  const biz = rows.reduce((s, t) => s + Math.abs(t.biz_amount), 0);
  return (
    <>
      <Figures>
        <Figure label="Transactions">{rows.length.toLocaleString("en-AU")}</Figure>
        <Figure label="Total out"><Money value={-total} /></Figure>
        <Figure label="Business share"><Money value={-biz} /></Figure>
      </Figures>
      <div className="table-wrap" style={{ marginTop: 16 }}>
        <table className="ledger">
          <thead><tr><th>Date</th><th>Account</th><th>Description</th><th>Category</th><th className="num">Amount</th>
            <th className="num">Business %</th><th className="num">Business $</th></tr></thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id}>
                <td style={{ whiteSpace: "nowrap" }}>{displayDate(t.date)}</td><td className="muted">{t.account_name}</td>
                <td>{t.description}</td><td className="muted">{t.category_name || "Uncategorized"}</td>
                <td className="num"><Money value={-Math.abs(t.amount)} /></td>
                <td className="num">{t.biz_pct > 0 ? `${t.biz_pct}%` : ""}</td>
                <td className="num">{t.biz_amount !== 0 ? <Money value={-Math.abs(t.biz_amount)} /> : ""}</td>
              </tr>
            ))}
            <tr className="total"><td colSpan={4}>Total</td><td className="num"><Money value={-total} /></td><td /><td className="num"><Money value={-biz} /></td></tr>
          </tbody>
        </table>
      </div>
    </>
  );
}

function Incoming({ rows }: { rows: SpreadsheetIncoming[] }) {
  if (!Array.isArray(rows)) return null;
  const total = rows.reduce((s, t) => s + t.amount, 0);
  const groups = new Map<string, { total: number; n: number }>();
  for (const t of rows) {
    const k = t.category_name || "Uncategorized";
    const g = groups.get(k) ?? { total: 0, n: 0 };
    groups.set(k, { total: g.total + t.amount, n: g.n + 1 });
  }
  return (
    <>
      <Section title="By category" aside={`${rows.length} transactions`}>
        <table className="ledger" style={{ maxWidth: 560 }}>
          <tbody>
            {[...groups.entries()].sort((a, b) => b[1].total - a[1].total).map(([k, g]) => (
              <tr key={k}><td>{k}</td><td className="num muted">{g.n}</td><td className="num"><Money value={g.total} /></td></tr>
            ))}
            <tr className="total"><td>Total</td><td className="num">{rows.length}</td><td className="num"><Money value={total} /></td></tr>
          </tbody>
        </table>
      </Section>
      <Section title="Every transaction">
        <div className="table-wrap">
          <table className="ledger">
            <thead><tr><th>Date</th><th>Account</th><th>Description</th><th>Category</th><th className="num">Amount</th></tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id}><td style={{ whiteSpace: "nowrap" }}>{displayDate(t.date)}</td><td className="muted">{t.account_name}</td>
                  <td>{t.description}</td><td className="muted">{t.category_name || "Uncategorized"}</td>
                  <td className="num"><Money value={t.amount} /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </>
  );
}

function Rental({ props }: { props: RentalProperty[] }) {
  if (!Array.isArray(props) || !props.length) return <p className="hint">No rental properties configured in tax.yaml.</p>;
  return (
    <>
      {props.map((p) => (
        <Section key={p.name} title={p.name} aside={`${p.ownership_pct}% owned · ${p.rental_weeks} weeks rented`}>
          <p className="hint" style={{ marginTop: 0 }}>{p.address}</p>
          <table className="ledger" style={{ maxWidth: 720 }}>
            <thead><tr><th>Item</th><th className="num">Full amount</th><th className="num">Your share</th></tr></thead>
            <tbody>
              <tr><td>Rental income</td><td className="num"><Money value={p.gross_income} /></td><td className="num"><Money value={p.income_share} /></td></tr>
              {p.expenses.map((e) => (
                <tr key={e.ato_label}><td>{e.ato_label}</td><td className="num"><Money value={-e.raw_amount} /></td><td className="num"><Money value={-e.share_amount} /></td></tr>
              ))}
              {p.depreciation.map((d) => (
                <tr key={d.description}><td>{d.description}</td><td /><td className="num"><Money value={-d.amount} /></td></tr>
              ))}
              <tr className="total"><td>Total expenses</td><td /><td className="num"><Money value={-p.total_expenses} /></td></tr>
              <tr className="total"><td>Net rent</td><td /><td className="num"><Money value={p.net_rent} /></td></tr>
            </tbody>
          </table>
        </Section>
      ))}
    </>
  );
}

function WorkTrips({ data }: { data: WorkTripsResponse }) {
  if (!data?.wfh) return null;
  return (
    <>
      {!data.trips.length && <p className="hint">No work trips recorded for this year.</p>}
      {data.trips.map((t) => (
        <Section key={t.id} title={t.name} aside={`${displayDate(t.start_date)} to ${displayDate(t.end_date)}`}>
          <table className="ledger" style={{ maxWidth: 720 }}>
            <thead><tr><th>Type</th><th className="num">Amount</th><th>Description</th></tr></thead>
            <tbody>
              {t.expenses.map((e, i) => (
                <tr key={i}><td>{e.expense_type}</td><td className="num"><Money value={-e.amount} /></td><td className="muted">{e.description}</td></tr>
              ))}
              <tr className="total"><td>Total</td><td className="num"><Money value={-t.total} /></td><td /></tr>
            </tbody>
          </table>
        </Section>
      ))}
      <Section title="Working from home" aside={`ATO fixed rate, $${data.wfh.rate_per_hour} an hour`}>
        <table className="ledger" style={{ maxWidth: 560 }}>
          <tbody>
            <tr><td>Weeks worked from home</td><td className="num">{data.wfh.weeks}</td></tr>
            <tr><td>Share of the week at home</td><td className="num">{data.wfh.allocation_pct}%</td></tr>
            <tr><td>Hours</td><td className="num">{data.wfh.hours.toFixed(1)}</td></tr>
            <tr className="total"><td>Deduction</td><td className="num"><Money value={-data.wfh.amount} /></td></tr>
          </tbody>
        </table>
      </Section>
    </>
  );
}
