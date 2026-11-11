// The transaction search box takes plain words plus a few typed filters, the
// way mail and issue trackers do:
//
//   category:"Eating Out"  account:"ING Orange Everyday"  tag:partner-rent
//   is:in  is:out  is:shared  is:review  is:transfer  amount>50  amount<200
//
// Anything else is free text, matched against descriptions and notes. Every
// filter runs on the server, so the totals beside the list cover every match,
// not just the rows on screen.

export interface TxnSearch {
  q?: string;
  from?: string;
  to?: string;
}

const TOKEN = /(\w+)(:|>=|<=|>|<)("([^"]*)"|\S+)/g;

export function parseQuery(q = ""): Record<string, string> {
  const filters: Record<string, string> = {};
  const rest = q.replace(TOKEN, (whole, key: string, op: string, raw: string, quoted?: string) => {
    const value = quoted ?? raw;
    const k = key.toLowerCase();
    if (op === ":" && (k === "category" || k === "account" || k === "tag")) filters[k] = value;
    else if (op === ":" && k === "is") {
      const v = value.toLowerCase();
      if (v === "in" || v === "out") filters.direction = v;
      else if (v === "shared") filters.shared = "1";
      else if (v === "review") filters.review = "1";
      else if (v === "transfer") filters.transfers = "1";
      else return whole;
    } else if (k === "amount" && op !== ":" && !Number.isNaN(Number(value))) {
      filters[op.startsWith(">") ? "min" : "max"] = value;
    } else return whole;
    return " ";
  });
  const search = rest.replace(/\s+/g, " ").trim();
  if (search) filters.search = search;
  return filters;
}

const quote = (v: string) => (/\s/.test(v) ? `"${v}"` : v);

/** The query with `key:value` set (or removed when value is empty). */
export function withToken(q = "", key: string, value: string): string {
  const re = new RegExp(`\\b${key}:("[^"]*"|\\S+)\\s*`, "gi");
  const stripped = q.replace(re, "").trim();
  return value ? `${stripped} ${key}:${quote(value)}`.trim() : stripped;
}

/** The current value of `key:` in the query, if any. */
export function tokenValue(q = "", key: string): string {
  const m = new RegExp(`\\b${key}:("([^"]*)"|\\S+)`, "i").exec(q);
  return m ? (m[2] ?? m[1]) : "";
}

/** Build a drill-through search: a category, an account, a period. */
export function txnSearch(opts: { category?: string; account?: string; tag?: string; text?: string; from?: string; to?: string }): TxnSearch {
  let q = opts.text ?? "";
  if (opts.category) q = withToken(q, "category", opts.category);
  if (opts.account) q = withToken(q, "account", opts.account);
  if (opts.tag) q = withToken(q, "tag", opts.tag);
  return { ...(q ? { q } : {}), ...(opts.from ? { from: opts.from } : {}), ...(opts.to ? { to: opts.to } : {}) };
}
