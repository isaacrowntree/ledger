"""Every real statement must satisfy the contract.

Synthetic fixtures are what let these defects survive for years: a hand-written
sample encodes the layout the parser already handles. The real archive does not --
it holds every layout each bank has used, including the ones that broke.

The corpus lives in data/archive/ (gitignored, personal). These tests skip when it
is absent, so they run for whoever has the data and never block anyone who does
not.

A statement that cannot account for its own printed balances is a parser bug.
There is deliberately no allowance for expected failures: an exception list is a
place for real defects to settle in and be forgotten, and every failure found so
far turned out to be a genuine bug -- wrong financial years, credits booked as
spending, whole statements read as empty.
"""
from functools import lru_cache
from pathlib import Path

import pytest

from etl.contract import BalanceConvention
from etl.parsers.amex_csv import AmexCSVParser
from etl.parsers.bankwest_csv import BankwestCSVParser
from etl.parsers.bankwest_pdf import BankwestPDFParser
from etl.parsers.coles_csv import ColesCSVParser
from etl.parsers.coles_pdf import ColesCreditPDFParser
from etl.parsers.hsbc_csv import HSBCCSVParser
from etl.parsers.hsbc_pdf import HSBCPDFParser
from etl.parsers.ing_csv import INGCSVParser
from etl.parsers.ing_pdf import INGPDFParser
from etl.parsers.paypal_csv import PayPalCSVParser

ARCHIVE = Path(__file__).resolve().parent.parent / "data" / "archive"

SOURCES = {
    "ing": (INGPDFParser, "*.pdf"),
    "ing-csv": (INGCSVParser, "*.csv"),
    "hsbc": (HSBCPDFParser, "*.pdf"),
    "hsbc-csv": (HSBCCSVParser, "*.csv"),
    "coles": (ColesCreditPDFParser, "*.pdf"),
    "coles-csv": (ColesCSVParser, "*.csv"),
    "bankwest": (BankwestPDFParser, "*.pdf"),
    "bankwest-csv": (BankwestCSVParser, "*.csv"),
    "amex": (AmexCSVParser, "*.csv"),
    "paypal": (PayPalCSVParser, "*.csv"),
}

def _corpus():
    cases = []
    for source, (parser_cls, pattern) in SOURCES.items():
        for path in sorted((ARCHIVE / source).glob(pattern)):
            cases.append(pytest.param(parser_cls, path, id=f"{source}/{path.name}"))
    return cases


CORPUS = _corpus()

if not CORPUS:
    pytest.skip("no statement archive on this machine", allow_module_level=True)


@lru_cache(maxsize=None)
def _parse(parser_cls, path):
    """Parsing a PDF is expensive; each statement is parsed once per session."""
    return parser_cls().parse_statement(path)


@pytest.mark.parametrize("parser_cls,path", CORPUS)
def test_statement_satisfies_the_contract(parser_cls, path):
    statement = _parse(parser_cls, path)
    assert isinstance(statement.balance_convention, BalanceConvention), \
        "a parser must state which way its balances point, even to say 'none'"
    issues = statement.validate()
    assert not issues, "\n".join(f"  {i}" for i in issues)


def test_every_source_is_represented():
    """Guards against a source silently dropping out of the corpus."""
    covered = {case.id.split("/")[0] for case in CORPUS}
    missing = set(SOURCES) - covered
    assert missing <= {"hsbc-csv", "airbnb"}, f"no corpus for: {missing}"
