"""What category each merchant belongs to, and a vector index to guess new ones.

`merchant_labels` holds one row per (merchant key, direction): the category it
is filed under and where that came from. Two sqlite-vec indexes in the same
SQLite file let a merchant seen for the first time borrow the category of what
it most resembles:

- merchant_vectors: each labelled merchant, embedded from a description as the
  bank printed it (its `example`), so merchants from the config count too.
- transaction_vectors: every settled transaction's raw description. Raw text,
  noise included, ranked neighbours better than cleaned keys in trials on the
  real ledger. Only rows whose category is trusted (confidence >= 0.85, so
  never a guess from this index) take part, so guesses do not feed guesses.

Origins, strongest first:
- human: you set the category in the dashboard or CLI.
- seed: the `merchants:` list in config/categories.yaml.
- llm: Claude filed it with high confidence (`ledger categorise --import`).
- bootstrap: taken from transactions already in the ledger by `ledger categorise --seed`.
A weaker origin never overwrites a stronger one.
"""
import sqlite3
from collections import Counter
from dataclasses import dataclass

import sqlite_vec

from etl.embedding import Embedder, default_embedder
from etl.merchant import direction as direction_of, merchant_key

# Rows at or above this confidence are trusted as neighbours: confirmed, labelled
# or confidently filed by Claude, never a guess from the index itself (0.6).
TRUSTED_CONFIDENCE = 0.85

ORIGIN_RANK = {"bootstrap": 0, "llm": 1, "seed": 2, "human": 3}


@dataclass
class Neighbour:
    merchant: str
    category_id: int
    similarity: float
    example: str | None = None


@dataclass
class Suggestion:
    category_id: int
    score: float          # share of the neighbours' weighted vote, 0..1
    similarity: float     # cosine similarity of the closest neighbour in this category


def load_vec(conn: sqlite3.Connection) -> None:
    """Load sqlite-vec into this connection (harmless if already loaded)."""
    try:
        conn.execute("SELECT vec_version()")
        return
    except sqlite3.OperationalError:
        pass
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


class MerchantStore:
    def __init__(self, conn: sqlite3.Connection, embedder: Embedder | None = None):
        self.conn = conn
        self.embedder = embedder or default_embedder()
        load_vec(conn)
        self._ensure_index()
        self._import_legacy_learned_rules()

    # --- index lifecycle -------------------------------------------------

    def _meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM merchant_index_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _ensure_index(self) -> None:
        """Create the vector tables, or rebuild them if the embedder changed."""
        if self._meta("embedder") == self.embedder.name:
            return
        dim = self.embedder.dim
        for table, key in (("merchant_vectors", "label_id"), ("transaction_vectors", "txn_id")):
            self.conn.execute(f"DROP TABLE IF EXISTS {table}")
            self.conn.execute(
                f"CREATE VIRTUAL TABLE {table} USING vec0({key} INTEGER PRIMARY KEY, "
                f"embedding float[{dim}] distance_metric=cosine, direction TEXT)")
        self.conn.execute(
            "INSERT OR REPLACE INTO merchant_index_meta (key, value) VALUES ('embedder', ?)",
            (self.embedder.name,))
        self._index(self.conn.execute(
            "SELECT id, COALESCE(example, merchant), direction FROM merchant_labels").fetchall())
        self.conn.commit()

    def _index(self, rows) -> None:
        """Embed and (re)insert labels, given rows of (id, text, direction)."""
        self._embed_into("merchant_vectors", "label_id", rows)

    def _embed_into(self, table: str, key: str, rows) -> None:
        rows = list(rows)
        for start in range(0, len(rows), 1024):
            chunk = rows[start:start + 1024]
            vecs = self.embedder.embed([r[1].upper() for r in chunk])
            self.conn.executemany(f"DELETE FROM {table} WHERE {key} = ?", [(r[0],) for r in chunk])
            self.conn.executemany(
                f"INSERT INTO {table} ({key}, embedding, direction) VALUES (?, ?, ?)",
                [(r[0], v.tobytes(), r[2]) for r, v in zip(chunk, vecs)])

    def sync_transactions(self) -> int:
        """Embed ledger rows that are not in the index yet. Returns how many."""
        rows = self.conn.execute(
            """SELECT t.id, t.description, CASE WHEN t.amount > 0 THEN 'in' ELSE 'out' END
               FROM transactions t
               WHERE NOT EXISTS (SELECT 1 FROM transaction_vectors v WHERE v.txn_id = t.id)""").fetchall()
        self._embed_into("transaction_vectors", "txn_id", rows)
        self.conn.commit()
        return len(rows)

    def _import_legacy_learned_rules(self) -> None:
        """Carry corrections from the old exact-description table over, once."""
        exists = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='category_rules_learned'").fetchone()
        if not exists:
            return
        for r in self.conn.execute(
                "SELECT description_pattern, category_id FROM category_rules_learned "
                "WHERE category_id IS NOT NULL").fetchall():
            amount = self.conn.execute(
                "SELECT amount FROM transactions WHERE UPPER(description) = ? LIMIT 1", (r[0],)).fetchone()
            self.learn(r[0], amount[0] if amount else -1.0, r[1], origin="human", commit=False)
        self.conn.execute("DROP TABLE category_rules_learned")
        self.conn.commit()

    # --- writes ------------------------------------------------------------

    def learn(self, description: str, amount: float, category_id: int, origin: str = "human",
              commit: bool = True) -> str | None:
        """File this transaction's merchant under a category. Returns the merchant key."""
        key = merchant_key(description)
        if not key:
            return None
        return self.learn_key(key, direction_of(amount), category_id, origin, commit, example=description)

    def learn_key(self, key: str, direction: str, category_id: int, origin: str = "human",
                  commit: bool = True, example: str | None = None) -> str:
        existing = self.conn.execute(
            "SELECT id, category_id, origin, mixed FROM merchant_labels WHERE merchant = ? AND direction = ?",
            (key, direction)).fetchone()
        if existing is not None and existing["mixed"]:
            pass        # one row's category says nothing about the merchant's next row
        elif existing is None:
            cur = self.conn.execute(
                "INSERT INTO merchant_labels (merchant, direction, category_id, origin, example) "
                "VALUES (?, ?, ?, ?, ?)", (key, direction, category_id, origin, example))
            self._index([(cur.lastrowid, example or key, direction)])
        elif ORIGIN_RANK[origin] >= ORIGIN_RANK[existing["origin"]]:
            self.conn.execute(
                "UPDATE merchant_labels SET category_id = ?, origin = ?, times_seen = times_seen + 1, "
                "example = COALESCE(?, example), updated_at = datetime('now') WHERE id = ?",
                (category_id, origin, example, existing["id"]))
        if commit:
            self.conn.commit()
        return key

    def flag(self, key: str, direction: str, alt_category_id: int | None, note: str | None = None,
             commit: bool = True) -> None:
        """Ask a person to choose between the label and an alternative category."""
        self.conn.execute(
            "UPDATE merchant_labels SET needs_review = 1, alt_category_id = ?, note = ? "
            "WHERE merchant = ? AND direction = ? AND mixed = 0", (alt_category_id, note, key, direction))
        if commit:
            self.conn.commit()

    def decide(self, label_id: int, category_id: int) -> list[int]:
        """A person's choice for a flagged merchant: label it and refile its unconfirmed rows.

        Returns the ids of the transactions refiled.
        """
        label = self.conn.execute(
            "SELECT merchant, direction FROM merchant_labels WHERE id = ?", (label_id,)).fetchone()
        if label is None:
            raise KeyError(label_id)
        self.conn.execute(
            "UPDATE merchant_labels SET category_id = ?, origin = 'human', needs_review = 0, "
            "alt_category_id = NULL, updated_at = datetime('now') WHERE id = ?", (category_id, label_id))
        ids = self.transactions_of(label["merchant"], label["direction"], confirmed=False)
        transfers = self.conn.execute("SELECT id FROM categories WHERE name = 'Transfers'").fetchone()
        transfers = transfers[0] if transfers else None
        self.conn.executemany(
            """UPDATE transactions SET category_id = ?, category_confidence = 1.0,
                   is_transfer = CASE WHEN ? = ? THEN 1 WHEN category_id = ? THEN 0 ELSE is_transfer END
               WHERE id = ?""", [(category_id, category_id, transfers, transfers, i) for i in ids])
        self.conn.commit()
        return ids

    def transactions_of(self, key: str, direction: str, confirmed: bool = True) -> list[int]:
        """Ids of the ledger rows from this merchant. confirmed=False leaves out rows a person confirmed."""
        sign = "amount > 0" if direction == "in" else "amount <= 0"
        extra = "" if confirmed else " AND COALESCE(category_confidence, 0) < 1"
        rows = self.conn.execute(f"SELECT id, description FROM transactions WHERE {sign}{extra}").fetchall()
        return [r[0] for r in rows if merchant_key(r[1]) == key]

    def forget(self, description: str, amount: float) -> bool:
        """Drop the label for this transaction's merchant (a one-off recategorisation)."""
        row = self.conn.execute(
            "SELECT id FROM merchant_labels WHERE merchant = ? AND direction = ?",
            (merchant_key(description), direction_of(amount))).fetchone()
        if not row:
            return False
        self.conn.execute("DELETE FROM merchant_vectors WHERE label_id = ?", (row[0],))
        self.conn.execute("DELETE FROM merchant_labels WHERE id = ?", (row[0],))
        self.conn.commit()
        return True

    def seed_from_transactions(self, before: str | None = None) -> int:
        """Label every merchant in the ledger with the category most of its rows carry.

        Rows you confirmed (confidence 1.0) outvote the rest. Existing labels of
        equal or stronger origin are kept. `before` (YYYY-MM-DD) limits it to
        earlier rows, for evaluation. Returns the number of labels written.
        """
        votes: dict[tuple[str, str], Counter] = {}
        examples: dict[tuple[str, str], str] = {}
        rows = self.conn.execute(
            """SELECT t.description, t.amount, t.category_id, t.category_confidence
               FROM transactions t JOIN categories c ON c.id = t.category_id
               WHERE c.name != 'Uncategorized' AND t.date < ?""", (before or "9999",)).fetchall()
        for r in rows:
            key = merchant_key(r["description"])
            if key:
                weight = 5 if (r["category_confidence"] or 0) >= 1.0 else 1
                k = (key, direction_of(r["amount"]))
                votes.setdefault(k, Counter())[r["category_id"]] += weight
                examples.setdefault(k, r["description"])
        new = []
        for (key, d), counter in votes.items():
            cat = counter.most_common(1)[0][0]
            existing = self.conn.execute(
                "SELECT id FROM merchant_labels WHERE merchant = ? AND direction = ?", (key, d)).fetchone()
            if existing:
                continue
            cur = self.conn.execute(
                "INSERT INTO merchant_labels (merchant, direction, category_id, origin, example) "
                "VALUES (?, ?, ?, 'bootstrap', ?)", (key, d, cat, examples[(key, d)]))
            new.append((cur.lastrowid, examples[(key, d)], d))
        self._index(new)
        self.conn.commit()
        return len(new)

    # --- reads -------------------------------------------------------------

    def lookup(self, description: str, amount: float) -> tuple[int, str, bool] | None:
        """(category_id, origin, needs_review) when this exact merchant is labelled.

        A mixed merchant reports needs_review, so its rows always come up for a look.
        """
        row = self.conn.execute(
            "SELECT category_id, origin, needs_review, mixed FROM merchant_labels "
            "WHERE merchant = ? AND direction = ?",
            (merchant_key(description), direction_of(amount))).fetchone()
        return (row[0], row[1], bool(row[2] or row[3])) if row else None

    def set_mixed(self, key: str, direction: str, default_category_id: int, note: str,
                  commit: bool = True) -> None:
        """Mark a merchant as mixed: rows file under the default for review, and
        filing one of its rows never relabels the merchant."""
        if not self.conn.execute("SELECT 1 FROM merchant_labels WHERE merchant = ? AND direction = ?",
                                 (key, direction)).fetchone():
            self.learn_key(key, direction, default_category_id, origin="human", commit=False)
        self.conn.execute(
            "UPDATE merchant_labels SET mixed = 1, category_id = ?, needs_review = 0, alt_category_id = NULL, "
            "note = ? WHERE merchant = ? AND direction = ?", (default_category_id, note, key, direction))
        if commit:
            self.conn.commit()

    def nearest(self, description: str, amount: float, k: int = 10,
                exclude_txn: int | None = None) -> list[Neighbour]:
        """The k most similar labelled merchants and settled transactions, same direction."""
        if not merchant_key(description):
            return []
        vec = self.embedder.embed([description.upper()])[0].tobytes()
        d = direction_of(amount)
        found = [Neighbour(r[0], r[1], 1.0 - r[2], r[3]) for r in self.conn.execute(
            """SELECT l.merchant, l.category_id, v.distance, l.example
               FROM merchant_vectors v JOIN merchant_labels l ON l.id = v.label_id
               WHERE v.embedding MATCH ? AND k = ? AND v.direction = ?
                 AND l.needs_review = 0""", (vec, k, d))]
        # Ask for more than k: untrusted rows are dropped after the search.
        for r in self.conn.execute(
                f"""SELECT t.id, t.description, t.category_id, v.distance
                    FROM transaction_vectors v JOIN transactions t ON t.id = v.txn_id
                    JOIN categories c ON c.id = t.category_id
                    WHERE v.embedding MATCH ? AND k = ? AND v.direction = ?
                      AND t.category_confidence >= {TRUSTED_CONFIDENCE} AND c.name != 'Uncategorized'""",
                (vec, k * 4, d)):
            if r[0] != exclude_txn:
                found.append(Neighbour(merchant_key(r[1]), r[2], 1.0 - r[3], r[1]))
        found.sort(key=lambda n: -n.similarity)
        return found[:k]

    def suggest(self, description: str, amount: float, k: int = 10, top: int = 3,
                exclude_txn: int | None = None) -> list[Suggestion]:
        """Categories ranked by the similarity-weighted vote of the nearest neighbours."""
        neighbours = self.nearest(description, amount, k, exclude_txn)
        weights: Counter = Counter()
        best: dict[int, float] = {}
        for n in neighbours:
            w = max(n.similarity, 0.0)
            weights[n.category_id] += w
            best[n.category_id] = max(best.get(n.category_id, 0.0), n.similarity)
        total = sum(weights.values()) or 1.0
        return [Suggestion(cat, w / total, best[cat]) for cat, w in weights.most_common(top)]

    def stats(self) -> dict:
        by_origin = dict(self.conn.execute(
            "SELECT origin, COUNT(*) FROM merchant_labels GROUP BY origin").fetchall())
        rows = self.conn.execute("SELECT COUNT(*) FROM transaction_vectors").fetchone()[0]
        return {"embedder": self.embedder.name, "labels": sum(by_origin.values()),
                "indexed_rows": rows, **by_origin}
