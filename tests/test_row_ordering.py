"""Putting statement rows into the order the engine requires.

The contract requires chronological rows, because a running balance only reads
forwards. How a parser gets there depends on what the source prints:

* newest-first export -> reverse it (preserves same-day order)
* rows grouped by section (purchases, then payments) -> sort by date, but ONLY
  when there is no running balance, since sorting would destroy the chain
* rows carrying a balance and out of order -> leave alone and let validation
  report it, rather than silently corrupting the chain
"""
from etl.parsers.base import chronological


class _Txn:
    def __init__(self, date, balance=None, tag=""):
        self.date = date
        self.raw_data = {"balance": balance} if balance is not None else {}
        self.tag = tag

    def __repr__(self):
        return f"{self.date}{self.tag}"


def dates(rows):
    return [r.date for r in rows]


class TestChronological:
    def test_already_chronological_is_untouched(self):
        rows = [_Txn("2026-01-01"), _Txn("2026-01-02")]
        assert dates(chronological(rows)) == ["2026-01-01", "2026-01-02"]

    def test_newest_first_is_reversed(self):
        rows = [_Txn("2026-01-03"), _Txn("2026-01-02"), _Txn("2026-01-01")]
        assert dates(chronological(rows)) == ["2026-01-01", "2026-01-02", "2026-01-03"]

    def test_reversing_preserves_same_day_order(self):
        """Same-day order is what makes a balance chain resolvable."""
        rows = [_Txn("2026-01-02", tag="b"), _Txn("2026-01-02", tag="a"),
                _Txn("2026-01-01")]
        assert [r.tag for r in chronological(rows)] == ["", "a", "b"]

    def test_section_grouped_rows_are_sorted_when_no_balance(self):
        """A card statement listing purchases then payments is neither ascending
        nor descending; with no balance to preserve, sorting is safe."""
        rows = [_Txn("2026-01-05"), _Txn("2026-01-20"), _Txn("2026-01-02")]
        assert dates(chronological(rows)) == ["2026-01-02", "2026-01-05", "2026-01-20"]

    def test_sorting_is_stable_for_equal_dates(self):
        rows = [_Txn("2026-01-05", tag="x"), _Txn("2026-01-01"),
                _Txn("2026-01-05", tag="y")]
        assert [r.tag for r in chronological(rows)] == ["", "x", "y"]

    def test_rows_with_balances_are_never_sorted(self):
        """Sorting would scramble a running balance into nonsense. Leave it and
        let validation report the disorder instead."""
        rows = [_Txn("2026-01-05", balance="50.00"),
                _Txn("2026-01-20", balance="30.00"),
                _Txn("2026-01-02", balance="10.00")]
        assert dates(chronological(rows)) == ["2026-01-05", "2026-01-20", "2026-01-02"]

    def test_descending_rows_with_balances_are_still_reversed(self):
        """Reversal is safe for a chain; only sorting is not."""
        rows = [_Txn("2026-01-03", balance="10.00"), _Txn("2026-01-01", balance="30.00")]
        assert dates(chronological(rows)) == ["2026-01-01", "2026-01-03"]

    def test_empty_and_single_row(self):
        assert chronological([]) == []
        assert dates(chronological([_Txn("2026-01-01")])) == ["2026-01-01"]
