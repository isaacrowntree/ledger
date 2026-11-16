"""Both vector backends answer the same way, and the categoriser runs on either."""
import sqlite3

import numpy as np
import pytest

from etl import vector_index
from etl.vector_index import NumpyIndex, SqliteVecIndex, open_index


def _unit(*xs):
    v = np.array(xs, np.float32)
    return v / np.linalg.norm(v)


def _backends():
    yield NumpyIndex
    try:
        SqliteVecIndex(sqlite3.connect(":memory:"), 3)
        yield SqliteVecIndex
    except Exception:          # sqlite-vec not installed or not loadable here
        pass


@pytest.mark.parametrize("cls", list(_backends()), ids=lambda c: c.backend)
def test_search_ranks_by_cosine_within_a_direction(cls):
    conn = sqlite3.connect(":memory:")
    idx = cls(conn, 3)
    idx.reset()
    idx.upsert("merchant", [(1, _unit(1, 0, 0), "out"), (2, _unit(1, 1, 0), "out"),
                            (3, _unit(1, 0, 0), "in")])
    hits = idx.search("merchant", _unit(1, 0, 0), "out", 5)
    assert [h[0] for h in hits] == [1, 2]
    assert hits[0][1] == pytest.approx(0.0, abs=1e-5)
    idx.delete("merchant", [1])
    assert [h[0] for h in idx.search("merchant", _unit(1, 0, 0), "out", 5)] == [2]
    assert idx.count("merchant") == 2


def test_numpy_is_used_when_forced(monkeypatch):
    monkeypatch.setenv("LEDGER_VECTOR_BACKEND", "numpy")
    assert isinstance(open_index(sqlite3.connect(":memory:"), 3), NumpyIndex)


def test_falls_back_to_numpy_when_sqlite_vec_will_not_load(monkeypatch):
    monkeypatch.delenv("LEDGER_VECTOR_BACKEND", raising=False)
    monkeypatch.setattr(vector_index, "_warned", False)

    def broken(conn):
        raise sqlite3.OperationalError("not authorized")
    monkeypatch.setattr(vector_index, "_load_sqlite_vec", broken)
    with pytest.warns(UserWarning, match="NumPy"):
        idx = open_index(sqlite3.connect(":memory:"), 3)
    assert isinstance(idx, NumpyIndex)


def test_categoriser_works_on_the_numpy_backend(monkeypatch):
    monkeypatch.setenv("LEDGER_VECTOR_BACKEND", "numpy")
    from pathlib import Path
    from etl import db
    from etl.categorizer import Categorizer, CONFIDENCE
    from etl.embedding import NgramEmbedder
    from etl.models import RawTransaction
    fixtures = Path(__file__).parent / "fixtures" / "categories.yaml"
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    db.init_db(conn); db.load_categories_from_config(conn, fixtures)
    cat = Categorizer(conn, fixtures, embedder=NgramEmbedder())
    assert cat.store.index.backend == "numpy"
    cid, conf = cat.categorize(RawTransaction(date="2025-01-01", description="NETFLIX.COM",
                                              amount=-20.0, source_type="ing"))
    assert conn.execute("SELECT name FROM categories WHERE id = ?", (cid,)).fetchone()[0] == "Subscriptions"
    assert conf == CONFIDENCE["seed"]
    assert cat.store.suggest("WOOLWORTH", -10.0)[0].category_id == db.get_category_id(conn, "Groceries")
