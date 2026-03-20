from pathlib import Path

import yaml
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from etl import db
from etl.splitter import load_tax_config

PROJECT_ROOT = Path(__file__).parent.parent
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
CONFIG_DIR = PROJECT_ROOT / "config"
TAX_CONFIG_PATH = CONFIG_DIR / "tax.yaml"

app = Flask(__name__, static_folder=str(FRONTEND_DIST), static_url_path="")
CORS(app)


def get_conn():
    conn = db.get_connection()
    db.init_db(conn)
    return conn


def _loan_transfer_clauses(prefix="t"):
    """Build WHERE clauses for exclude_loans and exclude_transfers params."""
    clauses = []
    params = []

    exclude_loans = request.args.get("exclude_loans", "true").lower()
    if exclude_loans == "true":
        clauses.append(f"a.account_type NOT IN ('loan')")

    exclude_transfers = request.args.get("exclude_transfers", "true").lower()
    if exclude_transfers == "true":
        clauses.append(f"{prefix}.is_transfer = 0")

    return clauses, params


# --- Frontend ---

@app.route("/")
def index():
    return send_from_directory(str(FRONTEND_DIST), "index.html")


# --- API ---

@app.route("/api/transactions")
def api_transactions():
    conn = get_conn()
    query = """
        SELECT t.id, t.date, t.description, t.amount, t.original_amount,
               t.original_currency, t.fee, t.category_id, t.category_confidence,
               t.reference_id, t.notes, t.source_type, t.is_transfer,
               c.name as category_name, a.name as account_name, a.account_type
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN accounts a ON t.account_id = a.id
        WHERE 1=1
    """
    params = []

    if request.args.get("from"):
        query += " AND t.date >= ?"
        params.append(request.args["from"])
    if request.args.get("to"):
        query += " AND t.date <= ?"
        params.append(request.args["to"])
    if request.args.get("category"):
        query += " AND c.name = ?"
        params.append(request.args["category"])
    if request.args.get("account"):
        query += " AND a.name = ?"
        params.append(request.args["account"])
    if request.args.get("search"):
        query += " AND t.description LIKE ?"
        params.append(f"%{request.args['search']}%")

    # Apply loan/transfer exclusions
    extra_clauses, extra_params = _loan_transfer_clauses()
    for clause in extra_clauses:
        query += f" AND {clause}"
    params.extend(extra_params)

    query += " ORDER BY t.date DESC, t.id DESC"

    if request.args.get("limit"):
        query += " LIMIT ?"
        params.append(int(request.args["limit"]))

    rows = conn.execute(query, params).fetchall()

    # Attach tags to each transaction
    results = []
    for r in rows:
        d = dict(r)
        tags = conn.execute(
            "SELECT tag FROM transaction_tags WHERE transaction_id = ?", (d["id"],)
        ).fetchall()
        d["tags"] = [t["tag"] for t in tags]
        results.append(d)

    conn.close()
    return jsonify(results)


@app.route("/api/transactions/<int:txn_id>", methods=["PATCH"])
def api_update_transaction(txn_id: int):
    conn = get_conn()
    data = request.get_json()

    if "category_name" in data:
        cat_row = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (data["category_name"],)
        ).fetchone()
        if cat_row:
            conn.execute(
                "UPDATE transactions SET category_id = ?, category_confidence = 1.0 WHERE id = ?",
                (cat_row["id"], txn_id),
            )
            # Learn this categorization
            txn_row = conn.execute(
                "SELECT description FROM transactions WHERE id = ?", (txn_id,)
            ).fetchone()
            if txn_row:
                desc_upper = txn_row["description"].upper()
                conn.execute(
                    """INSERT INTO category_rules_learned (description_pattern, category_id, times_seen)
                    VALUES (?, ?, 1)
                    ON CONFLICT(description_pattern)
                    DO UPDATE SET category_id = excluded.category_id, times_seen = times_seen + 1""",
                    (desc_upper, cat_row["id"]),
                )

    if "notes" in data:
        conn.execute(
            "UPDATE transactions SET notes = ? WHERE id = ?",
            (data["notes"], txn_id),
        )

    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/categories")
def api_categories():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, is_income, budget_monthly FROM categories ORDER BY name"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/accounts")
def api_accounts():
    conn = get_conn()
    rows = conn.execute("SELECT id, name, source_type, currency, account_type FROM accounts ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/accounts/summary")
def api_accounts_summary():
    """Return each account with name, type, and balance from last known statement balance."""
    conn = get_conn()
    import json

    # Get account info and transaction counts (only visible accounts)
    accounts = conn.execute("""
        SELECT a.id, a.name, a.source_type, a.currency, a.account_type,
               COUNT(t.id) as transaction_count
        FROM accounts a
        LEFT JOIN transactions t ON t.account_id = a.id
        WHERE a.display = 1
        GROUP BY a.id
        ORDER BY a.account_type, a.name
    """).fetchall()

    results = []
    for acct in accounts:
        acct_dict = dict(acct)
        # Try to get the last known balance from raw_data (statement balance column)
        last_raw = conn.execute("""
            SELECT r.raw_data FROM transactions t
            JOIN raw_imports r ON r.transaction_id = t.id
            WHERE t.account_id = ?
            ORDER BY t.date DESC, t.id DESC LIMIT 1
        """, (acct["id"],)).fetchone()

        balance = None
        if last_raw:
            try:
                raw = json.loads(last_raw["raw_data"])
                # Try closing_balance first (credit card PDFs), then Balance (CSV), then balance (ING)
                for key in ("closing_balance", "Balance", "balance"):
                    bal_str = raw.get(key, "")
                    if bal_str and str(bal_str) not in ("", "None"):
                        bal_val = float(str(bal_str).replace(",", ""))
                        if bal_val != 0 or key == "balance":  # ING balance of 0 is valid
                            # Credit card closing balances are positive in statements but represent debt
                            if acct["account_type"] == "credit" and bal_val > 0:
                                balance = -bal_val
                            # Loan accounts: balance from offset is positive but represents debt
                            elif acct["account_type"] == "loan" and bal_val > 0:
                                balance = -bal_val
                            else:
                                balance = bal_val
                            break
            except (ValueError, TypeError):
                pass

        # Fall back to SUM(amount) for all accounts
        if balance is None:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) as bal FROM transactions WHERE account_id = ?",
                (acct["id"],)
            ).fetchone()
            balance = row["bal"]

        acct_dict["balance"] = balance
        results.append(acct_dict)

    # Include holdings
    holdings = conn.execute("""
        SELECT id, asset_type, name, ticker, units, cost_basis,
               COALESCE(current_value, cost_basis) as current_value, as_at_date
        FROM holdings ORDER BY asset_type, name
    """).fetchall()

    conn.close()

    return jsonify({
        "accounts": results,
        "holdings": [dict(h) for h in holdings],
    })


@app.route("/api/summary/monthly")
def api_summary_monthly():
    conn = get_conn()
    year = request.args.get("year")

    query = """
        SELECT strftime('%Y-%m', t.date) as month,
               SUM(CASE WHEN t.amount < 0 THEN t.amount ELSE 0 END) as expenses,
               SUM(CASE WHEN t.amount > 0 THEN t.amount ELSE 0 END) as income
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
    """
    params = []
    where_parts = []

    if year:
        where_parts.append("strftime('%Y', t.date) = ?")
        params.append(year)

    extra_clauses, extra_params = _loan_transfer_clauses()
    where_parts.extend(extra_clauses)
    params.extend(extra_params)

    if request.args.get("account"):
        where_parts.append("a.name = ?")
        params.append(request.args["account"])

    if where_parts:
        query += " WHERE " + " AND ".join(where_parts)

    query += " GROUP BY month ORDER BY month"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/summary/category")
def api_summary_category():
    conn = get_conn()
    query = """
        SELECT c.name as category, c.is_income,
               SUM(t.amount) as total,
               COUNT(*) as count
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE 1=1
    """
    params = []

    if request.args.get("from"):
        query += " AND t.date >= ?"
        params.append(request.args["from"])
    if request.args.get("to"):
        query += " AND t.date <= ?"
        params.append(request.args["to"])

    extra_clauses, extra_params = _loan_transfer_clauses()
    for clause in extra_clauses:
        query += f" AND {clause}"
    params.extend(extra_params)

    if request.args.get("account"):
        query += " AND a.name = ?"
        params.append(request.args["account"])

    query += " GROUP BY c.name ORDER BY total ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/budget-vs-actual")
def api_budget_vs_actual():
    conn = get_conn()
    month = request.args.get("month")  # YYYY-MM
    if not month:
        from datetime import date
        month = date.today().strftime("%Y-%m")

    # Get budgets
    categories = conn.execute(
        "SELECT id, name, budget_monthly, is_income FROM categories WHERE budget_monthly IS NOT NULL"
    ).fetchall()

    exclude_loans = request.args.get("exclude_loans", "true").lower() == "true"
    exclude_transfers = request.args.get("exclude_transfers", "true").lower() == "true"

    results = []
    for cat in categories:
        query = """
            SELECT COALESCE(SUM(t.amount), 0) as actual
            FROM transactions t
            JOIN accounts a ON t.account_id = a.id
            WHERE t.category_id = ? AND strftime('%Y-%m', t.date) = ?
        """
        query_params = [cat["id"], month]

        if exclude_loans:
            query += " AND a.account_type NOT IN ('loan')"
        if exclude_transfers:
            query += " AND t.is_transfer = 0"

        actual_row = conn.execute(query, query_params).fetchone()

        results.append({
            "category": cat["name"],
            "budget": cat["budget_monthly"],
            "actual": abs(actual_row["actual"]),
            "remaining": cat["budget_monthly"] - abs(actual_row["actual"]),
            "is_income": bool(cat["is_income"]),
        })

    conn.close()
    return jsonify(results)


@app.route("/api/summary/trends")
def api_summary_trends():
    """Monthly spending by category over time."""
    conn = get_conn()
    query = """
        SELECT strftime('%Y-%m', t.date) as month,
               c.name as category,
               SUM(t.amount) as total
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.amount < 0
    """
    params = []
    if request.args.get("from"):
        query += " AND t.date >= ?"
        params.append(request.args["from"])
    if request.args.get("to"):
        query += " AND t.date <= ?"
        params.append(request.args["to"])

    extra_clauses, extra_params = _loan_transfer_clauses()
    for clause in extra_clauses:
        query += f" AND {clause}"
    params.extend(extra_params)

    if request.args.get("account"):
        query += " AND a.name = ?"
        params.append(request.args["account"])

    query += " GROUP BY month, c.name ORDER BY month"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/holdings")
def api_holdings():
    """Return all holdings with current values."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT h.id, h.asset_type, h.name, h.ticker, h.units, h.cost_basis,
               h.current_value, h.as_at_date, h.notes
        FROM holdings h
        ORDER BY h.asset_type, h.name
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/holdings/<int:holding_id>/events")
def api_holding_events(holding_id: int):
    """Return asset events for a specific holding."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM asset_events WHERE holding_id = ? ORDER BY date DESC
    """, (holding_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/holdings", methods=["POST"])
def api_create_holding():
    """Create or update a holding."""
    conn = get_conn()
    data = request.get_json()
    try:
        cursor = conn.execute(
            """INSERT INTO holdings (asset_type, name, ticker, units, cost_basis, current_value, as_at_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_type, name) DO UPDATE SET
                ticker=excluded.ticker, units=excluded.units, cost_basis=excluded.cost_basis,
                current_value=excluded.current_value, as_at_date=excluded.as_at_date, notes=excluded.notes""",
            (data["asset_type"], data["name"], data.get("ticker"), data.get("units", 0),
             data.get("cost_basis", 0), data.get("current_value"), data.get("as_at_date"), data.get("notes")),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "id": cursor.lastrowid})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/holdings/<int:holding_id>/events", methods=["POST"])
def api_create_event(holding_id: int):
    """Add an asset event (buy, sell, dividend, etc.)."""
    conn = get_conn()
    data = request.get_json()
    try:
        conn.execute(
            """INSERT INTO asset_events (holding_id, date, event_type, units, price_per_unit, total_value, fees, reference, source_file, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (holding_id, data["date"], data["event_type"], data.get("units"), data.get("price_per_unit"),
             data["total_value"], data.get("fees", 0), data.get("reference"), data.get("source_file"), data.get("notes")),
        )
        # Update holding units and cost basis
        if data["event_type"] == "buy":
            conn.execute("UPDATE holdings SET units = units + ?, cost_basis = cost_basis + ? WHERE id = ?",
                        (data.get("units", 0), data["total_value"], holding_id))
        elif data["event_type"] == "sell":
            conn.execute("UPDATE holdings SET units = units - ? WHERE id = ?",
                        (data.get("units", 0), holding_id))
        elif data["event_type"] == "valuation":
            conn.execute("UPDATE holdings SET current_value = ?, as_at_date = ? WHERE id = ?",
                        (data["total_value"], data["date"], holding_id))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/tags")
def api_tags():
    """Return all tags with transaction counts."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT tag, COUNT(*) as count, ROUND(SUM(t.amount), 2) as total
        FROM transaction_tags tt
        JOIN transactions t ON t.id = tt.transaction_id
        GROUP BY tag ORDER BY count DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/summary/tax")
def api_summary_tax():
    """ATO financial year summary: income, business expenses, work deductions, by category."""
    conn = get_conn()
    fy = request.args.get("fy")  # e.g. "2025" means FY 2024-25 (Jul 2024 - Jun 2025)
    if not fy:
        from datetime import date
        today = date.today()
        fy = str(today.year) if today.month >= 7 else str(today.year - 1)

    fy_int = int(fy)
    fy_start = f"{fy_int - 1}-07-01"
    fy_end = f"{fy_int}-06-30"

    # All transactions in the FY, excluding transfers and loans
    rows = conn.execute("""
        SELECT c.name as category, c.is_income,
               SUM(t.amount) as total,
               COUNT(*) as count
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.date >= ? AND t.date <= ?
          AND t.is_transfer = 0
          AND a.account_type NOT IN ('loan')
        GROUP BY c.name
        ORDER BY c.is_income DESC, total ASC
    """, (fy_start, fy_end)).fetchall()

    # Also get top business expense transactions for detail
    biz_txns = conn.execute("""
        SELECT t.date, t.description, t.amount, c.name as category, a.name as account_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.date >= ? AND t.date <= ?
          AND c.name LIKE 'Business:%'
          AND t.is_transfer = 0
        ORDER BY t.date
    """, (fy_start, fy_end)).fetchall()

    conn.close()
    return jsonify({
        "fy": fy,
        "fy_label": f"FY {fy_int - 1}-{str(fy_int)[2:]}",
        "fy_start": fy_start,
        "fy_end": fy_end,
        "categories": [dict(r) for r in rows],
        "business_transactions": [dict(r) for r in biz_txns],
    })


@app.route("/api/summary/year-review")
def api_year_review():
    """Comprehensive year-in-review stats."""
    conn = get_conn()
    year = request.args.get("year", "2025")

    # Monthly income/expenses
    monthly = conn.execute("""
        SELECT strftime('%Y-%m', t.date) as month,
               SUM(CASE WHEN t.amount > 0 THEN t.amount ELSE 0 END) as income,
               SUM(CASE WHEN t.amount < 0 THEN t.amount ELSE 0 END) as expenses
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
        WHERE strftime('%Y', t.date) = ? AND t.is_transfer = 0 AND a.account_type NOT IN ('loan')
        GROUP BY month ORDER BY month
    """, (year,)).fetchall()

    # Category breakdown
    categories = conn.execute("""
        SELECT c.name as category, c.is_income,
               SUM(t.amount) as total, COUNT(*) as count
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE strftime('%Y', t.date) = ? AND t.is_transfer = 0 AND a.account_type NOT IN ('loan')
        GROUP BY c.name ORDER BY total
    """, (year,)).fetchall()

    # Top merchants
    merchants = conn.execute("""
        SELECT t.description, COUNT(*) as count, SUM(t.amount) as total
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
        WHERE strftime('%Y', t.date) = ? AND t.amount < 0
          AND t.is_transfer = 0 AND a.account_type NOT IN ('loan')
        GROUP BY UPPER(SUBSTR(t.description, 1, 40))
        ORDER BY total ASC LIMIT 20
    """, (year,)).fetchall()

    # Business expenses
    business = conn.execute("""
        SELECT c.name as category, SUM(t.amount) as total, COUNT(*) as count
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE strftime('%Y', t.date) = ? AND c.name LIKE 'Business:%'
          AND t.is_transfer = 0
        GROUP BY c.name
    """, (year,)).fetchall()

    # Year-over-year comparison (previous year)
    prev_year = str(int(year) - 1)
    prev_monthly = conn.execute("""
        SELECT SUM(CASE WHEN t.amount > 0 THEN t.amount ELSE 0 END) as income,
               SUM(CASE WHEN t.amount < 0 THEN t.amount ELSE 0 END) as expenses
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
        WHERE strftime('%Y', t.date) = ? AND t.is_transfer = 0 AND a.account_type NOT IN ('loan')
    """, (prev_year,)).fetchone()

    # Transaction count by source
    sources = conn.execute("""
        SELECT t.source_type, COUNT(*) as count
        FROM transactions t
        WHERE strftime('%Y', t.date) = ?
        GROUP BY t.source_type ORDER BY count DESC
    """, (year,)).fetchall()

    # Biggest single transactions
    biggest_expense = conn.execute("""
        SELECT t.date, t.description, t.amount, a.name as account_name
        FROM transactions t JOIN accounts a ON t.account_id = a.id
        WHERE strftime('%Y', t.date) = ? AND t.amount < 0 AND t.is_transfer = 0
        ORDER BY t.amount ASC LIMIT 5
    """, (year,)).fetchall()

    biggest_income = conn.execute("""
        SELECT t.date, t.description, t.amount, a.name as account_name
        FROM transactions t JOIN accounts a ON t.account_id = a.id
        WHERE strftime('%Y', t.date) = ? AND t.amount > 0 AND t.is_transfer = 0
        ORDER BY t.amount DESC LIMIT 5
    """, (year,)).fetchall()

    conn.close()

    total_income = sum(m["income"] or 0 for m in monthly)
    total_expenses = sum(abs(m["expenses"] or 0) for m in monthly)

    return jsonify({
        "year": year,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net": total_income - total_expenses,
        "savings_rate": (total_income - total_expenses) / total_income * 100 if total_income > 0 else 0,
        "avg_monthly_expense": total_expenses / max(len(monthly), 1),
        "monthly": [dict(m) for m in monthly],
        "categories": [dict(c) for c in categories],
        "top_merchants": [dict(m) for m in merchants],
        "business": [dict(b) for b in business],
        "previous_year": {
            "income": prev_monthly["income"] if prev_monthly else 0,
            "expenses": prev_monthly["expenses"] if prev_monthly else 0,
        },
        "sources": [dict(s) for s in sources],
        "biggest_expenses": [dict(t) for t in biggest_expense],
        "biggest_income": [dict(t) for t in biggest_income],
    })


@app.route("/api/summary/top-merchants")
def api_top_merchants():
    """Top merchants by spend for a given year."""
    conn = get_conn()
    year = request.args.get("year")
    query = """
        SELECT t.description, COUNT(*) as count, SUM(t.amount) as total
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
        WHERE t.amount < 0
          AND t.is_transfer = 0
          AND a.account_type NOT IN ('loan')
    """
    params = []
    if year:
        query += " AND strftime('%Y', t.date) = ?"
        params.append(year)
    query += " GROUP BY UPPER(SUBSTR(t.description, 1, 40)) ORDER BY total ASC LIMIT 15"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


def _fy_dates(fy: str | None) -> tuple[int, str, str]:
    """Parse FY param and return (fy_int, start_date, end_date)."""
    if not fy:
        from datetime import date
        today = date.today()
        fy = str(today.year) if today.month >= 7 else str(today.year - 1)
    fy_int = int(fy)
    return fy_int, f"{fy_int - 1}-07-01", f"{fy_int}-06-30"


def _get_tax_config() -> dict:
    return load_tax_config(TAX_CONFIG_PATH)


# --- Spreadsheet Endpoints ---

@app.route("/api/spreadsheet/outgoing")
def api_spreadsheet_outgoing():
    """All expense transactions with business split columns."""
    conn = get_conn()
    fy_int, fy_start, fy_end = _fy_dates(request.args.get("fy"))

    rows = conn.execute("""
        SELECT t.id, t.date, t.description, t.amount, t.source_type,
               c.name as category_name, a.name as account_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.date >= ? AND t.date <= ?
          AND t.amount < 0
          AND t.is_transfer = 0
          AND a.account_type NOT IN ('loan')
        ORDER BY t.date, t.id
    """, (fy_start, fy_end)).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        # Get splits
        splits = conn.execute(
            "SELECT business_name, business_pct, business_amount FROM transaction_splits WHERE transaction_id = ?",
            (d["id"],)
        ).fetchall()
        d["splits"] = [dict(s) for s in splits]
        # Flatten for ZD convenience
        # Use the first business split (if any) for convenience columns
        first_split = d["splits"][0] if d["splits"] else None
        d["biz_pct"] = first_split["business_pct"] if first_split else 0
        d["biz_amount"] = first_split["business_amount"] if first_split else 0
        d["biz_name"] = first_split["business_name"] if first_split else ""
        results.append(d)

    conn.close()
    return jsonify(results)


@app.route("/api/spreadsheet/incoming")
def api_spreadsheet_incoming():
    """All income transactions grouped by category."""
    conn = get_conn()
    fy_int, fy_start, fy_end = _fy_dates(request.args.get("fy"))

    rows = conn.execute("""
        SELECT t.id, t.date, t.description, t.amount, t.source_type,
               c.name as category_name, a.name as account_name
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.date >= ? AND t.date <= ?
          AND t.amount > 0
          AND t.is_transfer = 0
          AND a.account_type NOT IN ('loan')
        ORDER BY t.date, t.id
    """, (fy_start, fy_end)).fetchall()

    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/spreadsheet/rental")
def api_spreadsheet_rental():
    """Rental property schedule for ATO."""
    conn = get_conn()
    fy_int, fy_start, fy_end = _fy_dates(request.args.get("fy"))
    tax_config = _get_tax_config()

    results = []
    for prop in tax_config.get("rental_properties", []):
        rental_weeks = prop.get("rental_weeks", {}).get(fy_int, 0)
        ownership_pct = prop.get("ownership_pct", 100)

        # Income
        income_cat = prop.get("income_category", "Airbnb Income")
        income_row = conn.execute("""
            SELECT COALESCE(SUM(t.amount), 0) as total
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.date >= ? AND t.date <= ? AND c.name = ?
              AND t.is_transfer = 0
        """, (fy_start, fy_end, income_cat)).fetchone()
        gross_income = income_row["total"]
        income_share = round(gross_income * ownership_pct / 100, 2)

        # Expenses by ATO mapping
        expenses = []
        for mapping in prop.get("expense_mapping", []):
            if "category" in mapping:
                row = conn.execute("""
                    SELECT COALESCE(SUM(t.amount), 0) as total
                    FROM transactions t
                    LEFT JOIN categories c ON t.category_id = c.id
                    WHERE t.date >= ? AND t.date <= ? AND c.name = ?
                      AND t.is_transfer = 0
                """, (fy_start, fy_end, mapping["category"])).fetchone()
                raw_amount = abs(row["total"])
            elif "tag" in mapping:
                row = conn.execute("""
                    SELECT COALESCE(SUM(t.amount), 0) as total
                    FROM transactions t
                    JOIN transaction_tags tt ON tt.transaction_id = t.id
                    WHERE t.date >= ? AND t.date <= ? AND tt.tag = ?
                      AND t.is_transfer = 0
                """, (fy_start, fy_end, mapping["tag"])).fetchone()
                raw_amount = abs(row["total"])
            else:
                raw_amount = 0

            share_amount = round(raw_amount * ownership_pct / 100, 2)
            expenses.append({
                "ato_label": mapping["ato_label"],
                "raw_amount": raw_amount,
                "share_amount": share_amount,
            })

        # Depreciation
        dep_items = []
        for sched in tax_config.get("depreciation_schedules", []):
            if sched.get("property") == prop["name"] and sched.get("type") == "rental":
                for item in sched.get("items", []):
                    if item.get("fy") == fy_int:
                        dep_items.append({
                            "description": item["description"],
                            "amount": item["amount"],
                        })

        total_expenses = sum(e["share_amount"] for e in expenses) + sum(d["amount"] for d in dep_items)
        net_rent = income_share - total_expenses

        results.append({
            "name": prop["name"],
            "address": prop.get("address", ""),
            "ownership_pct": ownership_pct,
            "rental_weeks": rental_weeks,
            "gross_income": gross_income,
            "income_share": income_share,
            "expenses": expenses,
            "depreciation": dep_items,
            "total_expenses": total_expenses,
            "net_rent": net_rent,
        })

    conn.close()
    return jsonify(results)


@app.route("/api/spreadsheet/work-trips")
def api_spreadsheet_work_trips():
    """Work trips and WFH deductions."""
    conn = get_conn()
    fy_int, fy_start, fy_end = _fy_dates(request.args.get("fy"))
    tax_config = _get_tax_config()

    # Work trips from DB
    trips = conn.execute(
        "SELECT * FROM work_trips WHERE fy = ? ORDER BY start_date", (fy_int,)
    ).fetchall()

    trip_results = []
    for trip in trips:
        expenses = conn.execute(
            "SELECT * FROM work_trip_expenses WHERE trip_id = ? ORDER BY expense_type",
            (trip["id"],)
        ).fetchall()
        trip_results.append({
            **dict(trip),
            "expenses": [dict(e) for e in expenses],
            "total": sum(e["amount"] for e in expenses),
        })

    # WFH
    wfh_config = tax_config.get("work_deductions", {}).get("wfh", {})
    wfh_entry = wfh_config.get("entries", {}).get(fy_int, {})
    wfh_rate = wfh_config.get("rate_per_hour", 0.67)
    wfh_weeks = wfh_entry.get("weeks", 0)
    wfh_pct = wfh_entry.get("allocation_pct", 0)
    # ATO fixed rate method: $0.67/hr × hours
    # Simplified: weeks × allocation% × 38hrs/week × rate
    wfh_hours = wfh_weeks * (wfh_pct / 100) * 38
    wfh_amount = round(wfh_hours * wfh_rate, 2)

    conn.close()
    return jsonify({
        "trips": trip_results,
        "wfh": {
            "weeks": wfh_weeks,
            "allocation_pct": wfh_pct,
            "rate_per_hour": wfh_rate,
            "hours": round(wfh_hours, 1),
            "amount": wfh_amount,
        },
    })


# --- ATO Return Endpoint ---

@app.route("/api/ato/return")
def api_ato_return():
    """Structured ATO return data matching actual return sections."""
    conn = get_conn()
    fy_int, fy_start, fy_end = _fy_dates(request.args.get("fy"))
    tax_config = _get_tax_config()

    # Item 1: Salary
    salary = conn.execute("""
        SELECT COALESCE(SUM(t.amount), 0) as total
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.date >= ? AND t.date <= ? AND c.name = 'Salary' AND t.is_transfer = 0
          AND a.account_type NOT IN ('loan')
    """, (fy_start, fy_end)).fetchone()["total"]

    # Item 10: Interest
    interest = conn.execute("""
        SELECT COALESCE(SUM(t.amount), 0) as total
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.date >= ? AND t.date <= ? AND c.name = 'Interest Income' AND t.is_transfer = 0
          AND a.account_type NOT IN ('loan')
    """, (fy_start, fy_end)).fetchone()["total"]

    # Item 21: Rental - reuse spreadsheet/rental logic
    rental_data = []
    for prop in tax_config.get("rental_properties", []):
        rental_weeks = prop.get("rental_weeks", {}).get(fy_int, 0)
        ownership_pct = prop.get("ownership_pct", 100)
        income_cat = prop.get("income_category", "Airbnb Income")

        income_row = conn.execute("""
            SELECT COALESCE(SUM(t.amount), 0) as total
            FROM transactions t LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.date >= ? AND t.date <= ? AND c.name = ? AND t.is_transfer = 0
        """, (fy_start, fy_end, income_cat)).fetchone()
        gross_income = income_row["total"]
        income_share = round(gross_income * ownership_pct / 100, 2)

        expenses = []
        for mapping in prop.get("expense_mapping", []):
            if "category" in mapping:
                row = conn.execute("""
                    SELECT COALESCE(SUM(t.amount), 0) as total
                    FROM transactions t LEFT JOIN categories c ON t.category_id = c.id
                    WHERE t.date >= ? AND t.date <= ? AND c.name = ? AND t.is_transfer = 0
                """, (fy_start, fy_end, mapping["category"])).fetchone()
                raw = abs(row["total"])
            elif "tag" in mapping:
                row = conn.execute("""
                    SELECT COALESCE(SUM(t.amount), 0) as total
                    FROM transactions t JOIN transaction_tags tt ON tt.transaction_id = t.id
                    WHERE t.date >= ? AND t.date <= ? AND tt.tag = ? AND t.is_transfer = 0
                """, (fy_start, fy_end, mapping["tag"])).fetchone()
                raw = abs(row["total"])
            else:
                raw = 0
            share = round(raw * ownership_pct / 100, 2)
            expenses.append({"ato_label": mapping["ato_label"], "raw": raw, "share": share})

        # Depreciation
        dep_total = 0
        for sched in tax_config.get("depreciation_schedules", []):
            if sched.get("property") == prop["name"] and sched.get("type") == "rental":
                for item in sched.get("items", []):
                    if item.get("fy") == fy_int:
                        dep_total += item["amount"]

        total_exp = sum(e["share"] for e in expenses) + dep_total
        net = income_share - total_exp

        rental_data.append({
            "property": prop["name"],
            "address": prop.get("address", ""),
            "ownership_pct": ownership_pct,
            "rental_weeks": rental_weeks,
            "gross_income": gross_income,
            "income_share": income_share,
            "expenses": expenses,
            "depreciation": dep_total,
            "total_expenses": total_exp,
            "net_rent": net,
        })

    # Business schedule
    business_data = []
    for biz in tax_config.get("businesses", []):
        biz_name = biz["name"]

        # Income (Freelance Income / Other Income tagged as business)
        biz_income = conn.execute("""
            SELECT COALESCE(SUM(t.amount), 0) as total
            FROM transactions t LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.date >= ? AND t.date <= ? AND t.is_transfer = 0
              AND (c.name = 'Freelance Income' OR c.name = 'Other Income')
        """, (fy_start, fy_end)).fetchone()["total"]

        # COGS & expenses from splits
        biz_expenses = conn.execute("""
            SELECT COALESCE(SUM(ts.business_amount), 0) as total
            FROM transaction_splits ts
            JOIN transactions t ON t.id = ts.transaction_id
            WHERE t.date >= ? AND t.date <= ? AND ts.business_name = ?
        """, (fy_start, fy_end, biz_name)).fetchone()["total"]

        # Depreciation
        dep_total = 0
        for sched in tax_config.get("depreciation_schedules", []):
            if sched.get("business") == biz_name and sched.get("type") == "business":
                for item in sched.get("items", []):
                    if item.get("fy") == fy_int:
                        dep_total += item["amount"]

        net = biz_income + biz_expenses - dep_total  # expenses are negative

        business_data.append({
            "name": biz_name,
            "abn": biz.get("abn", ""),
            "income": biz_income,
            "expenses": biz_expenses,
            "depreciation": dep_total,
            "net": round(net, 2),
        })

    # Work deductions
    wfh_config = tax_config.get("work_deductions", {}).get("wfh", {})
    wfh_entry = wfh_config.get("entries", {}).get(fy_int, {})
    wfh_weeks = wfh_entry.get("weeks", 0)
    wfh_pct = wfh_entry.get("allocation_pct", 0)
    wfh_rate = wfh_config.get("rate_per_hour", 0.67)
    wfh_hours = wfh_weeks * (wfh_pct / 100) * 38
    wfh_amount = round(wfh_hours * wfh_rate, 2)

    # Work trips
    trips = conn.execute("SELECT * FROM work_trips WHERE fy = ?", (fy_int,)).fetchall()
    trip_deductions = []
    for trip in trips:
        expenses = conn.execute(
            "SELECT expense_type, SUM(amount) as total FROM work_trip_expenses WHERE trip_id = ? GROUP BY expense_type",
            (trip["id"],)
        ).fetchall()
        trip_deductions.append({
            "name": trip["name"],
            "start_date": trip["start_date"],
            "end_date": trip["end_date"],
            "expenses": {e["expense_type"]: e["total"] for e in expenses},
            "total": sum(e["total"] for e in expenses),
        })

    # Tax overrides / manual entries
    overrides = conn.execute(
        "SELECT * FROM tax_overrides WHERE fy = ?", (fy_int,)
    ).fetchall()
    manual = tax_config.get("manual_entries", {}).get(fy_int, [])

    # Tax withheld
    tax_withheld = 0
    for entry in manual:
        if entry.get("label") == "Tax withheld":
            tax_withheld = entry["amount"]

    # Spouse info
    spouse = tax_config.get("taxpayer", {}).get("spouse", {})

    conn.close()

    return jsonify({
        "fy": fy_int,
        "fy_label": f"FY {fy_int - 1}-{str(fy_int)[2:]}",
        "income": {
            "salary": salary,
            "interest": round(interest, 2),
            "tax_withheld": tax_withheld,
        },
        "rental": rental_data,
        "business": business_data,
        "deductions": {
            "wfh": {
                "weeks": wfh_weeks,
                "allocation_pct": wfh_pct,
                "amount": wfh_amount,
            },
            "work_trips": trip_deductions,
        },
        "manual_entries": manual,
        "overrides": [dict(o) for o in overrides],
        "spouse": spouse,
    })


# --- Supporting CRUD Endpoints ---

@app.route("/api/transactions/<int:txn_id>/split", methods=["PATCH"])
def api_update_split(txn_id: int):
    """Manual override of a transaction split."""
    conn = get_conn()
    data = request.get_json()
    biz_name = data.get("business_name")
    if not biz_name:
        tax_config = _get_tax_config()
        businesses = tax_config.get("businesses", [])
        biz_name = businesses[0]["name"] if businesses else "Business"
    pct = data.get("business_pct", 0)

    # Get transaction amount
    txn = conn.execute("SELECT amount FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if not txn:
        conn.close()
        return jsonify({"error": "Transaction not found"}), 404

    amount = round(txn["amount"] * pct / 100, 2)

    if pct == 0:
        conn.execute("DELETE FROM transaction_splits WHERE transaction_id = ? AND business_name = ?",
                     (txn_id, biz_name))
    else:
        conn.execute("""
            INSERT OR REPLACE INTO transaction_splits (transaction_id, business_name, business_pct, business_amount)
            VALUES (?, ?, ?, ?)
        """, (txn_id, biz_name, pct, amount))

    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/work-trips", methods=["GET"])
def api_list_work_trips():
    conn = get_conn()
    fy = request.args.get("fy")
    if fy:
        rows = conn.execute("SELECT * FROM work_trips WHERE fy = ? ORDER BY start_date", (int(fy),)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM work_trips ORDER BY fy DESC, start_date").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/work-trips", methods=["POST"])
def api_create_work_trip():
    conn = get_conn()
    data = request.get_json()
    try:
        cursor = conn.execute(
            "INSERT INTO work_trips (fy, name, start_date, end_date, notes) VALUES (?, ?, ?, ?, ?)",
            (data["fy"], data["name"], data["start_date"], data["end_date"], data.get("notes")),
        )
        conn.commit()
        trip_id = cursor.lastrowid

        # Add expenses if provided
        for exp in data.get("expenses", []):
            conn.execute(
                "INSERT INTO work_trip_expenses (trip_id, expense_type, amount, description) VALUES (?, ?, ?, ?)",
                (trip_id, exp["expense_type"], exp["amount"], exp.get("description")),
            )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "id": trip_id})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/work-trips/<int:trip_id>", methods=["DELETE"])
def api_delete_work_trip(trip_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM work_trip_expenses WHERE trip_id = ?", (trip_id,))
    conn.execute("DELETE FROM work_trips WHERE id = ?", (trip_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/tax-overrides", methods=["GET"])
def api_list_tax_overrides():
    conn = get_conn()
    fy = request.args.get("fy")
    if fy:
        rows = conn.execute("SELECT * FROM tax_overrides WHERE fy = ?", (int(fy),)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tax_overrides ORDER BY fy DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/tax-overrides", methods=["POST"])
def api_create_tax_override():
    conn = get_conn()
    data = request.get_json()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO tax_overrides (fy, section, label, amount, notes)
            VALUES (?, ?, ?, ?, ?)""",
            (data["fy"], data["section"], data["label"], data["amount"], data.get("notes")),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/tax-overrides/<int:override_id>", methods=["DELETE"])
def api_delete_tax_override(override_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM tax_overrides WHERE id = ?", (override_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


def main():
    db.load_categories_from_config(get_conn(), CONFIG_DIR / "categories.yaml")
    print("Ledger API running on http://localhost:5050")
    app.run(host="127.0.0.1", port=5050, debug=True)


if __name__ == "__main__":
    main()
