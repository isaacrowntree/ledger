"""Deposits from people that pay back one of the owner's purchases."""
import pytest

from etl import db, reimburse


@pytest.fixture
def ledger(conn):
    acct = db.ensure_account(conn, "Everyday", "ing")
    n = [0]

    def add(date, amount, description, category, confidence=0.85, notes=None):
        n[0] += 1
        return db.insert_transaction(
            conn, account_id=acct, date=date, description=description, amount=amount,
            original_amount=None, original_currency=None, fee=0.0,
            category_id=db.get_category_id(conn, category), category_confidence=confidence,
            reference_id=None, source_type="ing", dedup_hash=f"r{n[0]}", source_file="t.csv",
            raw_data="{}", is_transfer=False) if notes is None else _with_note(conn, add, date, amount,
                                                                                description, category, confidence, notes)
    return conn, add


def _with_note(conn, add, *args):
    *rest, notes = args
    tid = add(*rest)
    conn.execute("UPDATE transactions SET notes = ? WHERE id = ?", (notes, tid))
    return tid


def _cat(conn, tid):
    return conn.execute("SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id "
                        "WHERE t.id = ?", (tid,)).fetchone()[0]


def _linked(conn):
    return dict(conn.execute("SELECT deposit_id, purchase_id FROM reimbursements").fetchall())


P = "Personal (non-assessable)"


def test_an_even_share_soon_after_is_linked_and_offsets_the_category(ledger):
    conn, add = ledger
    dinner = add("2026-03-01", -300.00, "EXAMPLE RESTAURANT EXAMPLETOWN", "Eating Out")
    back = add("2026-03-03", 100.00, "Osko Deposit - Receipt 1 Example Friend dinner", P)
    result = reimburse.match(conn)
    assert result.linked == 1 and _linked(conn) == {back: dinner}
    assert _cat(conn, back) == "Eating Out"
    note = conn.execute("SELECT notes FROM transactions WHERE id = ?", (back,)).fetchone()[0]
    assert note.startswith("repays 1/3 of 2026-03-01 EXAMPLE RESTAURANT")


def test_a_round_amount_fitting_several_purchases_is_left_for_claude(ledger):
    conn, add = ledger
    add("2026-03-01", -50.00, "EXAMPLE CINEMA", "Entertainment")
    add("2026-03-02", -100.00, "EXAMPLE BAR", "Eating Out")
    back = add("2026-03-03", 50.00, "Osko Deposit - Receipt 2 Example Friend", P)
    result = reimburse.match(conn)
    assert result.linked == 0 and [d["id"] for d, _ in result.ambiguous] == [back]
    assert _cat(conn, back) == P


def test_regular_payments_are_a_schedule_not_repayments(ledger):
    conn, add = ledger
    add("2026-03-01", -82.00, "EXAMPLE GROCER", "Groceries")
    for day in ("2026-02-01", "2026-02-15", "2026-03-01"):
        add(day, 82.00, "Deposit - Receipt 3 Example Housemate RENT", P)
    assert reimburse.match(conn).linked == 0


def test_medicare_refunds_are_not_people(ledger):
    conn, add = ledger
    add("2026-03-01", -80.00, "EXAMPLE PHYSIO", "Health")
    add("2026-03-04", 40.00, "Deposit - Receipt 4 Mcare Benefits", P)
    assert reimburse.match(conn).linked == 0


def test_tax_relevant_purchases_are_never_linked_automatically(ledger):
    conn, add = ledger
    add("2026-03-01", -1000.00, "EXAMPLE PLUMBING", "Rental: Maintenance")
    back = add("2026-03-02", 500.00, "Osko Deposit - Receipt 5 Example Co-owner", P)
    result = reimburse.match(conn)
    assert result.linked == 0 and back in {d["id"] for d, _ in result.ambiguous}


def test_a_purchase_is_never_repaid_beyond_its_cost(ledger):
    conn, add = ledger
    add("2026-03-01", -60.00, "EXAMPLE TAXI", "Public Transport")
    add("2026-03-02", 60.00, "Osko Deposit - Receipt 6 Example Friend A", P)
    second = add("2026-03-02", 60.00, "Osko Deposit - Receipt 7 Example Friend B", P)
    reimburse.match(conn)
    assert second not in _linked(conn)


def test_a_group_dinner_is_paid_back_by_everyone(ledger):
    conn, add = ledger
    bill = add("2026-05-09", -800.00, "EXAMPLE RESTAURANT EXAMPLETOWN", "Eating Out")
    shares = [add("2026-05-09", amt, f"EXAMPLE GUEST {i} dinner", P, confidence=1.0)
              for i, amt in enumerate([60.00, 60.00, 60.00, 60.00, 68.00, 120.00, 120.00])]
    unrelated = add("2026-05-10", 45.00, "EXAMPLE GUEST 9 thanks", P)
    reimburse.match(conn)
    linked = _linked(conn)
    assert all(linked.get(s) == bill for s in shares)   # a tip on top, and couples paying double
    assert unrelated not in linked
    assert {_cat(conn, s) for s in shares} == {"Eating Out"}
    # rows the owner confirmed stay confirmed
    assert conn.execute("SELECT MIN(category_confidence) FROM transactions WHERE id IN (%s)"
                        % ",".join(map(str, shares))).fetchone()[0] == 1.0


def test_house_money_is_never_a_repayment(ledger):
    conn, add = ledger
    add("2026-03-01", -20000.00, "EXAMPLE FURNITURE", "Home & Garden")
    add("2026-03-02", 20000.00, "Osko Deposit - Receipt 8 Example Partner", P,
        notes="partner: house equity")
    assert reimburse.match(conn).linked == 0


def test_reports_count_a_repayment_as_less_spending_not_income(tmp_path, monkeypatch):
    from api import server
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ledger.db")
    c = db.get_connection()
    db.init_db(c)
    for name in ("Eating Out", P):
        db.ensure_category(c, name)
    acct = db.ensure_account(c, "Everyday", "ing")
    ids = []
    for i, (amount, desc, cat) in enumerate([(-300.0, "EXAMPLE RESTAURANT", "Eating Out"),
                                             (100.0, "Osko Deposit - Receipt 1 Example Friend", P),
                                             (50.0, "Osko Deposit - Receipt 2 Example Gift", P)]):
        ids.append(db.insert_transaction(
            c, account_id=acct, date="2026-03-0%d" % (i + 1), description=desc, amount=amount,
            original_amount=None, original_currency=None, fee=0.0,
            category_id=db.get_category_id(c, cat), category_confidence=0.85, reference_id=None,
            source_type="ing", dedup_hash=f"h{i}", source_file="t.csv", raw_data="{}", is_transfer=False))
    c.commit()
    reimburse.link(c, ids[1], ids[0], "auto")
    c.commit()
    c.close()
    [month] = server.app.test_client().get("/api/summary/monthly?year=2026").get_json()
    assert month["expenses"] == -200.0     # the dinner, less the friend's share
    assert month["income"] == 0            # a gift is neither income nor spending (neutral)
