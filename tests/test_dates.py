"""Resolving the year a statement leaves off.

Statement PDFs print "28 Jun" and expect you to know the year. Getting it wrong
moves a transaction into a different FINANCIAL year, which silently changes
figures in a lodged tax return -- so this is resolved from the statement's own
period rather than guessed from a year found elsewhere on the page.
"""
import pytest

from etl.parsers.dates import StatementPeriod, parse_period, resolve_year


class TestResolveYear:
    def test_date_inside_the_period_takes_that_year(self):
        period = StatementPeriod("2020-06-06", "2020-07-06")
        assert resolve_year(28, 6, period) == 2020
        assert resolve_year(5, 7, period) == 2020

    def test_december_on_a_january_statement_is_the_previous_year(self):
        period = StatementPeriod("2025-12-03", "2026-01-05")
        assert resolve_year(15, 12, period) == 2025
        assert resolve_year(3, 1, period) == 2026

    def test_june_july_boundary(self):
        """The case that was wrong: a period spanning a mid-year month change."""
        period = StatementPeriod("2019-05-15", "2019-07-14")
        assert resolve_year(21, 6, period) == 2019
        assert resolve_year(12, 7, period) == 2019

    def test_a_stray_year_elsewhere_on_the_page_cannot_win(self):
        """The old code scanned the first 30 lines for any 20xx and used it."""
        period = StatementPeriod("2020-06-06", "2020-07-06")
        assert resolve_year(28, 6, period) != 2019

    def test_leap_day_resolves(self):
        period = StatementPeriod("2024-02-01", "2024-03-01")
        assert resolve_year(29, 2, period) == 2024

    def test_leap_day_on_a_non_leap_period_falls_back(self):
        """29 Feb cannot exist in the period's year; must not raise."""
        period = StatementPeriod("2023-02-01", "2023-03-01")
        assert resolve_year(29, 2, period) in (2023, 2024, None)

    def test_date_just_outside_the_period_uses_the_closest_year(self):
        """A posting a few days past the period end still belongs to that year."""
        period = StatementPeriod("2025-12-03", "2026-01-05")
        assert resolve_year(8, 1, period) == 2026

    def test_no_period_returns_none(self):
        """Without a period there is nothing to resolve against; say so rather
        than inventing a year."""
        assert resolve_year(28, 6, None) is None


class TestParsePeriod:
    def test_bankwest_format(self):
        p = parse_period("From 03 Dec 2025 to 05 Jan 2026")
        assert (p.start, p.end) == ("2025-12-03", "2026-01-05")

    def test_hsbc_two_digit_years(self):
        p = parse_period("4000 1234 5678 9010 6 Jun 20 to 6 Jul 20 19.99% p.a.")
        assert (p.start, p.end) == ("2020-06-06", "2020-07-06")

    def test_coles_slash_format(self):
        p = parse_period("Statement Period 15/05/19 - 14/07/19")
        assert (p.start, p.end) == ("2019-05-15", "2019-07-14")

    def test_coles_month_name_format(self):
        p = parse_period("Account Number Statement Period 15 Jul 26 to 14 Aug 26")
        assert (p.start, p.end) == ("2026-07-15", "2026-08-14")

    def test_period_spanning_new_year(self):
        p = parse_period("15/12/24 - 14/01/25")
        assert (p.start, p.end) == ("2024-12-15", "2025-01-14")

    def test_no_period_in_text(self):
        assert parse_period("nothing here") is None

    def test_ignores_a_later_unrelated_date_range(self):
        """The first period on the page is the statement's own."""
        text = ("Statement Period 15 Jul 26 to 14 Aug 26\n"
                "Promotional rate 01 Jan 20 to 01 Jan 21")
        p = parse_period(text)
        assert (p.start, p.end) == ("2026-07-15", "2026-08-14")
