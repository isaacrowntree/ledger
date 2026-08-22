"""Tests for totalling manually entered tax figures."""
from etl.manual_entries import summarise, tax_withheld


class TestSectionTotals:
    def test_sums_each_section_separately(self):
        totals = summarise([
            {"label": "Dividends", "amount": 66.24, "section": "income"},
            {"label": "Franking credits", "amount": 28.39, "section": "income"},
            {"label": "Donation", "amount": -689.00, "section": "deductions"},
            {"label": "Agent rent", "amount": 24934.14, "section": "rental"},
        ])
        assert totals == {"income": 94.63, "deductions": 689.00, "rental": 24934.14}

    def test_rental_income_and_fees_net_out(self):
        """An agent's statement lists gross rent and the fees taken from it."""
        totals = summarise([
            {"label": "Rent", "amount": 24934.14, "section": "rental"},
            {"label": "Management fee", "amount": -1645.63, "section": "rental"},
            {"label": "Council rates", "amount": -1969.35, "section": "rental"},
        ])
        assert totals["rental"] == 21319.16

    def test_deductions_counted_by_magnitude(self):
        """A deduction reduces tax whichever sign it is written with."""
        assert summarise([{"amount": -689.0, "section": "deductions"}])["deductions"] == 689.0
        assert summarise([{"amount": 689.0, "section": "deductions"}])["deductions"] == 689.0

    def test_unknown_section_is_display_only(self):
        totals = summarise([
            {"label": "Note to self", "amount": 5000.0, "section": "notes"},
            {"label": "No section", "amount": 5000.0},
        ])
        assert totals == {"income": 0.0, "deductions": 0.0, "rental": 0.0}

    def test_section_matching_is_forgiving(self):
        assert summarise([{"amount": 10.0, "section": "  Income "}])["income"] == 10.0

    def test_empty_and_missing_input(self):
        empty = {"income": 0.0, "deductions": 0.0, "rental": 0.0}
        assert summarise([]) == empty
        assert summarise(None) == empty


class TestRobustness:
    def test_unusable_amount_is_skipped_not_fatal(self):
        """Hand-edited YAML shouldn't be able to break the whole return."""
        totals = summarise([
            {"label": "Typo", "amount": "N/A", "section": "income"},
            {"label": "Blank", "amount": None, "section": "income"},
            {"label": "Good", "amount": 100.0, "section": "income"},
        ])
        assert totals["income"] == 100.0

    def test_numeric_string_is_accepted(self):
        assert summarise([{"amount": "250.50", "section": "income"}])["income"] == 250.50

    def test_non_dict_entry_ignored(self):
        assert summarise(["nonsense", None])["income"] == 0.0


class TestTaxWithheld:
    def test_tax_withheld_extracted(self):
        entries = [{"label": "Tax withheld", "amount": 12000.0}]
        assert tax_withheld(entries) == 12000.0

    def test_tax_withheld_excluded_from_sections(self):
        """It is the PAYG credit, not income — counting it as both would
        inflate assessable income and the refund at the same time."""
        entries = [{"label": "Tax withheld", "amount": 12000.0, "section": "income"}]
        assert summarise(entries)["income"] == 0.0
        assert tax_withheld(entries) == 12000.0

    def test_no_tax_withheld_entry(self):
        assert tax_withheld([{"label": "Dividends", "amount": 50.0}]) == 0.0
