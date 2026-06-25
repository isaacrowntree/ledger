"""Tests for the lodged ATO returns archive loader (etl.ato_returns)."""
from pathlib import Path

from etl.ato_returns import load_ato_returns

FIXTURE_DIR = Path(__file__).parent / "fixtures"
LEGACY = FIXTURE_DIR / "ato_returns.yaml"          # top-level reference/lodged
MULTI = FIXTURE_DIR / "ato_returns_multi.yaml"     # taxpayers: [...]


def _tp(data, tp_id):
    return next(t for t in data["taxpayers"] if t["id"] == tp_id)


# --- Legacy single-taxpayer shape (top-level reference/lodged) ---

def test_legacy_shape_wraps_into_one_taxpayer():
    data = load_ato_returns(LEGACY)
    assert len(data["taxpayers"]) == 1
    tp = data["taxpayers"][0]
    assert tp["reference"]["abn"] == "11111111111"
    assert tp["id"] == "test-user"  # slugged from reference.name
    assert tp["name"] == "Test User"


def test_legacy_years_sorted_most_recent_first():
    tp = load_ato_returns(LEGACY)["taxpayers"][0]
    assert [y["fy"] for y in tp["lodged"]] == [2024, 2022]


def test_legacy_latest_carry_forward_from_newest_year():
    tp = load_ato_returns(LEGACY)["taxpayers"][0]
    cf = tp["latest_carry_forward"]
    assert len(cf) == 1
    assert cf[0]["label"] == "18V"
    assert cf[0]["value"] == 9000
    assert cf[0]["from_fy"] == 2024
    assert cf[0]["from_fy_label"] == "FY 2023-24"


def test_string_label_values_preserved():
    tp = load_ato_returns(LEGACY)["taxpayers"][0]
    y2024 = next(y for y in tp["lodged"] if y["fy"] == 2024)
    cgt = next(s for s in y2024["sections"] if s["name"] == "Capital gains")
    assert cgt["rows"][0]["value"] == "Y"


# --- Multi-taxpayer shape (a couple's linked returns) ---

def test_multi_loads_both_taxpayers():
    data = load_ato_returns(MULTI)
    assert [t["id"] for t in data["taxpayers"]] == ["primary", "spouse"]


def test_multi_each_taxpayer_has_own_carry_forward():
    data = load_ato_returns(MULTI)
    assert _tp(data, "primary")["latest_carry_forward"][0]["value"] == 9000
    assert _tp(data, "spouse")["latest_carry_forward"] == []


def test_multi_taxpayers_keep_separate_reference():
    data = load_ato_returns(MULTI)
    assert _tp(data, "primary")["reference"]["tfn"] == "111 111 111"
    assert _tp(data, "spouse")["reference"]["tfn"] == "222 222 222"


# --- Empty / missing ---

def test_missing_file_yields_empty_archive(tmp_path):
    data = load_ato_returns(tmp_path / "nope.yaml")
    assert data == {"taxpayers": []}


def test_taxpayer_id_fallback_when_no_id_or_name(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("taxpayers:\n  - lodged: []\n")
    tp = load_ato_returns(p)["taxpayers"][0]
    assert tp["id"] == "taxpayer-1"  # neither id nor reference.name present


def test_null_fy_does_not_crash_sort(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text(
        "taxpayers:\n  - id: x\n    lodged:\n"
        "      - { fy: 2024, fy_label: 'FY 2023-24' }\n"
        "      - { fy: , fy_label: 'blank' }\n"  # explicit null fy
    )
    tp = load_ato_returns(p)["taxpayers"][0]
    assert tp["lodged"][0]["fy"] == 2024  # real year sorts ahead of the null one


def test_non_dict_top_level_yields_empty(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("- a\n- b\n")
    assert load_ato_returns(p) == {"taxpayers": []}
