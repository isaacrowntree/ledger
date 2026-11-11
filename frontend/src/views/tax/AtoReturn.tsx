import { useState } from "react";
import type { ATOReturn } from "../../api";
import { Figure, Figures, Loaded, Money, PageHead, Section } from "../../components/ui";
import { displayDate, fyLabel, fyOf, money } from "../../lib/format";
import { useAtoReturn } from "../../lib/queries";

export function FySelect({ value, onChange, back = 7 }: { value: number; onChange: (fy: number) => void; back?: number }) {
  const current = fyOf();
  return (
    <select value={value} onChange={(e) => onChange(Number(e.target.value))} aria-label="Financial year">
      {Array.from({ length: back + 1 }, (_, i) => current - i).map((fy) => (
        <option key={fy} value={fy}>{fyLabel(fy)}</option>
      ))}
    </select>
  );
}

export function AtoReturn() {
  // The most recent complete financial year is the one you lodge.
  const [fy, setFy] = useState(fyOf() - 1);
  const q = useAtoReturn(String(fy));
  return (
    <>
      <PageHead title="This year's return" lede="An estimate built from your transactions and config. Check every figure against your payment summary before lodging.">
        <FySelect value={fy} onChange={setFy} />
      </PageHead>
      <Loaded query={q}>{(data) => <ReturnBody data={data} />}</Loaded>
    </>
  );
}

function ReturnBody({ data }: { data: ATOReturn }) {
  const s = data.summary;
  const refund = s.refund_or_payable >= 0;
  const rentalNet = data.rental.reduce((t, r) => t + r.net_rent, 0);
  const bizNet = data.business.reduce((t, b) => t + b.net, 0);
  const withheldRate = data.income.salary > 0 ? (data.income.tax_withheld / data.income.salary) * 100 : 0;

  return (
    <>
      <p className="position" style={{ maxWidth: "40ch" }}>
        {refund ? "Estimated refund of " : "Estimated bill of "}
        <strong className={refund ? "money hold" : "money owe"}>{money(Math.abs(s.refund_or_payable))}</strong> for {data.fy_label}.
      </p>
      {withheldRate > 38 && (
        <div className="notice">
          Tax withheld is {withheldRate.toFixed(0)}% of salary ({money(data.income.tax_withheld)} of {money(data.income.salary)}),
          which is high for this income. Check it against your PAYG payment summary; a wrong withheld figure is the
          most common reason an estimated refund turns out wrong.
        </div>
      )}
      <Figures>
        <Figure label="Taxable income"><Money value={s.taxable_income} /></Figure>
        <Figure label="Tax payable"><Money value={s.total_tax} /></Figure>
        <Figure label="Withheld by employer"><Money value={s.tax_withheld} /></Figure>
        <Figure label="Effective rate">{s.effective_rate.toFixed(1)}%</Figure>
      </Figures>

      <Section title="How the estimate is worked out">
        <table className="ledger" style={{ maxWidth: 720 }}>
          <tbody>
            <tr><td>Assessable income <span className="muted">(salary, interest, net rent or business if a profit)</span></td><td className="num"><Money value={s.assessable_income} /></td></tr>
            <tr><td>Less deductions <span className="muted">(rental or business loss, WFH, work trips)</span></td><td className="num"><Money value={-s.total_deductions} /></td></tr>
            <tr className="total"><td>Taxable income</td><td className="num"><Money value={s.taxable_income} /></td></tr>
            <tr><td>Income tax</td><td className="num"><Money value={s.payg} /></td></tr>
            <tr><td>Medicare levy (2%)</td><td className="num"><Money value={s.medicare} /></td></tr>
            <tr className="total"><td>Total tax</td><td className="num"><Money value={s.total_tax} /></td></tr>
            <tr><td>Less tax already withheld</td><td className="num"><Money value={-s.tax_withheld} /></td></tr>
            <tr className="total"><td>{refund ? "Refund" : "Amount owing"}</td><td className="num"><Money value={s.refund_or_payable} tone="hold" /></td></tr>
          </tbody>
        </table>
      </Section>

      <Section title="Item 1 · Salary or wages">
        <Lines rows={[["Gross salary", data.income.salary], ["Tax withheld", data.income.tax_withheld]]} />
      </Section>
      <Section title="Item 10 · Interest">
        <Lines rows={[["Interest income", data.income.interest]]} />
      </Section>

      <CapitalGains cg={data.capital_gains} />

      {data.rental.map((r) => (
        <Section key={r.property} title={`Item 21 · Rent, ${r.property}`}
                 aside={`${r.ownership_pct}% owned · ${r.floor_area_pct}% floor area · ${r.rental_weeks} weeks rented`}>
          <p className="hint" style={{ marginTop: 0 }}>{r.address}</p>
          <div className="table-wrap">
            <table className="ledger">
              <thead><tr><th>Line</th><th className="num">Full amount</th><th>Applied</th><th className="num">Factor</th><th className="num">Your share</th></tr></thead>
              <tbody>
                <tr><td>Gross rental income</td><td className="num"><Money value={r.gross_income} /></td><td className="muted">ownership</td>
                  <td className="num">{(r.ownership_pct / 100).toFixed(2)}</td><td className="num"><Money value={r.income_share} /></td></tr>
                {r.expenses.map((e) => (
                  <tr key={e.ato_label}>
                    <td>{e.ato_label}</td><td className="num"><Money value={e.raw} tone="plain" /></td>
                    <td className="muted">{e.apply.length ? e.apply.join(" + ") : "in full"}</td>
                    <td className="num">{e.factor.toFixed(4)}</td><td className="num"><Money value={-e.share} /></td>
                  </tr>
                ))}
                {r.depreciation > 0 && (
                  <tr><td>Capital allowances (depreciation)</td><td className="num"><Money value={r.depreciation} tone="plain" /></td>
                    <td className="muted">depreciation</td><td className="num">1.0000</td><td className="num"><Money value={-r.depreciation} /></td></tr>
                )}
                <tr className="total"><td colSpan={4}>Net rent</td><td className="num"><Money value={r.net_rent} /></td></tr>
              </tbody>
            </table>
          </div>
        </Section>
      ))}

      {data.business.map((b) => (
        <Section key={b.name} title={`Business · ${b.name}`} aside={`ABN ${b.abn}`}>
          <table className="ledger" style={{ maxWidth: 560 }}>
            <tbody>
              <tr><td>Business income</td><td className="num"><Money value={b.income} /></td></tr>
              <tr><td>Expenses</td><td className="num"><Money value={-Math.abs(b.expenses)} /></td></tr>
              {b.depreciation > 0 && <tr><td>Depreciation</td><td className="num"><Money value={-b.depreciation} /></td></tr>}
              <tr className="total"><td>Net business {b.net >= 0 ? "income" : "loss"}</td><td className="num"><Money value={b.net} /></td></tr>
            </tbody>
          </table>
        </Section>
      ))}

      <Section title="D5 · Other work-related deductions">
        {data.deductions.work_trips.map((t) => (
          <div key={t.name} style={{ marginBottom: 12 }}>
            <h3>{t.name} <span className="muted" style={{ fontWeight: 400 }}>{displayDate(t.start_date)} to {displayDate(t.end_date)}</span></h3>
            <Lines rows={[...Object.entries(t.expenses).map(([k, v]) => [k, -v] as [string, number]), ["Trip total", -t.total]]} total />
          </div>
        ))}
        <h3>Working from home</h3>
        <Lines rows={[[`${data.deductions.wfh.weeks} weeks at ${data.deductions.wfh.allocation_pct}%`, -data.deductions.wfh.amount]]} />
      </Section>

      {data.manual_entries.length > 0 && (
        <Section title="Manual entries">
          <p className="hint" style={{ marginTop: 0 }}>
            Figures from tax.yaml that no bank transaction shows. Only income, deductions and rental entries are counted;
            anything else is shown for reference.
          </p>
          <table className="ledger">
            <thead><tr><th>Item</th><th>Counted as</th><th className="num">Amount</th><th>Notes</th></tr></thead>
            <tbody>
              {data.manual_entries.map((e, i) => {
                const counted = ["income", "deductions", "rental"].includes((e.section || "").trim().toLowerCase());
                return (
                  <tr key={i}><td>{e.label}</td><td className={counted ? "" : "muted"}>{counted ? e.section : "reference only"}</td>
                    <td className="num"><Money value={e.amount} /></td><td className="muted">{e.notes}</td></tr>
                );
              })}
              <tr className="total"><td colSpan={2}>Added to income</td><td className="num"><Money value={data.manual_entry_totals.income} /></td><td /></tr>
              <tr className="total"><td colSpan={2}>Added to deductions</td><td className="num"><Money value={data.manual_entry_totals.deductions} /></td><td /></tr>
              <tr className="total"><td colSpan={2}>Applied to net rent</td><td className="num"><Money value={data.manual_entry_totals.rental} /></td><td /></tr>
            </tbody>
          </table>
        </Section>
      )}

      {data.spouse?.name && (
        <Section title="Spouse">
          <Lines rows={[["Taxable income", data.spouse.taxable_income]]} />
        </Section>
      )}

      {(rentalNet !== 0 || bizNet !== 0) && (
        <p className="hint">Net rent {money(rentalNet)} · business {money(bizNet)}.</p>
      )}
    </>
  );
}

function Lines({ rows, total }: { rows: [string, number][]; total?: boolean }) {
  return (
    <table className="ledger" style={{ maxWidth: 560 }}>
      <tbody>
        {rows.map(([label, v], i) => (
          <tr key={label} className={total && i === rows.length - 1 ? "total" : ""}>
            <td>{label}</td><td className="num"><Money value={v} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// Each parcel is shown: the discount depends on its holding period, which a
// single total gives no way to check.
function CapitalGains({ cg }: { cg: ATOReturn["capital_gains"] }) {
  if (cg.error) {
    return (
      <Section title="Item 18 · Capital gains">
        <div className="error">Could not be calculated: {cg.error}</div>
      </Section>
    );
  }
  if (!cg.events.length) return null;
  return (
    <Section title="Item 18 · Capital gains">
      <p className="hint" style={{ marginTop: 0 }}>
        Parcels matched first in, first out from CommSec contract notes. The 50% discount applies to a parcel held more than 12 months.
      </p>
      <div className="table-wrap">
        <table className="ledger">
          <thead><tr><th>Code</th><th className="num">Units</th><th>Acquired</th><th>Disposed</th><th className="num">Cost base</th>
            <th className="num">Proceeds</th><th className="num">Gain</th><th>Discount</th></tr></thead>
          <tbody>
            {cg.events.map((e, i) => (
              <tr key={i}><td>{e.code}</td><td className="num">{e.units}</td><td>{displayDate(e.acquired)}</td><td>{displayDate(e.disposed)}</td>
                <td className="num"><Money value={e.cost_base} tone="plain" /></td><td className="num"><Money value={e.proceeds} tone="plain" /></td>
                <td className="num"><Money value={e.gain} /></td><td>{e.discountable ? "50%" : "none"}</td></tr>
            ))}
            <tr><td colSpan={6}>Gross capital gains</td><td className="num"><Money value={cg.gross_gains} /></td><td /></tr>
            {cg.losses > 0 && <tr><td colSpan={6}>Capital losses applied</td><td className="num"><Money value={-cg.losses_applied} /></td><td /></tr>}
            <tr><td colSpan={6}>Less 50% discount</td><td className="num"><Money value={-cg.discount} /></td><td /></tr>
            <tr className="total"><td colSpan={6}>Net capital gain</td><td className="num"><Money value={cg.net_capital_gain} /></td><td /></tr>
            {cg.losses_carried_forward > 0 && (
              <tr><td colSpan={6}>Net capital losses carried forward (18V)</td><td className="num"><Money value={cg.losses_carried_forward} /></td><td /></tr>
            )}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
