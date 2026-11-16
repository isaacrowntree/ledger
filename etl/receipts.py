"""`ledger receipts`: find the email receipt for each purchase and keep what was bought.

Reads mail through the himalaya CLI (https://github.com/pimalaya/himalaya), so
any account himalaya can reach works. config/receipts.yaml names the accounts:

    accounts:
      - name: personal            # a himalaya account
        mailbox: "[Gmail]/All Mail"
    since: 2019-11-01             # optional

How a purchase is matched:
- PayPal rows: the receipt's "Transaction ID" equals the row's reference id.
- Everything else: emails from a sender sharing a word with the merchant
  (AMAZON <-> amazon.com.au), dated from 10 days before to 3 days after,
  whose text contains the exact amount.

What was bought is kept in the `receipts` table and goes with each pending row
to `ledger categorise --export` / `--llm`, so a $30 Amazon charge is filed as
the tyre inflator it was, not as whatever Amazon usually is. Email text stays
local; nothing is sent anywhere by this command.
"""
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from etl.mailtext import to_text
from etl.merchant import merchant_key

WINDOW_BEFORE, WINDOW_AFTER = 10, 3
STOP = {"PTY", "LTD", "LIMITED", "THE", "AND", "COM", "WWW", "PAYPAL", "INC", "GROUP", "AUSTRALIA",
        "ONLINE", "STORE", "SHOP", "MARKETPLACE", "MKTPLC", "SYDNEY", "MELBOURNE", "AUS", "PAYMENT",
        "PURCHASE", "VISA", "CARD", "DIRECT", "DEBIT", "TRANSFER", "DEPOSIT", "RECEIPT"}

SCHEMA = """CREATE TABLE IF NOT EXISTS receipts (
    transaction_id INTEGER PRIMARY KEY REFERENCES transactions(id),
    account TEXT NOT NULL, message_id TEXT NOT NULL, subject TEXT, sender TEXT, date TEXT,
    items TEXT,                       -- one item per line
    matched_by TEXT NOT NULL CHECK (matched_by IN ('paypal-id', 'amount')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)"""


class Himalaya:
    """The few himalaya calls this needs. Tests pass a stand-in."""
    def __init__(self, binary: str = "himalaya"):
        self.binary = binary

    def search(self, account: str, mailbox: str, query: str) -> list[dict]:
        out = subprocess.run([self.binary, "--json", "-a", account, "envelope", "search", "-m", mailbox,
                              "-s", "5000", "--", query], capture_output=True, text=True, timeout=600)
        try:
            return json.loads(out.stdout).get("envelopes", [])
        except (json.JSONDecodeError, AttributeError):
            return []

    def read(self, account: str, mailbox: str, message_id: str) -> str:
        return subprocess.run([self.binary, "-a", account, "message", "read", "-m", mailbox, str(message_id)],
                              capture_output=True, text=True, timeout=120).stdout


@dataclass
class Account:
    name: str
    mailbox: str = "INBOX"


def load_config(path: Path) -> tuple[list[Account], str | None]:
    import yaml
    cfg = yaml.safe_load(path.read_text()) or {}
    since = cfg.get("since")          # YAML reads 2025-07-01 as a date
    return ([Account(a["name"], a.get("mailbox", "INBOX")) for a in cfg.get("accounts", [])],
            str(since) if since else None)


def items_from(text: str, subject: str) -> list[str]:
    """What was bought, as best the email says."""
    lines = text.split("\n")
    stars = [l[2:].strip() for l in lines if l.startswith("* ")]          # Amazon
    if stars:
        return stars[:10]
    if "Description" in lines:                                            # PayPal receipt table
        start = lines.index("Description") + 1
        out = []
        for l in lines[start:start + 40]:
            if l.startswith(("Subtotal", "Total")):
                break
            if l in ("Unit price", "Qty", "Amount") or re.fullmatch(r"[\$€£]?[\d,]+\.\d\d( [A-Z]{3})?|\d+", l):
                continue
            out.append(l)
        if out:
            return out[:10]
    qty = [lines[i - 1] for i, l in enumerate(lines) if re.match(r"(Qty|Quantity)\b", l, re.I) and i]
    return qty[:10] or ([subject] if subject else [])


def _has_amount(text: str, amount: float) -> bool:
    """The exact amount appears as a whole number in the text: 5.00 matches "$5.00",
    not "15.00" or "105.00"; 1,234.50 matches with or without the comma."""
    forms = {f"{amount:,.2f}", f"{amount:.2f}"}
    return any(re.search(rf"(?<![\d.,]){re.escape(f)}(?!\d)", text) for f in forms)


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[A-Z]{4,}", (s or "").upper()) if w not in STOP}


def _ensure(conn):
    conn.execute(SCHEMA)


def targets(conn, since: str, everything: bool) -> list[sqlite3.Row]:
    """Outgoing purchases without a receipt yet. By default only the ones where knowing
    the item can change the answer: not yet settled, mixed merchants, business, electronics."""
    filt = "" if everything else """
        AND (c.name = 'Uncategorized' OR t.category_confidence < 0.85
             OR c.name LIKE 'Business:%' OR c.name = 'Electronics')"""
    return conn.execute(f"""
        SELECT t.id, t.date, t.amount, t.description, t.source_type, t.reference_id
        FROM transactions t JOIN categories c ON c.id = t.category_id
        WHERE t.amount < 0 AND t.is_transfer = 0 AND t.date >= ? {filt}
          AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.transaction_id = t.id)
        ORDER BY t.date""", (since,)).fetchall()


def _store(conn, txn_id, acct, env, text, how):
    sender = ((env.get("from") or [{}])[0] or {}).get("email", "")
    conn.execute("INSERT OR REPLACE INTO receipts (transaction_id, account, message_id, subject, sender, date, "
                 "items, matched_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (txn_id, acct.name, str(env["id"]), env.get("subject"), sender, (env.get("date") or "")[:10],
                  "\n".join(items_from(text, env.get("subject") or "")), how))


def _years(since: str):
    for y in range(int(since[:4]), date.today().year + 1):
        yield f"{y}-01-01", f"{y}-12-31"


def match(conn, accounts: list[Account], since: str, everything: bool = False,
          dry_run: bool = False, mail: Himalaya | None = None) -> dict:
    mail = mail or Himalaya()
    _ensure(conn)
    rows = targets(conn, since, everything)
    found = {"paypal-id": 0, "amount": 0}
    done: set[int] = set()

    # 1. PayPal: one pass over the receipts, joined on the transaction id.
    paypal = {r["reference_id"]: r for r in rows if r["source_type"] == "paypal" and r["reference_id"]}
    if paypal:
        for acct in accounts:
            for lo, hi in _years(since):
                for env in mail.search(acct.name, acct.mailbox, f"from paypal and after {lo} and not after {hi}"):
                    if "receipt" not in (env.get("subject") or "").lower():
                        continue
                    text = to_text(mail.read(acct.name, acct.mailbox, env["id"]))
                    m = re.search(r"Transaction ID:?\s*([A-Z0-9]{12,20})", text)
                    row = paypal.get(m.group(1)) if m else None
                    if row is not None and row["id"] not in done:
                        done.add(row["id"]); found["paypal-id"] += 1
                        if not dry_run:
                            _store(conn, row["id"], acct, env, text, "paypal-id")

    # 2. Everyone else: one search per merchant word over the span its rows cover.
    by_word: dict[str, list] = {}
    for r in rows:
        if r["id"] in done or r["source_type"] == "paypal":
            continue
        words = _words(merchant_key(r["description"]) or r["description"])
        if words:
            by_word.setdefault(max(words, key=len), []).append(r)
    for word, group in by_word.items():
        lo = (date.fromisoformat(min(r["date"] for r in group)) - timedelta(days=WINDOW_BEFORE)).isoformat()
        hi = (date.fromisoformat(max(r["date"] for r in group)) + timedelta(days=WINDOW_AFTER)).isoformat()
        for acct in accounts:
            envs = mail.search(acct.name, acct.mailbox, f"from {word.lower()} and after {lo} and not after {hi}")
            texts: dict[str, str] = {}
            for r in group:
                if r["id"] in done:
                    continue
                d = date.fromisoformat(r["date"])
                amt = f"{abs(r['amount']):,.2f}"
                for env in envs:
                    edate = (env.get("date") or "")[:10]
                    if not edate or not (-WINDOW_BEFORE <= (date.fromisoformat(edate) - d).days <= WINDOW_AFTER):
                        continue
                    key = str(env["id"])
                    if key not in texts:
                        texts[key] = to_text(mail.read(acct.name, acct.mailbox, env["id"]))
                    if _has_amount(texts[key], abs(r["amount"])):
                        done.add(r["id"]); found["amount"] += 1
                        if not dry_run:
                            _store(conn, r["id"], acct, env, texts[key], "amount")
                        break
    if not dry_run:
        conn.commit()
    return {"looked_for": len(rows), **found}


def run(config_path: Path, since: str | None = None, everything: bool = False, dry_run: bool = False) -> None:
    from etl import db
    if not config_path.exists():
        raise SystemExit(f"No {config_path}. Copy config/receipts.yaml.example and name your himalaya accounts.")
    accounts, cfg_since = load_config(config_path)
    since = since or cfg_since or (date.today() - timedelta(days=365)).isoformat()
    conn = db.get_connection()
    db.init_db(conn)
    r = match(conn, accounts, since, everything, dry_run)
    print(f"Looked for {r['looked_for']} receipts since {since}: {r['paypal-id']} PayPal by transaction id, "
          f"{r['amount']} by sender and exact amount" + (" (dry run, nothing written)" if dry_run else ""))
    if not dry_run and (r["paypal-id"] or r["amount"]):
        print("Their items now go with each row to `ledger categorise --export` and `--llm`.")
