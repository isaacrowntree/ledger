import { fyOf, fyRange, isoDate } from "./format";

export type PeriodKey = "all" | "this-month" | "last-month" | "last-12" | "this-fy" | "last-fy" | "this-year" | "last-year";

export const PERIODS: { key: PeriodKey; label: string }[] = [
  { key: "all", label: "All time" },
  { key: "this-month", label: "This month" },
  { key: "last-month", label: "Last month" },
  { key: "last-12", label: "Last 12 months" },
  { key: "this-fy", label: "This financial year" },
  { key: "last-fy", label: "Last financial year" },
  { key: "this-year", label: "This calendar year" },
  { key: "last-year", label: "Last calendar year" },
];

/** The dates a named period covers, as of `now`. "all" is open at both ends. */
export function periodRange(key: PeriodKey, now = new Date()): { from: string; to: string } {
  const y = now.getFullYear();
  const m = now.getMonth();
  switch (key) {
    case "all": return { from: "", to: "" };
    case "this-month": return { from: isoDate(new Date(y, m, 1)), to: isoDate(new Date(y, m + 1, 0)) };
    case "last-month": return { from: isoDate(new Date(y, m - 1, 1)), to: isoDate(new Date(y, m, 0)) };
    case "last-12": return { from: isoDate(new Date(y - 1, m, 1)), to: isoDate(new Date(y, m, 0)) };
    case "this-fy": return fyRange(fyOf(now));
    case "last-fy": return fyRange(fyOf(now) - 1);
    case "this-year": return { from: `${y}-01-01`, to: `${y}-12-31` };
    case "last-year": return { from: `${y - 1}-01-01`, to: `${y - 1}-12-31` };
  }
}

/** Which named period a date range is, if any. */
export function periodOf(from = "", to = ""): PeriodKey | "custom" {
  return PERIODS.find((p) => {
    const r = periodRange(p.key);
    return r.from === from && r.to === to;
  })?.key ?? "custom";
}

/** First and last day of a month ("2026-10"). */
export function monthRange(ym: string): { from: string; to: string } {
  const [y, m] = ym.split("-").map(Number);
  return { from: `${ym}-01`, to: isoDate(new Date(y, m, 0)) };
}
