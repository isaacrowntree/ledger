"""Tests for the depreciation asset register loader (etl.depreciation)."""
from pathlib import Path

import pytest

from etl.depreciation import load_depreciation

FIXTURE = Path(__file__).parent / "fixtures" / "depreciation.yaml"


def _biz(data):
    return next(r for r in data["registers"] if r["owner"] == "Test Business")


def _rental(data):
    return next(r for r in data["registers"] if r["owner"] == "Test Rental")


def test_loads_registers_and_assets():
    data = load_depreciation(FIXTURE)
    assert len(data["registers"]) == 2
    assert len(_biz(data)["assets"]) == 2


def test_per_fy_totals_sum_deductible_decline():
    reg = _biz(load_depreciation(FIXTURE))
    # Loader keeps integer FY keys (JSON stringifies them only over HTTP).
    # FY2024: only the camera was held (100)
    assert reg["totals"][2024]["deductible"] == 100.00
    assert reg["totals"][2024]["n_assets"] == 1
    # FY2025: camera 760 + laptop 250 = 1010, across 2 assets
    assert reg["totals"][2025]["deductible"] == 1010.00
    assert reg["totals"][2025]["n_assets"] == 2


def test_business_register_full_deduction_when_100pct():
    reg = _biz(load_depreciation(FIXTURE))
    assert reg["ownership_pct"] == 100
    assert reg["totals"][2025]["taxpayer_deductible"] == 1010.00


def test_rental_register_keeps_decline_deductible_and_share_distinct():
    reg = _rental(load_depreciation(FIXTURE))
    assert reg["ownership_pct"] == 50
    t = reg["totals"][2025]
    # Fixture has decline=400, deductible=300 → these must NOT be conflated, and
    # the taxpayer share is deductible*0.5 (150), NOT decline*0.5 (200).
    assert t["decline"] == 400.00
    assert t["deductible"] == 300.00
    assert t["taxpayer_deductible"] == 150.00


def test_fy_totals_across_all_registers():
    data = load_depreciation(FIXTURE)
    fy = data["fy_totals"][2025]
    # decline: business 1010 + rental 400 = 1410
    assert fy["decline"] == 1410.00
    # full deductible: business 1010 + rental 300 = 1310
    assert fy["deductible"] == 1310.00
    # taxpayer deductible: business 1010 + rental 150 = 1160
    assert fy["taxpayer_deductible"] == 1160.00


def test_totals_keys_sorted():
    reg = _biz(load_depreciation(FIXTURE))
    assert list(reg["totals"].keys()) == [2024, 2025]


def test_missing_file_yields_empty():
    data = load_depreciation(Path("/nonexistent/depreciation.yaml"))
    assert data == {"registers": [], "fy_totals": {}}


# --- Robustness against hand-edited YAML ---

def test_null_ownership_pct_defaults_to_full(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text(
        "registers:\n  - owner: X\n    kind: business\n    ownership_pct:\n"
        "    assets:\n      - description: A\n        years:\n"
        "          - { fy: 2025, decline: 100, deductible: 100, closing: 0 }\n"
    )
    reg = load_depreciation(p)["registers"][0]
    assert reg["ownership_pct"] == 100
    assert reg["totals"][2025]["taxpayer_deductible"] == 100.00


def test_year_row_missing_fy_is_skipped_not_fatal(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text(
        "registers:\n  - owner: X\n    kind: business\n    assets:\n"
        "      - description: A\n        years:\n"
        "          - { decline: 50, deductible: 50 }\n"
        "          - { fy: 2025, decline: 100, deductible: 100, closing: 0 }\n"
    )
    reg = load_depreciation(p)["registers"][0]
    assert list(reg["totals"].keys()) == [2025]
    assert reg["totals"][2025]["deductible"] == 100.00


def test_non_dict_top_level_yields_empty(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text("- just\n- a\n- list\n")
    assert load_depreciation(p) == {"registers": [], "fy_totals": {}}
