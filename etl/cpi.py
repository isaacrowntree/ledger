"""Fetch and manage CPI data from the Australian Bureau of Statistics."""

import csv
import io
import sqlite3
from datetime import date
from urllib.request import Request, urlopen

ABS_CPI_URL = (
    "https://data.api.abs.gov.au/rest/data/CPI/1.10001.10.50.Q"
    "?startPeriod=2010-Q1&detail=dataonly"
)

ABS_CPI_YOY_URL = (
    "https://data.api.abs.gov.au/rest/data/CPI/2.10001.10.50.Q"
    "?startPeriod=2010-Q1&detail=dataonly"
)


def sync_cpi(conn: sqlite3.Connection) -> int:
    """Fetch latest CPI data from ABS and upsert into cpi_data table.

    Returns the number of rows upserted.
    """
    # Fetch index values
    index_data = _fetch_abs_csv(ABS_CPI_URL)
    # Fetch YoY percentage changes
    yoy_data = _fetch_abs_csv(ABS_CPI_YOY_URL)
    yoy_lookup = {row["TIME_PERIOD"]: row["OBS_VALUE"] for row in yoy_data}

    count = 0
    for row in index_data:
        period = row["TIME_PERIOD"]
        index_value = float(row["OBS_VALUE"])
        yoy = yoy_lookup.get(period)
        pct_change_yoy = float(yoy) if yoy else None

        conn.execute(
            """INSERT INTO cpi_data (period, index_value, pct_change_yoy, source)
            VALUES (?, ?, ?, 'abs')
            ON CONFLICT(period, source) DO UPDATE SET
                index_value = excluded.index_value,
                pct_change_yoy = excluded.pct_change_yoy""",
            (period, index_value, pct_change_yoy),
        )
        count += 1

    conn.commit()
    return count


def _fetch_abs_csv(url: str) -> list[dict]:
    """Fetch CSV data from the ABS SDMX API."""
    req = Request(url, headers={"Accept": "text/csv"})
    with urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def get_cpi_index(conn: sqlite3.Connection, d: date) -> float | None:
    """Get the CPI index for a given date by interpolating quarterly data.

    Uses the most recent quarter at or before the given date.
    """
    quarter = _date_to_quarter(d)
    row = conn.execute(
        "SELECT index_value FROM cpi_data WHERE period <= ? AND source = 'abs' ORDER BY period DESC LIMIT 1",
        (quarter,),
    ).fetchone()
    return row["index_value"] if row else None


def get_latest_cpi(conn: sqlite3.Connection) -> dict | None:
    """Get the most recent CPI data point."""
    row = conn.execute(
        "SELECT period, index_value, pct_change_yoy FROM cpi_data WHERE source = 'abs' ORDER BY period DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def get_cpi_history(conn: sqlite3.Connection, from_year: int = 2015) -> list[dict]:
    """Get CPI history from a given year onwards."""
    rows = conn.execute(
        "SELECT period, index_value, pct_change_yoy FROM cpi_data WHERE source = 'abs' AND period >= ? ORDER BY period",
        (f"{from_year}-Q1",),
    ).fetchall()
    return [dict(r) for r in rows]


def adjust_for_inflation(
    amount: float, from_date: date, to_date: date, conn: sqlite3.Connection
) -> float | None:
    """Convert a nominal amount from one date to real terms at another date.

    E.g., adjust_for_inflation(1000, date(2020,1,1), date(2025,1,1), conn)
    tells you what $1000 in Jan 2020 is worth in Jan 2025 dollars.
    """
    from_cpi = get_cpi_index(conn, from_date)
    to_cpi = get_cpi_index(conn, to_date)
    if from_cpi is None or to_cpi is None or from_cpi == 0:
        return None
    return amount * (to_cpi / from_cpi)


def get_cpi_for_year(conn: sqlite3.Connection, year: int) -> float | None:
    """Get the average CPI index for a calendar year (average of available quarters)."""
    rows = conn.execute(
        "SELECT AVG(index_value) as avg_cpi FROM cpi_data WHERE source = 'abs' AND period LIKE ?",
        (f"{year}-%",),
    ).fetchone()
    return rows["avg_cpi"] if rows and rows["avg_cpi"] else None


def _date_to_quarter(d: date) -> str:
    """Convert a date to a quarter string like '2024-Q3'."""
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"
