"""Choose a category for a transaction from what you have filed before.

In order:
1. Source rules (`type: source` in config/categories.yaml): every row from a
   source belongs to one category, e.g. Airbnb payouts are Airbnb Income.
2. The merchant's own label, when this merchant has been filed before.
3. The nearest labelled merchants and settled transactions in the vector
   index, compared on the raw description, when they agree strongly enough. Otherwise the row stays Uncategorized, and the dashboard
   offers the nearest merchants' categories as suggestions.

There are no description patterns to maintain. Filing a transaction in the
dashboard labels its merchant, which covers every later visit to it.
"""
import sqlite3
from pathlib import Path
from typing import Optional

import yaml

from etl import db
from etl.embedding import Embedder
from etl.merchant_store import MerchantStore
from etl.models import RawTransaction

# Confidence written to transactions.category_confidence. 1.0 is reserved for
# rows a person confirmed, so the "needs review" flag keeps working.
CONFIDENCE = {"source": 0.95, "human": 0.95, "seed": 0.9, "llm": 0.85, "bootstrap": 0.9, "nearest": 0.6}

# A guess from the neighbours is used only when the closest one is at least the
# embedder's min_similarity and this share of the weighted vote agrees. Tuned
# with `ledger categorise --evaluate`.
NEAREST_MIN_SHARE = 0.8


class Categorizer:
    def __init__(self, conn: sqlite3.Connection, config_path: Path, embedder: Embedder | None = None):
        self.conn = conn
        config = _load(config_path)
        self.source_rules = [r for r in config.get("rules", []) if r.get("type") == "source"]
        self.store = MerchantStore(conn, embedder)
        self._seed(config.get("merchants") or {})
        self.store.sync_transactions()

    def categorize(self, txn: RawTransaction) -> tuple[Optional[int], Optional[float]]:
        """Return (category_id, confidence) for a transaction."""
        for rule in self.source_rules:
            if txn.source_type == rule["source_type"]:
                cat_id = db.get_category_id(self.conn, rule["category"])
                if cat_id:
                    return cat_id, CONFIDENCE["source"]

        hit = self.store.lookup(txn.description, txn.amount)
        if hit:
            return hit[0], CONFIDENCE[hit[1]]

        suggestions = self.store.suggest(txn.description, txn.amount, top=1)
        if suggestions:
            best = suggestions[0]
            if best.similarity >= self.store.embedder.min_similarity and best.score >= NEAREST_MIN_SHARE:
                return best.category_id, CONFIDENCE["nearest"]

        return db.get_category_id(self.conn, "Uncategorized"), 0.0

    def _seed(self, merchants: dict) -> None:
        """Label the merchants listed under `merchants:` in the config.

        Each entry maps a merchant as it appears on a statement to a category.
        A label you set in the dashboard is never overwritten by the config.
        """
        for description, category in merchants.items():
            cat_id = db.get_category_id(self.conn, category)
            if cat_id is None:
                raise ValueError(f"merchants: {description!r} names unknown category {category!r}")
            for direction in ("out", "in"):
                amount = -1.0 if direction == "out" else 1.0
                existing = self.store.lookup(str(description), amount)
                if existing is None or (existing[1] != "human" and existing[0] != cat_id):
                    self.store.learn(str(description), amount, cat_id, origin="seed", commit=False)
        self.conn.commit()


def _load(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f) or {}
