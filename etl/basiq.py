"""Basiq API client for syncing Australian bank transactions."""

import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from etl.models import RawTransaction

CONFIG_PATH = Path(__file__).parent.parent / "data" / "basiq_state.json"

# Basiq institution IDs for supported banks
INSTITUTION_IDS = {
    "ing": "AU00201",
    "hsbc": "AU07201",
    "bankwest": "AU00401",
    "coles": "AU15301",
}

# Map Basiq institution IDs back to our source types
INSTITUTION_TO_SOURCE = {v: k for k, v in INSTITUTION_IDS.items()}

BASE_URL = "https://au-api.basiq.io"


def _get_api_key() -> str:
    key = os.environ.get("BASIQ_API_KEY")
    if not key:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
        key = os.environ.get("BASIQ_API_KEY")
    if not key:
        raise RuntimeError("BASIQ_API_KEY not set. Add it to .env")
    return key


def _request(method: str, path: str, token: str, data: Optional[dict] = None) -> dict:
    url = f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "basiq-version": "3.0",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        raise RuntimeError(f"Basiq API error {e.code}: {error_body}")


def get_server_token() -> str:
    api_key = _get_api_key()
    data = urllib.parse.urlencode({"scope": "SERVER_ACCESS"}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/token",
        data=data,
        headers={
            "Authorization": f"Basic {api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "basiq-version": "3.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def get_client_token(user_id: str) -> str:
    api_key = _get_api_key()
    data = urllib.parse.urlencode({
        "scope": "CLIENT_ACCESS",
        "userId": user_id,
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/token",
        data=data,
        headers={
            "Authorization": f"Basic {api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "basiq-version": "3.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


def load_state() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(state, f, indent=2)


def create_user(token: str) -> str:
    result = _request("POST", "/users", token, {"email": "ledger@localhost"})
    return result["id"]


def get_consent_link(user_id: str) -> str:
    client_token = get_client_token(user_id)
    return f"https://consent.basiq.io/home?token={client_token}"


def list_connections(token: str, user_id: str) -> list[dict]:
    result = _request("GET", f"/users/{user_id}/connections", token)
    return result.get("data", [])


def refresh_connection(token: str, user_id: str, connection_id: str) -> dict:
    return _request("POST", f"/users/{user_id}/connections/{connection_id}/refresh", token)


def wait_for_connection_job(token: str, job_url: str, timeout: int = 120) -> bool:
    """Wait for a connection refresh job to complete."""
    # job_url is a full URL, extract the path
    path = job_url.replace(BASE_URL, "")
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _request("GET", path, token)
        steps = result.get("steps", [])
        if all(s.get("status") == "success" for s in steps):
            return True
        if any(s.get("status") == "failed" for s in steps):
            failed = [s for s in steps if s.get("status") == "failed"]
            print(f"  Connection refresh failed: {failed}")
            return False
        time.sleep(3)
    print("  Timeout waiting for connection refresh")
    return False


def fetch_transactions(
    token: str,
    user_id: str,
    connection_id: Optional[str] = None,
    since: Optional[str] = None,
) -> list[dict]:
    """Fetch all transactions, paginating through results.

    Args:
        token: Server access token
        user_id: Basiq user ID
        connection_id: Optional filter to specific connection
        since: Optional date filter (YYYY-MM-DD) - only fetch transactions after this date
    """
    params = {"limit": "500"}
    if connection_id:
        params["filter"] = f"connection.id.eq('{connection_id}')"
    if since:
        existing_filter = params.get("filter", "")
        date_filter = f"transaction.postDate.gteq('{since}')"
        if existing_filter:
            params["filter"] = f"{existing_filter},{date_filter}"
        else:
            params["filter"] = date_filter

    query = urllib.parse.urlencode(params)
    path = f"/users/{user_id}/transactions?{query}"

    all_transactions = []
    while path:
        result = _request("GET", path, token)
        all_transactions.extend(result.get("data", []))
        # Handle pagination
        next_link = result.get("links", {}).get("next")
        if next_link:
            path = next_link.replace(BASE_URL, "")
        else:
            path = None

    return all_transactions


def basiq_to_raw_transactions(
    basiq_txns: list[dict],
    source_type: str,
) -> list[RawTransaction]:
    """Convert Basiq transaction objects to RawTransaction."""
    transactions = []
    for txn in basiq_txns:
        if txn.get("status") != "posted":
            continue

        amount_str = txn.get("amount", "0")
        amount = float(amount_str)
        date = txn.get("postDate", "")[:10]  # YYYY-MM-DD
        description = txn.get("description", "")
        reference = txn.get("id", "")
        currency = txn.get("currency", "AUD")

        raw = RawTransaction(
            date=date,
            description=description,
            amount=amount,
            currency=currency,
            reference_id=f"basiq:{reference}",
            source_type=source_type,
            source_file=f"basiq_sync",
            raw_data={
                "basiq_id": txn.get("id"),
                "direction": txn.get("direction"),
                "class": txn.get("class"),
                "balance": txn.get("balance"),
                "institution": txn.get("institution"),
                "connection": txn.get("connection"),
                "status": txn.get("status"),
                "enrich": txn.get("enrich"),
            },
        )
        transactions.append(raw)

    return transactions
