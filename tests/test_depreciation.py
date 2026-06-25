"""Tests for the depreciation asset register loader (etl.depreciation)."""
from pathlib import Path

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


def test_rental_register_applies_ownership_share():
    reg = _rental(load_depreciation(FIXTURE))
    assert reg["ownership_pct"] == 50
    # Full-property decline 200; 50% owner deducts 100.
    assert reg["totals"][2025]["deductible"] == 200.00
    assert reg["totals"][2025]["taxpayer_deductible"] == 100.00


def test_fy_totals_across_all_registers():
    data = load_depreciation(FIXTURE)
    # full: business 1010 + rental 200 = 1210
    assert data["fy_totals"][2025]["deductible"] == 1210.00
    # taxpayer: business 1010 + rental 100 = 1110
    assert data["fy_totals"][2025]["taxpayer_deductible"] == 1110.00


def test_totals_keys_sorted():
    reg = _biz(load_depreciation(FIXTURE))
    assert list(reg["totals"].keys()) == [2024, 2025]


def test_missing_file_yields_empty():
    data = load_depreciation(Path("/nonexistent/depreciation.yaml"))
    assert data == {"registers": [], "fy_totals": {}}
