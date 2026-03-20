import re
import sqlite3
from pathlib import Path
from typing import Optional

import yaml

from etl.models import RawTransaction
from etl import db


class Categorizer:
    """Rule-based transaction categorizer."""

    def __init__(self, conn: sqlite3.Connection, config_path: Path):
        self.conn = conn
        self.rules = self._load_rules(config_path)

    def categorize(self, txn: RawTransaction) -> tuple[Optional[int], Optional[float]]:
        """
        Return (category_id, confidence) for a transaction.
        Tries rules in priority order: source → exact → regex → contains → learned → uncategorized.
        """
        desc_upper = txn.description.upper()

        # 1. Source-based rules
        for rule in self.rules:
            if rule["type"] == "source" and txn.source_type == rule["source_type"]:
                cat_id = db.get_category_id(self.conn, rule["category"])
                if cat_id:
                    return cat_id, 1.0

        # 2. Exact match
        for rule in self.rules:
            if rule["type"] == "exact" and rule["pattern"].upper() in desc_upper:
                cat_id = db.get_category_id(self.conn, rule["category"])
                if cat_id:
                    return cat_id, 0.9

        # 3. Regex match (rules with source_type filter are checked against txn.source_type)
        for rule in self.rules:
            if rule["type"] == "regex":
                rule_source = rule.get("source_type")
                if rule_source and rule_source != txn.source_type:
                    continue
                if re.search(rule["pattern"], desc_upper, re.IGNORECASE):
                    cat_id = db.get_category_id(self.conn, rule["category"])
                    if cat_id:
                        return cat_id, 0.8

        # 4. Learned rules from DB
        row = self.conn.execute(
            "SELECT category_id FROM category_rules_learned WHERE description_pattern = ?",
            (desc_upper,),
        ).fetchone()
        if row:
            return row["category_id"], 0.7

        # 5. Uncategorized
        cat_id = db.get_category_id(self.conn, "Uncategorized")
        return cat_id, 0.0

    def _load_rules(self, config_path: Path) -> list[dict]:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        return config.get("rules", [])
