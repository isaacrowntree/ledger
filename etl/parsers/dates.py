"""Resolving the year a statement leaves off its transaction lines.

Statement PDFs print "28 Jun" and expect the reader to supply the year. Supplying
the wrong one moves a transaction into a different FINANCIAL year, quietly
changing figures in a return that may already be lodged -- so the year is resolved
from the statement's own printed period, never from a year found loose on the page.

The rule: pick the year that places the date inside the period. That handles a
Dec-Jan statement (December belongs to the earlier year) and a mid-year boundary
alike, without any special-casing of particular months.
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


@dataclass(frozen=True)
class StatementPeriod:
    start: str   # YYYY-MM-DD
    end: str     # YYYY-MM-DD

    @property
    def start_date(self) -> date:
        return date.fromisoformat(self.start)

    @property
    def end_date(self) -> date:
        return date.fromisoformat(self.end)


def _full_year(value: str) -> int:
    year = int(value)
    return year if year > 100 else 2000 + year


def _iso(day: int, month: int, year: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


# "03 Dec 2025 to 05 Jan 2026" / "6 Jun 20 to 6 Jul 20" / "15 Jul 26 - 14 Aug 26"
_NAMED = re.compile(
    r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4}|\d{2})\s*(?:to|-|–|—)\s*"
    r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4}|\d{2})", re.I)

# "15/05/19 - 14/07/19" / "15/12/2024 to 14/01/2025"
_NUMERIC = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\s*(?:to|-|–|—)\s*"
    r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})")


def parse_period(text: str) -> StatementPeriod | None:
    """The statement's own period, or None if it does not print one.

    Takes the FIRST range on the page: a statement prints its own period in the
    header, while anything later (a promotional rate window, say) is not it.
    """
    candidates = []

    m = _NAMED.search(text)
    if m:
        d1, mon1, y1, d2, mon2, y2 = m.groups()
        if mon1.lower() in MONTHS and mon2.lower() in MONTHS:
            candidates.append((m.start(), _iso(int(d1), MONTHS[mon1.lower()], _full_year(y1)),
                               _iso(int(d2), MONTHS[mon2.lower()], _full_year(y2))))

    m = _NUMERIC.search(text)
    if m:
        d1, mon1, y1, d2, mon2, y2 = m.groups()
        candidates.append((m.start(), _iso(int(d1), int(mon1), _full_year(y1)),
                           _iso(int(d2), int(mon2), _full_year(y2))))

    if not candidates:
        return None

    _, start, end = min(candidates)          # whichever appears first on the page
    if end < start:
        return None
    return StatementPeriod(start, end)


def resolve_year(day: int, month: int, period: StatementPeriod | None) -> int | None:
    """Which year puts this day/month inside the statement period.

    Returns None when there is no period to resolve against -- the caller must
    then decide, rather than this function inventing a year.
    """
    if period is None:
        return None

    end_year = period.end_date.year
    best = None

    for candidate in (end_year, end_year - 1, end_year + 1):
        if month == 2 and day == 29 and not calendar.isleap(candidate):
            continue
        try:
            when = date(candidate, month, day)
        except ValueError:
            continue

        if period.start_date <= when <= period.end_date:
            return candidate

        # Not inside: keep the closest, for a posting that falls just outside.
        distance = min(abs((when - period.start_date).days),
                       abs((when - period.end_date).days))
        if best is None or distance < best[0]:
            best = (distance, candidate)

    return best[1] if best else None
