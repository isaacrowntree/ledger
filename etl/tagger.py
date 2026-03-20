"""Auto-tagger: assigns multiple tags to transactions based on regex rules.

Tags are orthogonal to categories — a transaction has one category but
can have many tags. Tags enable sub-classification for reporting
(e.g. "flight" vs "taxi" within Public Transport, or "health-insurance"
vs "car-insurance" within Insurance).
"""
import re
import sqlite3
from pathlib import Path
from typing import Optional

import yaml

from etl.models import RawTransaction


class Tagger:
    """Rule-based transaction tagger. Assigns all matching tags."""

    def __init__(self, config_path: Path):
        self.rules = self._load_rules(config_path)

    def get_tags(self, txn: RawTransaction) -> list[str]:
        """Return all tags that match this transaction."""
        desc_upper = txn.description.upper()
        tags = []
        for rule in self.rules:
            source = rule.get("source_type")
            if source and source != txn.source_type:
                continue
            if re.search(rule["pattern"], desc_upper, re.IGNORECASE):
                tags.append(rule["tag"])
        return list(dict.fromkeys(tags))  # dedupe preserving order

    def _load_rules(self, config_path: Path) -> list[dict]:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        return config.get("tag_rules", [])


def insert_tags(conn: sqlite3.Connection, transaction_id: int, tags: list[str]) -> None:
    """Insert tags for a transaction, ignoring duplicates."""
    for tag in tags:
        conn.execute(
            "INSERT OR IGNORE INTO transaction_tags (transaction_id, tag) VALUES (?, ?)",
            (transaction_id, tag),
        )
