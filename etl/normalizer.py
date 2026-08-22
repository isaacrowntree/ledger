"""Account configuration used by the ingestion engine.

Identity, idempotency and validation moved to etl/engine.py, which owns them for
every source. What remains here is the source-of-truth configuration: which
accounts hold the authoritative detail, and which descriptions on other accounts
are payments TO them and therefore transfers rather than spending.
"""
import re
from pathlib import Path

import yaml

from etl.models import RawTransaction

def load_payment_patterns(config_path: Path) -> tuple[list[re.Pattern], set[str]]:
    """Load compiled payment patterns and source-of-truth source types from config.

    Returns (patterns, source_of_truth_types).
    Any transaction from a non-source-of-truth account whose description
    matches a pattern is a payment TO the source-of-truth account and
    should be marked as a transfer.
    """
    with open(config_path) as f:
        config = yaml.safe_load(f)
    patterns = []
    sot_types = set()
    for acct in config.get("accounts", []):
        if acct.get("source_of_truth"):
            sot_types.add(acct["source_type"])
            for p in acct.get("payment_patterns", []):
                patterns.append(re.compile(p, re.IGNORECASE))
    return patterns, sot_types


def is_payment_to_source_of_truth(txn: RawTransaction, patterns: list[re.Pattern], source_of_truth_types: set[str]) -> bool:
    """Check if a transaction is a payment to a source-of-truth account.

    Only applies to non-source-of-truth source types (e.g. ING).
    Source-of-truth accounts' own transactions are never suppressed.
    """
    if txn.source_type in source_of_truth_types:
        return False

    desc_upper = txn.description.upper()
    return any(p.search(desc_upper) for p in patterns)


def _balance_key(raw_data: dict) -> str | None:
    """The running balance after this transaction, normalized, or None.

    ING writes it as "$673.88" on interim statements and "673.88" on quarterly
    ones; normalizing lets the same transaction hash identically whichever
    statement format it arrived in.

    Only a true per-record running balance counts. Bankwest/HSBC/Coles carry a
    "closing_balance" that is constant across every row of a statement -- that
    is the statement's closing figure, so it identifies the file rather than the
    transaction and must not be used here.
    """
    for key in ("balance",):
        value = raw_data.get(key)
        if value in (None, "", "None"):
            continue
        cleaned = str(value).replace("$", "").replace(",", "").strip()
        try:
            return f"{float(cleaned):.2f}"
        except ValueError:
            continue
    return None
