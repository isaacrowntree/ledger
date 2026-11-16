"""Assets without contract notes, and net capital losses carried between years."""
from datetime import date

import pytest

from etl.cgt import (CGTError, capital_gains_for, events_from_config, losses_brought_forward,
                     lodged_losses_carried_forward, unrecorded_losses)

GOLD = {"asset": "Gold", "acquired": "2020-08-03", "disposed": "2021-03-10",
        "cost_base": 12000, "proceeds": 10000}           # 2,000 loss in FY2021
LODGED = [{"fy": 2020, "carry_forward": []},
          {"fy": 2021, "carry_forward": []},             # the loss never reached the return
          {"fy": 2024, "carry_forward": [{"label": "18V", "value": 900}, {"label": "P9", "value": 50}]},
          {"fy": 2025, "carry_forward": []}]             # 900 dropped


def test_config_events_become_cgt_events():
    [e] = events_from_config([GOLD])
    assert (e.code, e.acquired, e.gain, e.financial_year) == ("Gold", date(2020, 8, 3), -2000.0, 2021)
    with pytest.raises(CGTError, match="missing proceeds"):
        events_from_config([{k: v for k, v in GOLD.items() if k != "proceeds"}])
    with pytest.raises(CGTError, match="before it was acquired"):
        events_from_config([{**GOLD, "disposed": "2020-01-01"}])


def test_lodged_18v_only_and_a_lodged_year_without_it_is_zero():
    assert lodged_losses_carried_forward(LODGED) == {2020: 0, 2021: 0, 2024: 900, 2025: 0}


def test_losses_roll_forward_from_the_latest_known_balance():
    events = events_from_config([GOLD])
    # No anchor: rolled from the first event.
    assert losses_brought_forward(2023, events, {}) == {"amount": 2000.0, "from_fy": None}
    # An anchor wins over earlier history; later gains use the balance up.
    gain = events_from_config([{**GOLD, "asset": "Shares", "acquired": "2024-08-01",
                                "disposed": "2025-03-01", "cost_base": 100, "proceeds": 400}])
    assert losses_brought_forward(2026, events + gain, {2024: 900}) == {"amount": 600.0, "from_fy": 2024}


def test_a_correction_beats_the_lodged_figure_and_both_gaps_are_flagged(tmp_path):
    out = capital_gains_for(2026, tmp_path / "none", {"cgt_events": [GOLD],
                                                      "capital_losses_carried_forward": {2025: 900}}, LODGED)
    assert (out["brought_forward"], out["brought_forward_from_fy"]) == (900.0, 2025)
    assert out["unrecorded_losses"] == [
        {"fy": 2021, "lodged": 0, "ledger": 2000.0, "corrected": None},
        {"fy": 2025, "lodged": 0, "ledger": 900.0, "corrected": 900.0}]


def test_years_the_ledger_knows_less_about_are_not_flagged():
    # The ledger lacks whatever made FY2024's 900 loss: a shortfall the other way isn't reported.
    assert unrecorded_losses([], {2024: 900}, {}, 2026) == []
