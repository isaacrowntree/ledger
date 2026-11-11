"""Taxes paid through spending: GST, excise, duties, and road tolls."""
import json

import pytest

from etl import db
from etl.indirect_tax import estimate


@pytest.fixture
def ledger(conn):
    acct = db.ensure_account(conn, "Card", "test", account_type="credit")
    for name in ("Eating Out", "Groceries", "Fuel", "Utilities", "Insurance", "Car", "Mortgage", "Transfers"):
        db.ensure_category(conn, name)
    n = [0]

    def add(date, desc, amount, category, tags=(), transfer=False):
        n[0] += 1
        tid = db.insert_transaction(
            conn, account_id=acct, date=date, description=desc, amount=amount,
            original_amount=None, original_currency=None, fee=0.0,
            category_id=db.get_category_id(conn, category), category_confidence=None,
            reference_id=None, source_type="test", dedup_hash=f"h{n[0]}", source_file="t.pdf",
            raw_data=json.dumps({}), is_transfer=transfer)
        for t in tags:
            conn.execute("INSERT INTO transaction_tags (transaction_id, tag) VALUES (?, ?)", (tid, t))
        return tid

    return add


def _line(result, name):
    return next((l for l in result["lines"] if l["name"] == name), None)


def test_gst_is_one_eleventh_of_taxable_spending_only(conn, ledger):
    ledger("2026-03-01", "RESTAURANT", -110.0, "Eating Out")      # fully taxable: $10 GST
    ledger("2026-03-02", "SUPERMARKET", -100.0, "Groceries")      # 35% taxable: $35 -> $3.18
    ledger("2026-03-03", "INTEREST", -500.0, "Mortgage")          # financial supply: none
    ledger("2026-03-04", "TO SAVINGS", -1000.0, "Transfers", transfer=True)
    gst = _line(estimate(conn, "2026-01-01", "2026-12-31"), "GST")
    assert gst["amount"] == round((110 + 35) / 11, 2)
    assert gst["estimate"] is True


def test_known_gst_free_parts_are_removed(conn, ledger):
    ledger("2026-03-01", "SYDNEY WATER", -220.0, "Utilities", tags=["water"])
    ledger("2026-03-02", "ORIGIN ENERGY", -110.0, "Utilities")
    ledger("2026-03-03", "SERVICE NSW REGO", -500.0, "Car", tags=["rego"])
    gst = _line(estimate(conn, "2026-01-01", "2026-12-31"), "GST")
    assert gst["amount"] == round(110 / 11, 2)  # water and registration carry none


def test_fuel_excise_from_litres(conn, ledger):
    ledger("2026-03-01", "AMPOL", -200.0, "Fuel")
    excise = _line(estimate(conn, "2026-01-01", "2026-12-31",
                            {"fuel_price_per_litre": 2.0, "fuel_excise_cents_per_litre": 50.0}), "Fuel excise")
    assert excise["amount"] == 50.0  # 100 litres x 50c


def test_insurance_duty_is_inside_the_premium(conn, ledger):
    ledger("2026-03-01", "AAMI", -1199.0, "Insurance", tags=["car-insurance"])
    duty = _line(estimate(conn, "2026-01-01", "2026-12-31"), "Insurance stamp duty")
    # base x 1.09 duty x 1.1 GST = 1199  ->  base 1000, duty 90
    assert duty["amount"] == pytest.approx(90.0, abs=0.01)


def test_actual_charges_are_marked_actual_and_tolls_are_not_tax(conn, ledger):
    ledger("2026-03-01", "BAYSIDE COUNCIL", -400.0, "Utilities", tags=["council-rates"])
    ledger("2026-03-02", "LINKT MELBOURNE", -20.0, "Car")
    r = estimate(conn, "2026-01-01", "2026-12-31")
    rates, tolls = _line(r, "Council rates"), _line(r, "Road tolls")
    assert rates["estimate"] is False and rates["amount"] == 400.0
    assert tolls["kind"] == "charge"
    assert r["total"] == round(r["total_tax"] + 20.0, 2)


def test_lines_appear_only_once_something_is_bought(conn, ledger):
    """No tobacco, no tobacco line; one purchase and it is counted."""
    ledger("2026-03-01", "RESTAURANT", -110.0, "Eating Out")
    assert _line(estimate(conn, "2026-01-01", "2026-12-31"), "Tobacco excise") is None
    ledger("2026-03-02", "TOBACCONIST", -100.0, "Eating Out", tags=["tobacco"])
    tobacco = _line(estimate(conn, "2026-01-01", "2026-12-31", {"tobacco_tax_share": 0.6}), "Tobacco excise")
    assert tobacco["amount"] == 60.0 and tobacco["estimate"] is True


def test_departure_tax_per_international_flight(conn, ledger):
    ledger("2026-03-01", "EMIRATES", -1500.0, "Eating Out", tags=["intl-flight"])
    ledger("2026-04-01", "AIRASIA", -300.0, "Eating Out", tags=["intl-flight"])
    pmc = _line(estimate(conn, "2026-01-01", "2026-12-31"), "Passenger movement charge")
    assert pmc["amount"] == 140.0


def test_fines_and_stamp_duty_are_actual_amounts(conn, ledger):
    ledger("2026-03-01", "REVENUE NSW", -171.0, "Eating Out", tags=["fines"])
    ledger("2026-03-02", "REVENUE NSW TRANSFER DUTY", -900.0, "Eating Out", tags=["stamp-duty"])
    r = estimate(conn, "2026-01-01", "2026-12-31")
    assert _line(r, "Fines and court fees")["amount"] == 171.0
    assert _line(r, "Fines and court fees")["kind"] == "charge"
    assert _line(r, "Stamp duty")["estimate"] is False
