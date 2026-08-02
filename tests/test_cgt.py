"""Tests for FIFO parcel matching and capital gains."""
from datetime import date

import pytest

from etl.cgt import CGTError, match_disposals, summarise
from etl.parsers.commsec_pdf import BUY, SELL, Trade


def trade(side, code, day, units, consideration, brokerage=0.0):
    return Trade(
        side=side, code=code, name=code, trade_date=day, units=units,
        consideration=consideration, brokerage=brokerage,
        confirmation_no="1", settlement_date=None, source_file="note.pdf",
    )


def buy(code, day, units, consideration, brokerage=0.0):
    return trade(BUY, code, day, units, consideration, brokerage)


def sell(code, day, units, consideration, brokerage=0.0):
    return trade(SELL, code, day, units, consideration, brokerage)


class TestFIFOMatching:
    def test_oldest_parcel_is_consumed_first(self):
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 1000.0),
            buy("AAA", date(2021, 1, 1), 100, 2000.0),
            sell("AAA", date(2023, 1, 1), 100, 3000.0),
        ])
        assert len(events) == 1
        assert events[0].acquired == date(2020, 1, 1)
        assert events[0].cost_base == 1000.0

    def test_disposal_spanning_parcels_is_split(self):
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 1000.0),
            buy("AAA", date(2021, 1, 1), 100, 2000.0),
            sell("AAA", date(2023, 1, 1), 150, 4500.0),
        ])
        assert [e.units for e in events] == [100, 50]
        # Proceeds apportion per unit and must sum back to the disposal.
        assert round(sum(e.proceeds for e in events), 2) == 4500.0
        assert [e.cost_base for e in events] == [1000.0, 1000.0]

    def test_securities_are_matched_independently(self):
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 1000.0),
            buy("BBB", date(2021, 1, 1), 100, 5000.0),
            sell("BBB", date(2023, 1, 1), 100, 6000.0),
        ])
        assert len(events) == 1
        assert events[0].code == "BBB"
        assert events[0].cost_base == 5000.0

    def test_selling_more_than_held_is_an_error(self):
        """Better to refuse than to invent a cost base of zero."""
        with pytest.raises(CGTError, match="contract note is missing"):
            match_disposals([
                buy("AAA", date(2020, 1, 1), 50, 500.0),
                sell("AAA", date(2023, 1, 1), 100, 1500.0),
            ])

    def test_brokerage_cuts_the_gain_at_both_ends(self):
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 1000.0, brokerage=20.0),
            sell("AAA", date(2023, 1, 1), 100, 1500.0, brokerage=30.0),
        ])
        assert events[0].cost_base == 1020.0
        assert events[0].proceeds == 1470.0
        assert events[0].gain == 450.0


class TestDiscountEligibility:
    def test_held_more_than_twelve_months_qualifies(self):
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 1000.0),
            sell("AAA", date(2021, 1, 2), 100, 1500.0),
        ])
        assert events[0].discountable is True

    def test_held_exactly_twelve_months_does_not_qualify(self):
        """The event must fall more than 12 months after acquisition."""
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 1000.0),
            sell("AAA", date(2021, 1, 1), 100, 1500.0),
        ])
        assert events[0].discountable is False

    def test_a_loss_is_never_discountable(self):
        events = match_disposals([
            buy("AAA", date(2018, 1, 1), 100, 2000.0),
            sell("AAA", date(2023, 1, 1), 100, 1000.0),
        ])
        assert events[0].gain == -1000.0
        assert events[0].discountable is False

    def test_leap_day_acquisition(self):
        events = match_disposals([
            buy("AAA", date(2020, 2, 29), 100, 1000.0),
            sell("AAA", date(2021, 3, 1), 100, 1500.0),
        ])
        assert events[0].discountable is True


class TestFinancialYear:
    @pytest.mark.parametrize("disposed,fy", [
        (date(2024, 8, 1), 2025),    # after 30 June -> next FY
        (date(2025, 6, 30), 2025),   # on 30 June -> same FY
        (date(2025, 7, 1), 2026),
    ])
    def test_fy_boundary(self, disposed, fy):
        events = match_disposals([
            buy("AAA", date(2019, 1, 1), 100, 1000.0),
            sell("AAA", disposed, 100, 1500.0),
        ])
        assert events[0].financial_year == fy

    def test_summary_only_counts_the_year_asked_for(self):
        events = match_disposals([
            buy("AAA", date(2019, 1, 1), 200, 2000.0),
            sell("AAA", date(2024, 8, 1), 100, 1500.0),
            sell("AAA", date(2025, 8, 1), 100, 1500.0),
        ])
        assert summarise(events, 2025)["gross_gains"] == 500.0
        assert summarise(events, 2026)["gross_gains"] == 500.0


class TestSummary:
    def test_discount_halves_an_eligible_gain(self):
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 1000.0),
            sell("AAA", date(2024, 8, 1), 100, 3000.0),
        ])
        s = summarise(events, 2025)
        assert s["gross_gains"] == 2000.0
        assert s["discount"] == 1000.0
        assert s["net_capital_gain"] == 1000.0

    def test_losses_applied_to_undiscounted_gains_first(self):
        """A dollar of loss is worth a whole dollar there, but only fifty
        cents against a gain that is about to be halved."""
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 1000.0),
            sell("AAA", date(2024, 8, 1), 100, 3000.0),      # +2000 discountable
            buy("BBB", date(2024, 1, 1), 100, 1000.0),
            sell("BBB", date(2024, 8, 1), 100, 1500.0),      # +500 not discountable
            buy("CCC", date(2024, 1, 1), 100, 2000.0),
            sell("CCC", date(2024, 8, 1), 100, 1500.0),      # -500 loss
        ])
        s = summarise(events, 2025)
        assert s["other_gains"] == 500.0
        assert s["losses"] == 500.0
        # The loss cancels the undiscounted gain, leaving the discounted one whole.
        assert s["net_capital_gain"] == 1000.0

    def test_carried_forward_losses_are_applied(self):
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 1000.0),
            sell("AAA", date(2024, 8, 1), 100, 3000.0),
        ])
        s = summarise(events, 2025, carried_forward_losses=500.0)
        assert s["net_capital_gain"] == 750.0

    def test_surplus_losses_carry_forward(self):
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 2000.0),
            sell("AAA", date(2024, 8, 1), 100, 1000.0),
        ])
        s = summarise(events, 2025)
        assert s["net_capital_gain"] == 0.0
        assert s["losses_carried_forward"] == 1000.0

    def test_net_gain_never_negative(self):
        events = match_disposals([
            buy("AAA", date(2020, 1, 1), 100, 5000.0),
            sell("AAA", date(2024, 8, 1), 100, 1000.0),
        ])
        assert summarise(events, 2025)["net_capital_gain"] == 0.0

    def test_empty_year(self):
        s = summarise([], 2025)
        assert s["net_capital_gain"] == 0.0 and s["events"] == []
