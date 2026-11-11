import { useState } from "react";
import type { ATOLabelRow } from "../../api";
import { CopyValue, Figure, Figures, Loaded, Masked, Money, PageHead, Section } from "../../components/ui";
import { money } from "../../lib/format";
import { useLodged } from "../../lib/queries";

function LabelRows({ rows }: { rows: ATOLabelRow[] }) {
  return (
    <>
      {rows.map((r, i) => (
        <tr key={i}>
          <td className="code">{r.code ?? r.label ?? ""}</td>
          <td>{r.desc}</td>
          <td className="num">
            <CopyValue copy={String(r.value)}>
              {typeof r.value === "number" ? <Money value={r.value} tone="plain" /> : r.value}
            </CopyValue>
          </td>
        </tr>
      ))}
    </>
  );
}

export function Lodgement() {
  const q = useLodged();
  const [tpId, setTpId] = useState<string>("");
  const [fySel, setFySel] = useState<number | null>(null);
  return (
    <Loaded query={q}>
      {(data) => {
        const tp = data.taxpayers.find((t) => t.id === tpId) ?? data.taxpayers[0];
        if (!tp || !tp.lodged.length) {
          return (
            <>
              <PageHead title="Lodged returns" />
              <p className="hint">No lodged returns on file. Add them to config/ato_returns.yaml (see the .example template).</p>
            </>
          );
        }
        const year = tp.lodged.find((y) => y.fy === fySel) ?? tp.lodged[0];
        const ref = tp.reference || {};
        const cf = tp.latest_carry_forward || [];
        return (
          <>
            <PageHead title="Lodged returns" lede="What was lodged, label by label. Click a value to copy it for myTax.">
              {data.taxpayers.length > 1 && (
                <select value={tp.id} onChange={(e) => { setTpId(e.target.value); setFySel(null); }} aria-label="Taxpayer">
                  {data.taxpayers.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              )}
              <select value={year.fy} onChange={(e) => setFySel(Number(e.target.value))} aria-label="Year lodged">
                {tp.lodged.map((y) => <option key={y.fy} value={y.fy}>{y.fy_label}</option>)}
              </select>
            </PageHead>

            {cf.length > 0 && (
              <Section title="Carry forward into your next return" aside={`From ${cf[0].from_fy_label}`}>
                <table className="ledger"><tbody><LabelRows rows={cf} /></tbody></table>
              </Section>
            )}

            <Section title="Details that don't change">
              <table className="ledger" style={{ maxWidth: 720 }}>
                <tbody>
                  {ref.tfn && <tr><td>Tax file number</td><td className="num"><Masked value={ref.tfn} copy={ref.tfn.replace(/\s/g, "")} /></td></tr>}
                  {ref.abn && <tr><td>ABN{ref.business_name ? ` (${ref.business_name})` : ""}</td><td className="num"><CopyValue copy={ref.abn}>{ref.abn}</CopyValue></td></tr>}
                  {ref.occupation && <tr><td>Occupation</td><td className="num">{ref.occupation}</td></tr>}
                  {ref.health_insurer?.id && (
                    <tr><td>Private health</td><td className="num">{ref.health_insurer.id}{ref.health_insurer.membership ? <>, membership <Masked value={ref.health_insurer.membership} /></> : null}</td></tr>
                  )}
                  {ref.spouse?.name && (
                    <tr><td>Spouse</td><td className="num">{ref.spouse.name}{ref.spouse.date_of_birth ? <>, born <Masked value={ref.spouse.date_of_birth} /></> : null}</td></tr>
                  )}
                </tbody>
              </table>
            </Section>

            <Section title={`${year.fy_label}, as lodged`} aside={year.receipt ? `ATO receipt ${year.receipt}` : undefined}>
              <Figures>
                <Figure label="Taxable income"><Money value={year.taxable_income} /></Figure>
                <Figure label="Tax withheld"><Money value={year.tax_withheld} /></Figure>
              </Figures>
            </Section>

            {year.sections.map((sec) => (
              <Section key={sec.name} title={sec.name}>
                <table className="ledger"><thead><tr><th>Label</th><th>Item</th><th className="num">Value</th></tr></thead>
                  <tbody><LabelRows rows={sec.rows} /></tbody></table>
              </Section>
            ))}
            <p className="hint">Values copy as plain numbers ({money(1234.5).replace("$", "")} copies as 1234.5).</p>
          </>
        );
      }}
    </Loaded>
  );
}
