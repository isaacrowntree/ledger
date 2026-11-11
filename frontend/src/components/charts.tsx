import { useMemo, useRef, useState, type ReactNode } from "react";
import { money, monthLong, monthShort } from "../lib/format";

/* Charts are hand-built SVG, sized by their container. Marks are thin, bars
   meet the baseline square and round only at the data end, the grid recedes,
   and every chart has a hover readout. Colours come from the validated series
   tokens in styles.css; text never wears a series colour. */

const W = 760;

/** A round tick step (1, 2 or 5 × a power of ten) giving about four gridlines. */
function niceStep(v: number): number {
  const raw = v / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) if (m * mag >= raw) return m * mag;
  return 10 * mag;
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const step = niceStep(v);
  return Math.ceil(v / step) * step;
}

function ticks(max: number): number[] {
  const step = niceStep(max);
  const out: number[] = [];
  for (let t = 0; t <= max + step / 1000; t += step) out.push(t);
  return out;
}

function axisMoney(v: number): string {
  if (v >= 1000) return `$${Math.round(v / 1000)}k`;
  return `$${Math.round(v)}`;
}

export interface Series {
  key: string;
  label: string;
  color: string;
}

/** Bar with a 4px rounded data-end and a square base on the baseline. */
function barPath(x: number, y: number, w: number, h: number): string {
  if (h <= 0) return "";
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

/** Grouped monthly bars: one group per month, one bar per series. */
export function MonthlyBars({
  rows,
  series,
  height = 260,
}: {
  rows: ({ month: string } & Record<string, number | string>)[];
  series: Series[];
  height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const pad = { l: 44, r: 8, t: 8, b: 26 };
  const max = niceMax(Math.max(1, ...rows.flatMap((r) => series.map((s) => Number(r[s.key]) || 0))));
  const innerW = W - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const groupW = innerW / Math.max(rows.length, 1);
  const gap = 2; // surface gap between adjacent bars
  const barW = Math.max(3, Math.min(18, (groupW * 0.72 - gap * (series.length - 1)) / series.length));
  const y = (v: number) => pad.t + innerH - (v / max) * innerH;

  return (
    <div>
      <div className="legend" style={{ justifyContent: "space-between" }}>
        <span style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          {series.map((s) => (
            <span key={s.key}>
              <i style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </span>
        <button type="button" className="quiet" onClick={() => setAsTable((t) => !t)}>
          {asTable ? "Show chart" : "Show as table"}
        </button>
      </div>
      {asTable ? (
        <div className="table-wrap">
          <table className="ledger">
            <thead>
              <tr>
                <th>Month</th>
                {series.map((s) => (
                  <th key={s.key} className="num">{s.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.month}>
                  <td>{monthLong(r.month)}</td>
                  {series.map((s) => (
                    <td key={s.key} className="num">{money(Number(r[s.key]) || 0)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="chart-box" ref={box} onMouseLeave={() => setHover(null)}>
          <svg className="chart" viewBox={`0 0 ${W} ${height}`} role="img"
               aria-label={`${series.map((s) => s.label).join(", ")} by month`}>
            <g className="grid">
              {ticks(max).map((t) => (
                <g key={t}>
                  <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} />
                  <text x={pad.l - 6} y={y(t) + 4} textAnchor="end">{axisMoney(t)}</text>
                </g>
              ))}
            </g>
            {rows.map((r, i) => {
              const gx = pad.l + i * groupW;
              const total = series.length * barW + (series.length - 1) * gap;
              const x0 = gx + (groupW - total) / 2;
              return (
                <g key={r.month}>
                  {hover === i && <rect x={gx} y={pad.t} width={groupW} height={innerH} fill="var(--wash)" />}
                  {series.map((s, j) => {
                    const v = Number(r[s.key]) || 0;
                    return <path key={s.key} d={barPath(x0 + j * (barW + gap), y(v), barW, y(0) - y(v))} fill={s.color} />;
                  })}
                  <text x={gx + groupW / 2} y={height - 8} textAnchor="middle">
                    {rows.length > 14 && i % 2 ? "" : monthShort(r.month)}
                    {r.month.endsWith("-01") || i === 0 ? ` ${r.month.slice(2, 4)}` : ""}
                  </text>
                  {/* hit target wider than the marks */}
                  <rect x={gx} y={pad.t} width={groupW} height={innerH} fill="transparent"
                        onMouseEnter={() => setHover(i)} />
                </g>
              );
            })}
            <line className="baseline" x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} />
          </svg>
          {hover !== null && rows[hover] && (
            <div className="tooltip" style={{
              left: `${((pad.l + (hover + 0.5) * groupW) / W) * 100}%`,
              top: 0,
              transform: hover > rows.length / 2 ? "translateX(-105%)" : "translateX(8px)",
            }}>
              <div style={{ fontWeight: 600 }}>{monthLong(rows[hover].month)}</div>
              {series.map((s) => (
                <div key={s.key}>
                  <i style={{ display: "inline-block", width: 8, height: 8, background: s.color, marginRight: 6, borderRadius: 2 }} />
                  {s.label}: {money(Number(rows[hover][s.key]) || 0)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Ranked horizontal bars for one measure (spending by category). */
export function RankedBars({
  items,
  limit = 10,
  kind = "spending",
  footer,
}: {
  items: { label: string; value: number; note?: string; other?: boolean; color?: string }[];
  limit?: number;
  kind?: "spending" | "income";
  footer?: ReactNode;
}) {
  const shown = useMemo(() => {
    const sorted = [...items].sort((a, b) => b.value - a.value);
    if (sorted.length <= limit) return sorted;
    const head = sorted.slice(0, limit - 1);
    const rest = sorted.slice(limit - 1);
    return [...head, { label: `${rest.length} other categories`, value: rest.reduce((s, r) => s + r.value, 0), other: true }];
  }, [items, limit]);
  const max = Math.max(1, ...shown.map((s) => s.value));
  return (
    <div>
      <div className="ranked">
        {shown.map((s) => (
          <RankedRow key={s.label} {...s} max={max} kind={kind} />
        ))}
      </div>
      {footer}
    </div>
  );
}

function RankedRow({ label, value, note, max, kind, other, color }: {
  label: string; value: number; note?: string; max: number; kind: string; other?: boolean; color?: string;
}) {
  // The folded "other" row is a sum, not a category: neutral, so it never reads as the biggest one.
  return (
    <>
      <span title={note} className={`cat-cell ${other ? "muted" : ""}`}>
        {color && !other && <i className="cat-dot" style={{ background: color }} />}
        {label}
      </span>
      <span>
        <div className={`bar ${kind === "income" ? "income" : ""}`}
             style={{ width: `${(Math.max(value, 0) / max) * 100}%`,
                      ...(other ? { background: "var(--series-neutral)" } : color ? { background: color } : {}) }}
             title={`${label}: ${money(value)}`} />
      </span>
      <span className="val">{money(value, { cents: false })}</span>
    </>
  );
}

/** One small bar chart per category, all on the same scale so they compare. */
export function SmallMultiples({
  groups,
  months,
}: {
  groups: { label: string; values: Record<string, number>; total: number; color?: string }[];
  months: string[];
}) {
  const max = niceMax(Math.max(1, ...groups.flatMap((g) => months.map((m) => g.values[m] || 0))));
  const h = 70;
  const w = 240;
  const bw = w / Math.max(months.length, 1);
  return (
    <div className="multiples">
      {groups.map((g) => (
        <div className="multiple" key={g.label}>
          <h3>
            <span className="cat-cell">{g.color && <i className="cat-dot" style={{ background: g.color }} />}{g.label}</span>
            <span className="muted">{money(g.total, { cents: false })}</span>
          </h3>
          <svg className="chart" viewBox={`0 0 ${w} ${h + 14}`} role="img" aria-label={`${g.label} by month`}>
            {months.map((m, i) => {
              const v = g.values[m] || 0;
              const bh = (v / max) * h;
              return (
                <g key={m}>
                  <title>{`${monthLong(m)}: ${money(v)}`}</title>
                  <path d={barPath(i * bw + 1, h - bh, Math.max(bw - 2, 1), bh)} fill={g.color ?? "var(--series-spending)"} />
                  <rect x={i * bw} y={0} width={bw} height={h} fill="transparent" />
                </g>
              );
            })}
            <line className="baseline" x1={0} x2={w} y1={h} y2={h} />
            <text x={0} y={h + 12}>{monthShort(months[0] ?? "")} {months[0]?.slice(2, 4)}</text>
            <text x={w} y={h + 12} textAnchor="end">
              {monthShort(months[months.length - 1] ?? "")} {months[months.length - 1]?.slice(2, 4)}
            </text>
          </svg>
        </div>
      ))}
    </div>
  );
}

/** A single-series line with a hover crosshair (CPI index over time). */
export function LineChart({
  points,
  label,
  format,
  height = 220,
}: {
  points: { x: string; y: number }[];
  label: string;
  format: (v: number) => string;
  height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 44, r: 10, t: 10, b: 24 };
  if (points.length < 2) return null;
  const ys = points.map((p) => p.y);
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const span = hi - lo || 1;
  const min = lo - span * 0.05;
  const max = hi + span * 0.05;
  const innerW = W - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const x = (i: number) => pad.l + (i / (points.length - 1)) * innerW;
  const y = (v: number) => pad.t + innerH - ((v - min) / (max - min)) * innerH;
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.y)}`).join(" ");
  return (
    <div className="chart-box" onMouseLeave={() => setHover(null)}>
      <svg className="chart" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={label}
           onMouseMove={(e) => {
             const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
             const px = ((e.clientX - rect.left) / rect.width) * W;
             setHover(Math.max(0, Math.min(points.length - 1, Math.round(((px - pad.l) / innerW) * (points.length - 1)))));
           }}>
        <g className="grid">
          {[0, 0.5, 1].map((t) => {
            const v = min + (max - min) * t;
            return (
              <g key={t}>
                <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} />
                <text x={pad.l - 6} y={y(v) + 4} textAnchor="end">{format(v)}</text>
              </g>
            );
          })}
        </g>
        <path d={d} fill="none" stroke="var(--series-income)" strokeWidth={2} />
        <text x={pad.l} y={height - 6}>{points[0].x}</text>
        <text x={W - pad.r} y={height - 6} textAnchor="end">{points[points.length - 1].x}</text>
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={pad.t + innerH} stroke="var(--rule-strong)" />
            <circle cx={x(hover)} cy={y(points[hover].y)} r={4} fill="var(--series-income)" stroke="var(--surface)" strokeWidth={2} />
          </g>
        )}
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%`, top: 0,
          transform: hover > points.length / 2 ? "translateX(-105%)" : "translateX(8px)" }}>
          {points[hover].x}: {format(points[hover].y)}
        </div>
      )}
    </div>
  );
}

/** Gross → deductions → net, as horizontal bars on one scale. */
export function Waterfall({ steps }: { steps: { label: string; value: number; kind: "in" | "out" | "result" }[] }) {
  const max = Math.max(1, ...steps.map((s) => Math.abs(s.value)));
  return (
    <div className="ranked">
      {steps.map((s) => (
        <RankedWaterRow key={s.label} {...s} max={max} />
      ))}
    </div>
  );
}

function RankedWaterRow({ label, value, kind, max }: { label: string; value: number; kind: string; max: number }) {
  const color = kind === "out" ? "var(--series-spending)" : kind === "in" ? "var(--series-income)" : "var(--series-partner)";
  return (
    <>
      <span>{label}</span>
      <span><div className="bar" style={{ width: `${(Math.abs(value) / max) * 100}%`, background: color }} /></span>
      <span className="val">{money(value)}</span>
    </>
  );
}
