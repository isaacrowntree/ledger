"""Reduce a bank description to the merchant it names.

Statement descriptions carry noise that changes on every transaction: receipt
numbers, dates, card digits, reference codes, foreign-currency amounts and the
suburb of the terminal. Two visits to the same café then never share a
description, so nothing learned from one applies to the next. The merchant key
strips that noise so the same merchant always gives the same key.

    >>> merchant_key("Visa Purchase - Receipt 100001 SOME CAFE EXAMPLETOWN Date 24/11/24 Card 1234")
    'SOME CAFE EXAMPLETOWN'
    >>> merchant_key("WOOLWORTHS      0101     EXAMPLETOWN       NS")
    'WOOLWORTHS'
"""
import re

# Fixed-width card statements (Bankwest, Amex) pad merchant, suburb and
# reference into columns. The first column is the merchant.
_COLUMN_GAP = re.compile(r"\s{3,}")

_NOISE = [
    re.compile(r"\bRECEIPT\s+\d+"),
    re.compile(r"\bDATE\s+\d{1,2}/\d{1,2}/\d{2,4}"),
    re.compile(r"\bCARD\s+X*\d+"),
    re.compile(r"\(\s*[\d.,]+\s+[A-Z]{3}\s*\)"),      # (100.00 USD)
    re.compile(r"\$[\d.,]+"),                          # Transaction $12.50
    # Reference codes: a token of 6+ letters/digits with at least two digits
    # (HMAB12CD34, A-7WNQN3SG). One digit is a name: 7-ELEVEN, 3M.
    re.compile(r"(?<![A-Z0-9])(?=(?:[A-Z-]*\d){2})[A-Z0-9-]{6,}(?![A-Z0-9])"),
    re.compile(r"\d{3,}"),
]

# How the bank says it was paid, not who was paid. "VISA PURCHASE X" and
# "DIRECT DEBIT X" are the same merchant X. Money in or out is kept separately.
_TYPE_PREFIX = re.compile(
    r"^(VISA PURCHASE|VISA REFUND|EFTPOS PURCHASE|EFTPOS REFUND|INTL ATMPURCHASE|"
    r"DIRECT DEBIT|OSKO PAYMENT|OSKO DEPOSIT|DEPOSIT|BPAY BILL PAYMENT|PAY ANYONE)\s+")

# Trailing place words that say where the terminal was, not who was paid.
_PLACE = {
    "NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT", "NS", "VI", "QL",
    "AU", "AUS", "AUSTRALIA", "NZ", "NZL", "GB", "GBR", "US", "USA", "NL",
    "SYDNEY", "MELBOURNE", "BRISBANE", "PERTH", "ADELAIDE", "HOBART",
    "CANBERRA", "DARWIN", "LONDON",
}


def merchant_key(description: str) -> str:
    s = description.upper().strip()
    first = _COLUMN_GAP.split(s, maxsplit=1)[0]
    if len(re.sub(r"[^A-Z]", "", first)) >= 3:
        s = first
    for pattern in _NOISE:
        s = pattern.sub(" ", s)
    s = re.sub(r"[^A-Z0-9&' ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    stripped = _TYPE_PREFIX.sub("", s)
    if stripped:
        s = stripped
    words = s.split()
    while len(words) > 1 and words[-1] in _PLACE:
        words.pop()
    return " ".join(words)


def direction(amount: float) -> str:
    """Money in or out. A merchant's refunds are kept apart from its charges."""
    return "in" if amount > 0 else "out"
