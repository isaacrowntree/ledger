"""Nearest-neighbour search over embeddings, with or without sqlite-vec.

Two backends behind one small interface:

- SqliteVecIndex: sqlite-vec virtual tables in the ledger's SQLite file. Fast,
  but needs the sqlite-vec package and a Python whose sqlite3 allows loading
  extensions (some system and distro builds don't).
- NumpyIndex: vectors as BLOBs in an ordinary table, searched by brute force
  with NumPy. No extension needed; a personal ledger has tens of thousands of
  rows, which this searches in milliseconds.

`open_index` picks sqlite-vec when it loads and falls back to NumPy otherwise.
LEDGER_VECTOR_BACKEND=numpy or =sqlite-vec forces one. Each index has two
collections: "merchant" (labelled merchants, keyed by label id) and "txn"
(ledger rows, keyed by transaction id). Search returns (id, cosine distance).
"""
import os
import sqlite3
import warnings

import numpy as np

COLLECTIONS = {"merchant": ("merchant_vectors", "label_id"), "txn": ("transaction_vectors", "txn_id")}


class SqliteVecIndex:
    backend = "sqlite-vec"

    def __init__(self, conn: sqlite3.Connection, dim: int):
        self.conn, self.dim = conn, dim
        _load_sqlite_vec(conn)

    def reset(self) -> None:
        for table, key in COLLECTIONS.values():
            self.conn.execute(f"DROP TABLE IF EXISTS {table}")
            self.conn.execute(
                f"CREATE VIRTUAL TABLE {table} USING vec0({key} INTEGER PRIMARY KEY, "
                f"embedding float[{self.dim}] distance_metric=cosine, direction TEXT)")

    def upsert(self, collection: str, rows) -> None:
        """rows: (id, unit vector, direction)."""
        table, key = COLLECTIONS[collection]
        rows = list(rows)
        self.conn.executemany(f"DELETE FROM {table} WHERE {key} = ?", [(r[0],) for r in rows])
        self.conn.executemany(f"INSERT INTO {table} ({key}, embedding, direction) VALUES (?, ?, ?)",
                              [(r[0], np.asarray(r[1], np.float32).tobytes(), r[2]) for r in rows])

    def delete(self, collection: str, ids) -> None:
        table, key = COLLECTIONS[collection]
        self.conn.executemany(f"DELETE FROM {table} WHERE {key} = ?", [(i,) for i in ids])

    def search(self, collection: str, vec, direction: str, k: int) -> list[tuple[int, float]]:
        table, key = COLLECTIONS[collection]
        return [(r[0], r[1]) for r in self.conn.execute(
            f"SELECT {key}, distance FROM {table} WHERE embedding MATCH ? AND k = ? AND direction = ? "
            f"ORDER BY distance", (np.asarray(vec, np.float32).tobytes(), k, direction))]

    def missing_txns(self) -> list:
        return self.conn.execute(
            """SELECT t.id, t.description, CASE WHEN t.amount > 0 THEN 'in' ELSE 'out' END
               FROM transactions t
               WHERE NOT EXISTS (SELECT 1 FROM transaction_vectors v WHERE v.txn_id = t.id)""").fetchall()

    def count(self, collection: str) -> int:
        table, _ = COLLECTIONS[collection]
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class NumpyIndex:
    backend = "numpy"

    def __init__(self, conn: sqlite3.Connection, dim: int):
        self.conn, self.dim = conn, dim
        self._cache: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
        conn.execute("""CREATE TABLE IF NOT EXISTS vector_store (
            collection TEXT NOT NULL, ref_id INTEGER NOT NULL, direction TEXT NOT NULL,
            embedding BLOB NOT NULL, PRIMARY KEY (collection, ref_id))""")

    def reset(self) -> None:
        self.conn.execute("DELETE FROM vector_store")
        self._cache.clear()

    def upsert(self, collection: str, rows) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO vector_store (collection, ref_id, direction, embedding) VALUES (?, ?, ?, ?)",
            [(collection, r[0], r[2], np.asarray(r[1], np.float32).tobytes()) for r in rows])
        self._drop_cache(collection)

    def delete(self, collection: str, ids) -> None:
        self.conn.executemany("DELETE FROM vector_store WHERE collection = ? AND ref_id = ?",
                              [(collection, i) for i in ids])
        self._drop_cache(collection)

    def _drop_cache(self, collection: str) -> None:
        for k in [k for k in self._cache if k[0] == collection]:
            del self._cache[k]

    def _matrix(self, collection: str, direction: str):
        if (collection, direction) not in self._cache:
            rows = self.conn.execute(
                "SELECT ref_id, embedding FROM vector_store WHERE collection = ? AND direction = ?",
                (collection, direction)).fetchall()
            ids = np.array([r[0] for r in rows], dtype=np.int64)
            mat = (np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), self.dim)
                   if rows else np.zeros((0, self.dim), np.float32))
            self._cache[(collection, direction)] = (ids, mat)
        return self._cache[(collection, direction)]

    def search(self, collection: str, vec, direction: str, k: int) -> list[tuple[int, float]]:
        ids, mat = self._matrix(collection, direction)
        if not len(ids):
            return []
        sims = mat @ np.asarray(vec, np.float32)
        top = np.argsort(-sims)[:k]
        return [(int(ids[i]), float(1.0 - sims[i])) for i in top]

    def missing_txns(self) -> list:
        return self.conn.execute(
            """SELECT t.id, t.description, CASE WHEN t.amount > 0 THEN 'in' ELSE 'out' END
               FROM transactions t WHERE NOT EXISTS (
                 SELECT 1 FROM vector_store v WHERE v.collection = 'txn' AND v.ref_id = t.id)""").fetchall()

    def count(self, collection: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM vector_store WHERE collection = ?",
                                 (collection,)).fetchone()[0]


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("SELECT vec_version()")
        return
    except sqlite3.OperationalError:
        pass
    import sqlite_vec           # ImportError when not installed
    conn.enable_load_extension(True)   # AttributeError/OperationalError when the build forbids it
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


_warned = False


def open_index(conn: sqlite3.Connection, dim: int):
    """sqlite-vec when it loads, NumPy otherwise (or as LEDGER_VECTOR_BACKEND says)."""
    global _warned
    choice = os.environ.get("LEDGER_VECTOR_BACKEND", "")
    if choice == "numpy":
        return NumpyIndex(conn, dim)
    try:
        return SqliteVecIndex(conn, dim)
    except (ImportError, AttributeError, sqlite3.OperationalError) as e:
        if choice == "sqlite-vec":
            raise
        if not _warned:
            warnings.warn(f"sqlite-vec unavailable ({e}); using the NumPy vector index")
            _warned = True
        return NumpyIndex(conn, dim)
