from pathlib import Path

import yaml
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from etl import db
from etl.cpi import sync_cpi, get_cpi_for_year, get_cpi_history, get_latest_cpi, adjust_for_inflation
from etl.dedup import find_duplicates, resolve_duplicates
from etl.tax_calc import calculate_total_tax, get_tax_dollar_breakdown
from etl.splitter import load_tax_config
from etl import rental as rental_calc
from etl import schedules as sched_calc
from etl import ato_returns as ato_archive
from etl import depreciation as depreciation_calc

PROJECT_ROOT = Path(__file__).parent.parent
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
CONFIG_DIR = PROJECT_ROOT / "config"
TAX_CONFIG_PATH = CONFIG_DIR / "tax.yaml"
SCHEDULES_CONFIG_PATH = CONFIG_DIR / "schedules.yaml"
ATO_RETURNS_CONFIG_PATH = CONFIG_DIR / "ato_returns.yaml"
DEPRECIATION_CONFIG_PATH = CONFIG_DIR / "depreciation.yaml"

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
    cpi_adjust = request.args.get("cpi_adjust", "false").lower() == "true"

    # CPI adjustment: scale budgets from a base period to the current month
    cpi_multiplier = 1.0
    if cpi_adjust:
        from datetime import date as date_type
        year_int = int(month.split("-")[0])
        current_cpi = get_cpi_for_year(conn, year_int)
        base_cpi = get_cpi_for_year(conn, 2022)  # Budget base year
        if current_cpi and base_cpi and base_cpi > 0:
            cpi_multiplier = current_cpi / base_cpi

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

        budget = round(cat["budget_monthly"] * cpi_multiplier, 2)
        results.append({
            "category": cat["name"],
            "budget": budget,
            "budget_original": cat["budget_monthly"],
            "cpi_adjusted": cpi_adjust and cpi_multiplier != 1.0,
            "actual": abs(actual_row["actual"]),
            "remaining": budget - abs(actual_row["actual"]),
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

    # Item 21: Rental — uses the per-rule apply-mode allocator (etl.rental)
    # which supports floor_area_pct and time-fraction in addition to ownership_pct.
    rental_results = rental_calc.compute_rental_deductions(conn, tax_config, fy_int)
    rental_data = []
    for prop in tax_config.get("rental_properties", []):
        name = prop["name"]
        r = rental_results.get(name)
        if not r:
            continue
        expenses = []
        depreciation = 0.0
        for d in r["deductions"]:
            if d["apply"] == ["depreciation"]:
                depreciation = d["taxpayer_share"]
                continue
            expenses.append({
                "ato_label": d["ato_label"],
                "raw": d["gross"],
                "share": d["taxpayer_share"],
                "factor": d["factor"],
                "apply": d["apply"],
                "n_txns": d["n_txns"],
            })
        rental_data.append({
            "property": name,
            "address": prop.get("address", ""),
            "ownership_pct": prop.get("ownership_pct", 100),
            "floor_area_pct": prop.get("floor_area_pct", 100),
            "rental_weeks": prop.get("rental_weeks", {}).get(fy_int, 0),
            "gross_income": r["income"]["gross"],
            "income_share": r["income"]["taxpayer_share"],
            "expenses": expenses,
            "depreciation": depreciation,
            "total_expenses": r["total_deductions"],
            "net_rent": r["net"],
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

        # COGS & expenses from splits (exclude transfer legs — they aren't expenses)
        biz_expenses = conn.execute("""
            SELECT COALESCE(SUM(ts.business_amount), 0) as total
            FROM transaction_splits ts
            JOIN transactions t ON t.id = ts.transaction_id
            WHERE t.date >= ? AND t.date <= ? AND ts.business_name = ?
              AND t.is_transfer = 0
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
            # Div 35 non-commercial loss: when set, a net LOSS is deferred (carried
            # forward at P9), NOT deducted against other income this year.
            "defer_losses": bool(biz.get("defer_losses", False)),
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

    # --- Final tax summary ----------------------------------------------
    # Build the assessable income / total deductions / taxable income / payable view
    rental_net_total = sum(r["net_rent"] for r in rental_data)
    trips_total = sum(t["total"] for t in trip_deductions)

    # Business: profits are always assessable; losses are a deduction UNLESS the
    # business defers them (Div 35 non-commercial loss), in which case they are
    # quarantined/carried forward (P9) and do NOT reduce this year's income.
    business_profit = sum(b["net"] for b in business_data if b["net"] > 0)
    business_deductible_loss = sum(
        -b["net"] for b in business_data if b["net"] < 0 and not b["defer_losses"]
    )
    business_deferred_loss = round(sum(
        -b["net"] for b in business_data if b["net"] < 0 and b["defer_losses"]
    ), 2)

    assessable = (
        salary
        + interest
        + max(rental_net_total, 0)        # positive net rent adds to income
        + business_profit                 # positive business profit adds to income
    )
    deductions_total = (
        abs(min(rental_net_total, 0))     # negative net rent (loss) is a deduction
        + business_deductible_loss        # non-deferred business losses only
        + wfh_amount
        + trips_total
    )
    taxable_income = round(max(assessable - deductions_total, 0), 2)

    tax_bd = calculate_total_tax(taxable_income, fy_int)
    refund_or_payable = round(tax_withheld - tax_bd["total_tax"], 2)

    summary = {
        "assessable_income": round(assessable, 2),
        "total_deductions": round(deductions_total, 2),
        "taxable_income": taxable_income,
        "payg": tax_bd["payg"],
        "medicare": tax_bd["medicare"],
        "total_tax": tax_bd["total_tax"],
        "effective_rate": tax_bd["effective_rate"],
        "tax_withheld": tax_withheld,
        # positive = refund, negative = bill
        "refund_or_payable": refund_or_payable,
        # Div 35 loss deferred this year (carried forward at P9, not deducted above)
        "business_deferred_loss": business_deferred_loss,
    }

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
        "summary": summary,
    })


# --- Lodged ATO Returns Archive ---

@app.route("/api/ato/lodged")
def api_ato_lodged():
    """Historical lodged ATO returns (from config/ato_returns.yaml).

    Archival record of filed figures, keyed by ATO label code, for copy-paste
    into myTax. Returns one or more taxpayers (a couple lodging linked returns),
    each with static reference fields, their lodged years, and the latest
    carry-forward balances that apply to their next return.
    """
    return jsonify(ato_archive.load_ato_returns(ATO_RETURNS_CONFIG_PATH))


@app.route("/api/depreciation")
def api_depreciation():
    """Depreciation asset register (from config/depreciation.yaml).

    Per-asset capital-allowances schedules with written-down value carried forward
    year to year, plus per-FY deductible-decline totals for copy into the return.
    """
    return jsonify(depreciation_calc.load_depreciation(DEPRECIATION_CONFIG_PATH))


# --- Economics / CPI Endpoints ---

@app.route("/api/dedup", methods=["POST"])
def api_dedup():
    """Find and resolve cross-account duplicate transactions."""
    conn = get_conn()
    try:
        dupes = find_duplicates(conn)
        updated = resolve_duplicates(conn, dupes)
        conn.close()
        return jsonify({"ok": True, "duplicates_found": len(dupes), "resolved": updated})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500


@app.route("/api/cpi/sync", methods=["POST"])
def api_cpi_sync():
    """Fetch latest CPI data from ABS and store in DB."""
    conn = get_conn()
    try:
        count = sync_cpi(conn)
        conn.close()
        return jsonify({"ok": True, "rows_synced": count})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500


@app.route("/api/cpi")
def api_cpi():
    """Return CPI history."""
    conn = get_conn()
    from_year = int(request.args.get("from_year", 2015))
    history = get_cpi_history(conn, from_year)
    latest = get_latest_cpi(conn)
    conn.close()
    return jsonify({"history": history, "latest": latest})


@app.route("/api/summary/economics")
def api_summary_economics():
    """Spending power, tax analysis, inflation-adjusted spending, real vs nominal net worth."""
    from datetime import date as date_type

    conn = get_conn()
    year = int(request.args.get("year", date_type.today().year))

    # Ensure CPI data exists
    latest_cpi = get_latest_cpi(conn)
    if not latest_cpi:
        try:
            sync_cpi(conn)
            latest_cpi = get_latest_cpi(conn)
        except Exception:
            pass

    # --- CPI data ---
    current_cpi = get_cpi_for_year(conn, year)
    prev_cpi = get_cpi_for_year(conn, year - 1)
    base_year = 2012
    base_cpi = get_cpi_for_year(conn, base_year)
    cpi_history = get_cpi_history(conn, 2015)

    yoy_change = None
    if current_cpi and prev_cpi and prev_cpi > 0:
        yoy_change = round((current_cpi - prev_cpi) / prev_cpi * 100, 1)

    # --- Income (salary) for the year ---
    salary_row = conn.execute("""
        SELECT COALESCE(SUM(t.amount), 0) as total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE c.name = 'Salary' AND strftime('%Y', t.date) = ?
    """, (str(year),)).fetchone()
    salary = salary_row["total"] if salary_row else 0

    prev_salary_row = conn.execute("""
        SELECT COALESCE(SUM(t.amount), 0) as total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE c.name = 'Salary' AND strftime('%Y', t.date) = ?
    """, (str(year - 1),)).fetchone()
    prev_salary = prev_salary_row["total"] if prev_salary_row else 0

    # --- Total expenses for the year ---
    expense_row = conn.execute("""
        SELECT COALESCE(SUM(t.amount), 0) as total
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
        WHERE t.amount < 0 AND strftime('%Y', t.date) = ?
          AND t.is_transfer = 0 AND a.account_type NOT IN ('loan')
    """, (str(year),)).fetchone()
    total_expenses = abs(expense_row["total"]) if expense_row else 0

    # --- Monthly expenses ---
    month_count_row = conn.execute("""
        SELECT COUNT(DISTINCT strftime('%Y-%m', t.date)) as months
        FROM transactions t WHERE t.amount < 0 AND strftime('%Y', t.date) = ?
    """, (str(year),)).fetchone()
    active_months = month_count_row["months"] if month_count_row else 1
    monthly_expenses_nominal = total_expenses / max(active_months, 1)

    # --- Real (inflation-adjusted) values ---
    salary_real = None
    salary_real_prev = None
    purchasing_power_loss = None
    monthly_expenses_real = None

    if current_cpi and base_cpi and base_cpi > 0:
        deflator = base_cpi / current_cpi
        salary_real = round(salary * deflator, 2)
        monthly_expenses_real = round(monthly_expenses_nominal * deflator, 2)
        purchasing_power_loss = round((1 - deflator) * 100, 1)

        if prev_cpi and prev_cpi > 0:
            prev_deflator = base_cpi / prev_cpi
            salary_real_prev = round(prev_salary * prev_deflator, 2)

    # --- Tax analysis ---
    # Use FY that overlaps with calendar year (FY ending in year, e.g. year=2025 → FY 2024-25)
    fy = year
    tax_result = calculate_total_tax(salary, fy)
    tax_breakdown = get_tax_dollar_breakdown(tax_result["total_tax"])

    after_tax_real = None
    if current_cpi and base_cpi and base_cpi > 0:
        after_tax_real = round(tax_result["after_tax"] * (base_cpi / current_cpi), 2)

    # Real savings rate
    after_tax = tax_result["after_tax"]
    real_savings_rate = None
    if after_tax > 0:
        real_savings_rate = round((after_tax - total_expenses) / after_tax * 100, 1)

    # --- Inflation-adjusted spending by category ---
    cat_rows = conn.execute("""
        SELECT c.name as category, SUM(t.amount) as total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.amount < 0 AND strftime('%Y', t.date) = ?
          AND t.is_transfer = 0 AND a.account_type NOT IN ('loan')
          AND c.name != 'Uncategorized'
        GROUP BY c.name
        ORDER BY total ASC
    """, (str(year),)).fetchall()

    prev_cat_rows = conn.execute("""
        SELECT c.name as category, SUM(t.amount) as total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.amount < 0 AND strftime('%Y', t.date) = ?
          AND t.is_transfer = 0 AND a.account_type NOT IN ('loan')
          AND c.name != 'Uncategorized'
        GROUP BY c.name
    """, (str(year - 1),)).fetchall()
    prev_cat_lookup = {r["category"]: abs(r["total"]) for r in prev_cat_rows}

    inflation_adjusted_spending = []
    for row in cat_rows:
        nominal = abs(row["total"])
        real_val = None
        real_prev = prev_cat_lookup.get(row["category"])
        real_change_pct = None

        if current_cpi and base_cpi and base_cpi > 0:
            real_val = round(nominal * (base_cpi / current_cpi), 2)

        if real_prev is not None and prev_cpi and base_cpi and base_cpi > 0:
            real_prev_adj = round(real_prev * (base_cpi / prev_cpi), 2)
            if real_prev_adj > 0 and real_val is not None:
                real_change_pct = round((real_val - real_prev_adj) / real_prev_adj * 100, 1)
            real_prev = round(real_prev_adj, 2)

        inflation_adjusted_spending.append({
            "category": row["category"],
            "nominal": round(nominal, 2),
            "real": real_val,
            "real_prev_year": real_prev,
            "real_change_pct": real_change_pct,
        })

    # --- Net worth (nominal) ---
    accounts_data = conn.execute("""
        SELECT a.account_type,
               COALESCE((SELECT SUM(t2.amount) FROM transactions t2 WHERE t2.account_id = a.id), 0) as balance
        FROM accounts a WHERE a.display = 1
    """).fetchall()
    holdings_data = conn.execute("SELECT current_value FROM holdings").fetchall()

    nominal_nw = sum(r["balance"] for r in accounts_data) + sum(
        (r["current_value"] or 0) for r in holdings_data
    )
    real_nw = None
    if current_cpi and base_cpi and base_cpi > 0:
        real_nw = round(nominal_nw * (base_cpi / current_cpi), 2)

    # Previous year net worth (approximate from transactions up to end of prev year)
    prev_accounts = conn.execute("""
        SELECT COALESCE(SUM(t.amount), 0) as total
        FROM transactions t
        JOIN accounts a ON t.account_id = a.id
        WHERE t.date <= ? AND a.display = 1
    """, (f"{year - 1}-12-31",)).fetchone()
    prev_nominal_nw = prev_accounts["total"] if prev_accounts else 0
    # Add holdings (use current values as approximation)
    prev_nominal_nw += sum((r["current_value"] or 0) for r in holdings_data)
    prev_real_nw = None
    if prev_cpi and base_cpi and base_cpi > 0:
        prev_real_nw = round(prev_nominal_nw * (base_cpi / prev_cpi), 2)

    real_nw_change = None
    if real_nw is not None and prev_real_nw and prev_real_nw != 0:
        real_nw_change = round((real_nw - prev_real_nw) / abs(prev_real_nw) * 100, 1)

    conn.close()

    return jsonify({
        "year": str(year),
        "cpi": {
            "current_index": round(current_cpi, 1) if current_cpi else None,
            "yoy_change": yoy_change,
            "base_year_index": round(base_cpi, 1) if base_cpi else None,
            "base_year": str(base_year),
        },
        "spending_power": {
            "salary": round(salary, 2),
            "salary_real": salary_real,
            "salary_real_prev_year": salary_real_prev,
            "purchasing_power_loss": purchasing_power_loss,
            "monthly_expenses_nominal": round(monthly_expenses_nominal, 2),
            "monthly_expenses_real": monthly_expenses_real,
            "real_savings_rate": real_savings_rate,
        },
        "tax_analysis": {
            "gross_income": round(salary, 2),
            **tax_result,
            "after_tax_real": after_tax_real,
            "tax_breakdown": tax_breakdown,
        },
        "inflation_adjusted_spending": inflation_adjusted_spending,
        "net_worth": {
            "nominal": round(nominal_nw, 2),
            "real": real_nw,
            "real_prev_year": prev_real_nw,
            "real_change_pct": real_nw_change,
        },
        "cpi_history": cpi_history,
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


# --- Shared Expenses ---

@app.route("/api/shared-expenses")
def api_shared_expenses():
    conn = get_conn()
    rows = conn.execute("""
        SELECT se.id, se.transaction_id, se.split_pct, se.is_settled, se.settled_date,
               t.date, t.description, t.amount, t.notes,
               c.name as category_name, a.name as account_name,
               (SELECT group_concat(tt.tag, ',') FROM transaction_tags tt
                WHERE tt.transaction_id = t.id) as tags
        FROM shared_expenses se
        JOIN transactions t ON se.transaction_id = t.id
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN accounts a ON t.account_id = a.id
        ORDER BY t.date DESC, t.id DESC
    """).fetchall()

    items = []
    total_shared = 0.0
    total_settled = 0.0
    by_category: dict[str, dict] = {}
    by_tag: dict[str, dict] = {}

    def add_group(bucket: dict, key: str, share: float, settled: bool):
        g = bucket.setdefault(key, {"total_shared": 0.0, "total_settled": 0.0})
        g["total_shared"] += share
        if settled:
            g["total_settled"] += share

    for r in rows:
        d = dict(r)
        share_amount = abs(d["amount"]) * (d["split_pct"] / 100.0)
        d["share_amount"] = round(share_amount, 2)
        d["tags"] = d["tags"].split(",") if d["tags"] else []
        total_shared += share_amount
        if d["is_settled"]:
            total_settled += share_amount
        add_group(by_category, d["category_name"] or "Uncategorized", share_amount, bool(d["is_settled"]))
        for tag in (d["tags"] or ["(untagged)"]):
            add_group(by_tag, tag, share_amount, bool(d["is_settled"]))
        items.append(d)

    def finalize(bucket: dict) -> list:
        out = [{
            "key": key,
            "total_shared": round(g["total_shared"], 2),
            "total_settled": round(g["total_settled"], 2),
            "balance_owing": round(g["total_shared"] - g["total_settled"], 2),
        } for key, g in bucket.items()]
        out.sort(key=lambda x: x["balance_owing"], reverse=True)
        return out

    conn.close()
    return jsonify({
        "items": items,
        "total_shared": round(total_shared, 2),
        "total_settled": round(total_settled, 2),
        "balance_owing": round(total_shared - total_settled, 2),
        "by_category": finalize(by_category),
        "by_tag": finalize(by_tag),
    })


@app.route("/api/schedules")
def api_schedules():
    """Recurring shared schedules (rent, repayments, ...) from config."""
    from datetime import date

    conn = get_conn()
    config = sched_calc.load_schedules_config(SCHEDULES_CONFIG_PATH)
    as_of = date.today()
    results = sched_calc.compute_schedules(conn, config, as_of)
    conn.close()

    total_expected = round(sum(r["expected_to_date"] for r in results), 2)
    total_paid = round(sum(r["paid"] for r in results), 2)
    return jsonify({
        "as_of": as_of.isoformat(),
        "schedules": results,
        "total_expected": total_expected,
        "total_paid": total_paid,
        "total_owing": round(total_expected - total_paid, 2),
    })


@app.route("/api/shared-expenses", methods=["POST"])
def api_add_shared_expense():
    conn = get_conn()
    data = request.get_json()
    txn_id = data["transaction_id"]
    split_pct = data.get("split_pct", 50.0)
    try:
        conn.execute(
            "INSERT INTO shared_expenses (transaction_id, split_pct) VALUES (?, ?)",
            (txn_id, split_pct),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/shared-expenses/<int:se_id>", methods=["PATCH"])
def api_update_shared_expense(se_id: int):
    conn = get_conn()
    data = request.get_json()

    if "is_settled" in data:
        settled_date = None
        if data["is_settled"]:
            from datetime import date
            settled_date = date.today().isoformat()
        conn.execute(
            "UPDATE shared_expenses SET is_settled = ?, settled_date = ? WHERE id = ?",
            (int(data["is_settled"]), settled_date, se_id),
        )

    if "split_pct" in data:
        conn.execute(
            "UPDATE shared_expenses SET split_pct = ? WHERE id = ?",
            (data["split_pct"], se_id),
        )

    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/shared-expenses/<int:se_id>", methods=["DELETE"])
def api_delete_shared_expense(se_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM shared_expenses WHERE id = ?", (se_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


def main():
    db.load_categories_from_config(get_conn(), CONFIG_DIR / "categories.yaml")
    print("Ledger API running on http://localhost:5050")
    app.run(host="127.0.0.1", port=5050, debug=True)


if __name__ == "__main__":
    main()
