// Money is always shown with an explicit sign when it is owed: colour alone
// never carries "you owe this".

const AUD = new Intl.NumberFormat("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const AUD0 = new Intl.NumberFormat("en-AU", { maximumFractionDigits: 0 });

export function money(n: number, opts: { cents?: boolean; sign?: boolean } = {}): string {
  const { cents = true, sign = false } = opts;
  const body = (cents ? AUD : AUD0).format(Math.abs(n));
  if (n < 0 && Math.abs(n) >= (cents ? 0.005 : 0.5)) return `−$${body}`;
  return `${sign && n > 0 ? "+" : ""}$${body}`;
}

export function pct(n: number | null | undefined, digits = 1): string {
  return n == null ? "—" : `${n.toFixed(digits)}%`;
}

export function fyLabel(fy: number): string {
  return `FY ${fy - 1}–${String(fy).slice(2)}`;
}

/** The Australian financial year (ending 30 June) a date falls in. */
export function fyOf(d = new Date()): number {
  return d.getMonth() >= 6 ? d.getFullYear() + 1 : d.getFullYear();
}

export function fyRange(fy: number): { from: string; to: string } {
  return { from: `${fy - 1}-07-01`, to: `${fy}-06-30` };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function monthShort(ym: string): string {
  return MONTHS[Number(ym.slice(5, 7)) - 1] ?? ym;
}

export function monthLong(ym: string): string {
  return `${monthShort(ym)} ${ym.slice(0, 4)}`;
}

export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function displayDate(iso: string): string {
  if (!iso || iso.length < 10) return iso;
  return `${Number(iso.slice(8, 10))} ${monthShort(iso.slice(0, 7))} ${iso.slice(0, 4)}`;
}
