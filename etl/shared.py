"""Rule-driven shared expenses.

Household bills split with a partner were marked by hand, one transaction at a
time, which is how ten weeks of them silently went unshared. These rules mark
them at ingest, alongside categories and tags.

Three properties matter, and the tests pin all three:

* **A rule never overrides a manual decision.** If a transaction already has a
  shared entry, the rule leaves it alone -- including one deliberately set to a
  different split, or deliberately removed.
* **A rule can start from a date.** Turning one on must not reach back into
  periods already settled.
* **A failed payment is not an expense.** A "Force Post No Funds" that is
  reversed never took the money, and the real payment lands days later; sharing
  both charge the partner twice. `exclude:` keeps those out.
* **Inclusion follows the merchant, not the category.** Optus is a utility that
  has never been shared; fuel is shared on a road trip but not at the local
  servo. Category-based rules would get both wrong.
"""
import re
import sqlite3
from pathlib import Path

import yaml

from etl.models import RawTransaction


class SharedRules:
    """Decides whether a transaction is a shared expense, and at what split."""

    def __init__(self, config_path: Path | None = None, rules: list[dict] | None = None):
        if rules is not None:
            self.rules = rules
        elif config_path is not None:
            self.rules = self._load_rules(config_path)
        else:
            self.rules = []

    def split_for(self, txn: RawTransaction) -> float | None:
        """The split percentage for this transaction, or None if not shared."""
        # A refund or credit from a shared merchant is not an expense to split.
        if txn.amount >= 0:
            return None

        description = txn.description.upper()
        for rule in self.rules:
            pattern = rule.get("pattern")
            if not pattern or not re.search(pattern, description, re.IGNORECASE):
                continue
            excluded = rule.get("exclude")
            if excluded and re.search(excluded, description, re.IGNORECASE):
                continue   # e.g. a failed direct debit that was reversed
            starts = rule.get("from")
            if starts and txn.date < str(starts):
                continue   # rule not yet in force; leave settled history alone
            ends = rule.get("until")
            if ends and txn.date > str(ends):
                continue
            return rule.get("split_pct", 50)
        return None

    @staticmethod
    def _load_rules(config_path: Path) -> list[dict]:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        return config.get("shared_rules", [])


def mark_shared(conn: sqlite3.Connection, transaction_id: int, split_pct: float) -> bool:
    """Record a shared expense, unless the transaction already has one.

    Returns True if a row was created. Never updates an existing entry: that
    entry represents a decision already made, possibly a manual one, and a rule
    must not overwrite it.
    """
    existing = conn.execute(
        "SELECT 1 FROM shared_expenses WHERE transaction_id = ?", (transaction_id,)
    ).fetchone()
    if existing:
        return False
    conn.execute(
        "INSERT INTO shared_expenses (transaction_id, split_pct, is_settled) VALUES (?, ?, 0)",
        (transaction_id, split_pct),
    )
    return True
