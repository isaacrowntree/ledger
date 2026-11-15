"""`ledger categorise`: label merchants, recategorise, and measure the categoriser."""
import sqlite3
from collections import Counter
from pathlib import Path

from etl import db
from etl.categorizer import CONFIDENCE, Categorizer
from etl.merchant_store import load_vec
from etl.models import RawTransaction


# Claude's confidence at or above which its choice is filed and its merchant
# learned. In the blind trial on new merchants, 0.9+ was right 97% of the time.
LLM_LEARN_CONFIDENCE = 0.9
# Rows below that are filed but stay flagged for review at this confidence.
LLM_REVIEW_CONFIDENCE = 0.5


def run(config_path: Path, seed=False, rerun=False, evaluate=None, stats=False, dry_run=False,
        export=None, import_=None):
    conn = db.get_connection()
    db.init_db(conn)
    db.load_categories_from_config(conn, config_path)
    if evaluate:
        return _evaluate(conn, config_path, evaluate)
    categorizer = Categorizer(conn, config_path)
    store = categorizer.store
    if seed:
        n = store.seed_from_transactions()
        print(f"Labelled {n} new merchants from the ledger.")
        print(_format_stats(store.stats()))
    elif stats:
        print(_format_stats(store.stats()))
    elif rerun:
        _rerun(conn, categorizer, dry_run)
    elif export:
        _export(conn, categorizer, Path(export))
    elif import_:
        _import(conn, categorizer, Path(import_), dry_run)
    conn.close()


def _format_stats(s: dict) -> str:
    return (f"{s['labels']} merchants labelled ({s.get('human', 0)} by you, {s.get('llm', 0)} by Claude, "
            f"{s.get('seed', 0)} from config, {s.get('bootstrap', 0)} from the existing ledger); "
            f"{s['indexed_rows']} transactions indexed with {s['embedder']}")


def _names(conn) -> dict[int, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT id, name FROM categories")}


def _txn(r) -> RawTransaction:
    return RawTransaction(date=r["date"], description=r["description"], amount=r["amount"],
                          source_type=r["source_type"])


def _rerun(conn, categorizer: Categorizer, dry_run: bool) -> None:
    """Recategorise rows nobody confirmed. Confirmed rows (confidence 1.0) are left alone."""
    names = _names(conn)
    transfers = db.get_category_id(conn, "Transfers")
    rows = conn.execute(
        "SELECT id, date, description, amount, source_type, category_id FROM transactions "
        "WHERE COALESCE(category_confidence, 0) < 1").fetchall()
    moves: Counter = Counter()
    updates = []
    for r in rows:
        cat_id, conf = categorizer.categorize(_txn(r))
        if cat_id != r["category_id"]:
            moves[(names.get(r["category_id"]), names.get(cat_id))] += 1
        updates.append((cat_id, conf, cat_id, transfers, r["category_id"], transfers, r["id"]))
    print(f"{len(rows)} unconfirmed rows, {sum(moves.values())} would change category")
    for (old, new), n in moves.most_common(20):
        print(f"  {n:5d}  {old} -> {new}")
    if dry_run:
        print("Dry run: nothing written.")
        return
    # is_transfer follows the category only where the category is or was Transfers,
    # so flags set by the card-payment rule elsewhere are left as they are.
    conn.executemany(
        """UPDATE transactions SET category_id = ?, category_confidence = ?,
               is_transfer = CASE WHEN ? = ? THEN 1 WHEN ? = ? THEN 0 ELSE is_transfer END
           WHERE id = ?""", updates)
    conn.commit()
    print("Written.")


def _evaluate(conn: sqlite3.Connection, config_path: Path, cutoff: str) -> None:
    """Score the categoriser on rows dated from `cutoff`, learning only from earlier rows.

    The ledger is copied into memory, so nothing is written. The current
    categories are the answer key; rows you confirmed are scored separately as
    the most trustworthy part of it.
    """
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    conn.backup(mem)
    load_vec(mem)
    mem.execute("DROP TABLE IF EXISTS category_rules_learned")  # corrections may postdate the cutoff
    mem.execute("DELETE FROM merchant_labels")
    mem.execute("DELETE FROM merchant_index_meta")  # forces a fresh, empty vector index
    # Rows from the cutoff on are what is being scored: they must not be neighbours.
    # Their category stays as the answer key; only their trust is withdrawn.
    mem.execute("UPDATE transactions SET category_confidence = 0 WHERE date >= ?", (cutoff,))
    categorizer = Categorizer(mem, config_path)
    store = categorizer.store
    store.seed_from_transactions(before=cutoff)
    names = _names(mem)
    uncategorised = db.get_category_id(mem, "Uncategorized")

    rows = mem.execute(
        "SELECT date, description, amount, source_type, category_id, category_confidence "
        "FROM transactions WHERE date >= ? AND category_id != ?", (cutoff, uncategorised)).fetchall()
    if not rows:
        print(f"No categorised rows from {cutoff}.")
        return
    tally = {k: [0, 0] for k in ("source", "merchant", "nearest", "none")}
    sweep = []          # (similarity, share, right?) for every novel merchant
    top3 = [0, 0]
    confusions: Counter = Counter()
    for r in rows:
        txn = _txn(r)
        cat_id, conf = categorizer.categorize(txn)
        if any(txn.source_type == rule["source_type"] for rule in categorizer.source_rules):
            kind = "source"
        elif store.lookup(txn.description, txn.amount):
            kind = "merchant"
        else:
            kind = "nearest" if conf == CONFIDENCE["nearest"] else "none"
            suggestions = store.suggest(txn.description, txn.amount, top=3)
            if suggestions:
                best = suggestions[0]
                sweep.append((best.similarity, best.score, best.category_id == r["category_id"]))
                top3[0] += any(s.category_id == r["category_id"] for s in suggestions)
            top3[1] += 1
        tally[kind][1] += 1
        if kind != "none":
            right = cat_id == r["category_id"]
            tally[kind][0] += right
            if not right:
                confusions[(names[r["category_id"]], names[cat_id])] += 1

    n = len(rows)
    print(f"Learned from rows before {cutoff}; scoring {n} rows from it ({store.embedder.name}).\n")
    labels = {"source": "source rule", "merchant": "known merchant",
              "nearest": "nearest merchants", "none": "left Uncategorized"}
    for kind, (right, total) in tally.items():
        if total:
            acc = f"{right / total:6.1%} right" if kind != "none" else ""
            print(f"  {labels[kind]:20s} {total:6d} rows ({total / n:5.1%})  {acc}")
    if top3[1]:
        print(f"\n  New merchants: the right category is among the top 3 suggestions "
              f"for {top3[0] / top3[1]:.1%} of {top3[1]} rows.")
    if sweep:
        print("\n  Guessing new merchants at other thresholds (similarity, vote share):")
        for sim in (0.7, 0.75, 0.8, 0.85, 0.9, 0.95):
            for share in (0.5, 0.6, 0.8):
                taken = [ok for s, sh, ok in sweep if s >= sim and sh >= share]
                if taken:
                    print(f"    >= {sim:.2f}, >= {share:.1f}: guesses {len(taken) / len(sweep):5.1%} "
                          f"of them, {sum(taken) / len(taken):5.1%} right")
    if confusions:
        print("\n  Most common misses (answer key -> guess):")
        for (want, got), k in confusions.most_common(10):
            print(f"    {k:5d}  {want} -> {got}")


def _export(conn, categorizer: Categorizer, path: Path) -> None:
    """Write the rows the categoriser could not settle, with context for Claude to file them.

    Pending means Uncategorized, or a guess from the neighbours that nobody
    confirmed. Each row carries the ten most similar merchants already filed
    (with one description as the bank printed it), and each category carries a
    few merchants filed under it, so the person's own conventions are visible.
    """
    import json
    names = _names(conn)
    store = categorizer.store
    rows = conn.execute(
        """SELECT t.id, t.date, t.description, t.amount, t.source_type, a.name AS account, c.name AS category
           FROM transactions t JOIN categories c ON c.id = t.category_id
           LEFT JOIN accounts a ON a.id = t.account_id
           WHERE COALESCE(t.category_confidence, 0) < 1
             AND (c.name = 'Uncategorized' OR t.category_confidence <= ?)
           ORDER BY t.date""", (CONFIDENCE["nearest"],)).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r["id"], "date": r["date"], "description": r["description"],
            "source": r["source_type"], "account": r["account"],
            "direction": "in" if r["amount"] > 0 else "out", "amount": round(abs(r["amount"]), 2),
            "current_guess": None if r["category"] == "Uncategorized" else r["category"],
            "similar_filed": [
                {"description": n.example or n.merchant, "category": names[n.category_id],
                 "similarity": round(n.similarity, 2)}
                for n in store.nearest(r["description"], r["amount"], k=10)],
        })
    examples = {}
    for cat_id, name in sorted(names.items(), key=lambda kv: kv[1]):
        if name == "Uncategorized":
            continue
        ex = conn.execute(
            """SELECT COALESCE(example, merchant) FROM merchant_labels WHERE category_id = ?
               ORDER BY CASE origin WHEN 'human' THEN 0 WHEN 'seed' THEN 1 ELSE 2 END, times_seen DESC
               LIMIT 6""", (cat_id,)).fetchall()
        examples[name] = [e[0] for e in ex]
    path.write_text(json.dumps({
        "instructions": (
            "File each item under exactly one of `categories`. similar_filed shows how this person "
            "filed the most similar merchants; category_examples shows what each category holds. "
            "Answer as {\"<id>\": {\"category\": ..., \"confidence\": 0-1}}. Be calibrated: "
            f"{LLM_LEARN_CONFIDENCE}+ files the row and teaches its merchant; lower is filed but flagged for review."),
        "categories": [n for n in sorted(names.values()) if n != "Uncategorized"],
        "category_examples": examples,
        "items": items,
    }, indent=1))
    print(f"Exported {len(items)} pending rows to {path}")


def _import(conn, categorizer: Categorizer, path: Path, dry_run: bool) -> None:
    """File Claude's answers. Confident ones also teach the merchant (origin 'llm')."""
    import json
    answers = json.loads(path.read_text())
    ids = {name: cat_id for cat_id, name in _names(conn).items()}
    transfers = ids.get("Transfers")
    bad = {k: v["category"] for k, v in answers.items() if v.get("category") not in ids}
    if bad:
        raise SystemExit(f"Unknown categories, nothing written: {bad}")
    learned = flagged = skipped = 0
    for txn_id, ans in answers.items():
        row = conn.execute(
            "SELECT id, description, amount, category_confidence FROM transactions WHERE id = ?",
            (int(txn_id),)).fetchone()
        if row is None or (row["category_confidence"] or 0) >= 1:
            skipped += 1     # gone, or confirmed by a person since the export
            continue
        cat_id = ids[ans["category"]]
        confident = float(ans.get("confidence", 0)) >= LLM_LEARN_CONFIDENCE
        if dry_run:
            learned += confident; flagged += not confident
            continue
        conn.execute(
            """UPDATE transactions SET category_id = ?, category_confidence = ?,
                   is_transfer = CASE WHEN ? = ? THEN 1 WHEN category_id = ? THEN 0 ELSE is_transfer END
               WHERE id = ?""",
            (cat_id, CONFIDENCE["llm"] if confident else LLM_REVIEW_CONFIDENCE,
             cat_id, transfers, transfers, row["id"]))
        if confident:
            categorizer.store.learn(row["description"], row["amount"], cat_id, origin="llm", commit=False)
            learned += 1
        else:
            flagged += 1
    if not dry_run:
        conn.commit()
    print(f"{learned} filed and learned, {flagged} filed for review, {skipped} skipped"
          + (" (dry run, nothing written)" if dry_run else ""))
