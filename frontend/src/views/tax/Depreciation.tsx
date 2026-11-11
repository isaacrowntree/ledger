import { useState } from "react";
import { CopyValue, Figure, Figures, Loaded, Money, PageHead, Section } from "../../components/ui";
import { displayDate, fyLabel, fyOf, money } from "../../lib/format";
import { useDepreciation } from "../../lib/queries";

// Copies carry the exact two-decimal figure shown, so a column of copies sums
// to the printed total.
const Copy = ({ n }: { n: number }) => <CopyValue copy={n.toFixed(2)}>{money(n)}</CopyValue>;

export function Depreciation() {
  const q = useDepreciation();
  const [fySel, setFySel] = useState<number | null>(null);
  return (
    <Loaded query={q}>
      {(data) => {
        const fys = [...new Set(data.registers.flatMap((r) => Object.keys(r.totals).map(Number)))].sort((a, b) => b - a);
        if (!data.registers.length || !fys.length) {
          return (
            <>
              <PageHead title="Depreciation" />
              <p className="hint">No depreciation register on file. Add assets to config/depreciation.yaml (see the .example template).</p>
            </>
          );
        }
        const fy = fySel ?? (fys.includes(fyOf() - 1) ? fyOf() - 1 : fys[0]);
        return (
          <>
            <PageHead title="Depreciation" lede="Each asset's written-down value rolled forward a year at a time. Click a figure to copy it.">
              <select value={fy} onChange={(e) => setFySel(Number(e.target.value))} aria-label="Financial year">
                {fys.map((y) => <option key={y} value={y}>{fyLabel(y)}</option>)}
              </select>
            </PageHead>
            {data.registers.map((reg) => {
              const t = reg.totals[String(fy)];
              const joint = reg.ownership_pct < 100;
              const share = reg.ownership_pct / 100;
              const claim = t ? (joint ? t.taxpayer_deductible : t.deductible) : 0;
              return (
                <Section key={reg.owner} title={reg.owner}
                         aside={[reg.kind, joint ? `${reg.ownership_pct}% owned` : null, reg.method_note].filter(Boolean).join(" · ")}>
                  <Figures>
                    <Figure label={joint ? `Your ${reg.ownership_pct}% to claim, ${fyLabel(fy)}` : `To claim, ${fyLabel(fy)}`}
                            note={joint && t ? `Whole property: ${money(t.deductible)}` : undefined}>
                      <Copy n={claim} />
                    </Figure>
                    <Figure label="Assets held">{t ? t.n_assets : 0}</Figure>
                  </Figures>
                  <div className="table-wrap" style={{ marginTop: 12 }}>
                    <table className="ledger">
                      <thead><tr><th>Asset</th><th className="num">Opening value</th><th className="num">Decline</th>
                        <th className="num">Deductible</th>{joint && <th className="num">Your {reg.ownership_pct}%</th>}
                        <th className="num">Closing value</th><th>Detail</th></tr></thead>
                      <tbody>
                        {reg.assets.map((a) => {
                          const y = a.years.find((yr) => yr.fy === fy);
                          if (!y) {
                            const sold = a.years.find((yr) => yr.disposed)?.disposed;
                            return <tr key={a.description}><td>{a.description}</td>
                              <td colSpan={joint ? 6 : 5} className="muted">
                                {sold && sold < `${fy}-07-01`
                                  ? `Sold ${displayDate(sold)}`
                                  : `Not held this year (acquired ${displayDate(a.acquired)})`}
                              </td></tr>;
                          }
                          return (
                            <tr key={a.description}>
                              <td>{a.description}</td>
                              <td className="num"><Money value={y.opening} tone="plain" /></td>
                              <td className="num"><Money value={-y.decline} /></td>
                              <td className="num">{joint ? <Money value={y.deductible} tone="plain" /> : <Copy n={y.deductible} />}</td>
                              {joint && <td className="num"><Copy n={y.deductible * share} /></td>}
                              <td className="num"><Money value={y.closing} tone="plain" /></td>
                              <td className="muted">{displayDate(a.acquired)} · {a.effective_life} years · {a.method}</td>
                            </tr>
                          );
                        })}
                        <tr className="total"><td>Total</td><td /><td />
                          <td className="num">{joint ? <Money value={t?.deductible ?? 0} tone="plain" /> : <Copy n={t?.deductible ?? 0} />}</td>
                          {joint && <td className="num"><Copy n={claim} /></td>}<td /><td /></tr>
                      </tbody>
                    </table>
                  </div>
                </Section>
              );
            })}
          </>
        );
      }}
    </Loaded>
  );
}
