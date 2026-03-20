import argparse
import shutil
import sys
from pathlib import Path

from etl import db
from etl.categorizer import Categorizer
from etl.currency import extract_fx_rates
from etl.normalizer import normalize_and_insert, load_payment_patterns
from etl.tagger import Tagger
from etl.parsers.airbnb_csv import AirbnbCSVParser
from etl.parsers.amex_csv import AmexCSVParser
from etl.parsers.bankwest_csv import BankwestCSVParser
from etl.parsers.bankwest_pdf import BankwestPDFParser
from etl.parsers.coles_pdf import ColesCreditPDFParser
from etl.parsers.hsbc_pdf import HSBCPDFParser
from etl.parsers.ing_csv import INGCSVParser
from etl.parsers.ing_pdf import INGPDFParser
from etl.parsers.paypal_csv import PayPalCSVParser
from etl import basiq
from etl.splitter import load_tax_config, backfill_splits

PROJECT_ROOT = Path(__file__).parent.parent
STAGING_DIR = PROJECT_ROOT / "staging"
ARCHIVE_DIR = PROJECT_ROOT / "data" / "archive"
CONFIG_DIR = PROJECT_ROOT / "config"

# Each source can have multiple parser entries: (ParserClass, staging_subdir, glob_pattern)
PARSERS = {
    "paypal": (PayPalCSVParser, "paypal", "*.csv"),
    "airbnb": (AirbnbCSVParser, "airbnb", "*.csv"),
    "ing": (INGPDFParser, "ing", "*.pdf"),
    "ing-csv": (INGCSVParser, "ing-csv", "*.csv"),
    "hsbc": (HSBCPDFParser, "hsbc", "*.pdf"),
    "coles": (ColesCreditPDFParser, "coles", "*.pdf"),
    "bankwest": (BankwestPDFParser, "bankwest", "*.pdf"),
    "bankwest-csv": (BankwestCSVParser, "bankwest-csv", "*.csv"),
    "amex": (AmexCSVParser, "amex", "*.csv"),
}

# Default account names for non-ING sources (ING uses file_prefix mapping)
ACCOUNT_NAMES = {
    "paypal": "PayPal",
    "ing": "ING Orange Everyday",
    "ing-csv": "ING Orange Everyday",
    "airbnb": "Airbnb",
    "hsbc": "HSBC",
    "coles": "Coles Credit Card",
    "bankwest": "Bankwest",
    "bankwest-csv": "Bankwest",
    "amex": "Amex",
}


def _build_file_prefix_map(config_path: Path) -> dict[str, str]:
    """Load {file_prefix: account_name} from config for sources with file_prefix."""
    import yaml
    with open(config_path) as f:
        config = yaml.safe_load(f)
    prefix_map = {}
    for acct in config.get("accounts", []):
        if "file_prefix" in acct:
            prefix_map[acct["file_prefix"]] = acct["name"]
    return prefix_map


def _resolve_account_from_file(filename: str, prefix_map: dict[str, str], fallback: str) -> str:
    """Match a filename to an account name using prefix map."""
    for prefix, account_name in prefix_map.items():
        if filename.startswith(prefix):
            return account_name
    return fallback


def main():
    parser = argparse.ArgumentParser(description="Ledger ETL - ingest bank statements")
    sub = parser.add_subparsers(dest="command")

    ingest_parser = sub.add_parser("ingest", help="Ingest files from staging/")
    ingest_parser.add_argument("--source", choices=list(PARSERS.keys()), help="Only ingest from this source")
    ingest_parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")

    sub.add_parser("init", help="Initialize database and load config")

    sub.add_parser("connect", help="Connect bank accounts via Basiq")

    sync_parser = sub.add_parser("sync", help="Sync transactions from connected banks via Basiq")
    sync_parser.add_argument("--source", choices=list(basiq.INSTITUTION_IDS.keys()),
                             help="Only sync from this source")
    sync_parser.add_argument("--since", help="Only fetch transactions after this date (YYYY-MM-DD)")
    sync_parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")

    split_parser = sub.add_parser("split", help="Compute business splits for transactions")
    split_parser.add_argument("--backfill", action="store_true", help="Backfill splits for existing transactions")
    split_parser.add_argument("--fy", type=int, help="Financial year (e.g. 2025 for FY 2024-25)")

    tax_parser = sub.add_parser("tax", help="Show ATO tax summary")
    tax_parser.add_argument("--fy", type=int, help="Financial year (e.g. 2025 for FY 2024-25)")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "ingest":
        cmd_ingest(source=args.source, dry_run=args.dry_run)
    elif args.command == "connect":
        cmd_connect()
    elif args.command == "sync":
        cmd_sync(source=args.source, since=args.since, dry_run=args.dry_run)
    elif args.command == "split":
        cmd_split(backfill=args.backfill, fy=args.fy)
    elif args.command == "tax":
        cmd_tax(fy=args.fy)
    else:
        parser.print_help()
        sys.exit(1)


def cmd_init():
    conn = db.get_connection()
    db.init_db(conn)
    db.load_categories_from_config(conn, CONFIG_DIR / "categories.yaml")
    db.load_accounts_from_config(conn, CONFIG_DIR / "accounts.yaml")
    conn.close()
    print("Database initialized and config loaded.")


def cmd_ingest(source: str | None = None, dry_run: bool = False):
    conn = db.get_connection()
    db.init_db(conn)
    db.load_categories_from_config(conn, CONFIG_DIR / "categories.yaml")
    db.load_accounts_from_config(conn, CONFIG_DIR / "accounts.yaml")

    categorizer = Categorizer(conn, CONFIG_DIR / "categories.yaml")
    tagger = Tagger(CONFIG_DIR / "categories.yaml")
    prefix_map = _build_file_prefix_map(CONFIG_DIR / "accounts.yaml")
    payment_patterns, sot_types = load_payment_patterns(CONFIG_DIR / "accounts.yaml")

    sources_to_process = [source] if source else list(PARSERS.keys())
    total_inserted = 0
    total_skipped = 0

    for src in sources_to_process:
        if src not in PARSERS:
            print(f"Parser for '{src}' not yet implemented, skipping.")
            continue

        parser_cls, staging_subdir, glob_pattern = PARSERS[src]
        staging_path = STAGING_DIR / staging_subdir
        files = sorted(set(staging_path.glob(glob_pattern)) | set(staging_path.glob(glob_pattern.upper())))

        if not files:
            print(f"No {glob_pattern} files found in {staging_path}")
            continue

        parser = parser_cls()

        for file_path in files:
            # Resolve account per-file (important for ING multi-account)
            account_name = _resolve_account_from_file(file_path.name, prefix_map, ACCOUNT_NAMES[src])
            account_id = db.ensure_account(conn, account_name, src)

            print(f"\nProcessing: {file_path.name} → {account_name}")
            transactions = parser.parse(file_path)
            print(f"  Parsed {len(transactions)} transactions")

            # Extract FX rates before normalization
            extract_fx_rates(transactions, conn)

            inserted, skipped = normalize_and_insert(
                conn, transactions, account_id, categorizer,
                payment_patterns=payment_patterns, source_of_truth_types=sot_types,
                tagger=tagger, dry_run=dry_run,
            )
            total_inserted += inserted
            total_skipped += skipped
            print(f"  Inserted: {inserted}, Skipped (duplicates): {skipped}")

            # Move file to archive (unless dry run)
            if not dry_run:
                archive_dest = ARCHIVE_DIR / staging_subdir
                archive_dest.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file_path), str(archive_dest / file_path.name))
                print(f"  Archived to: {archive_dest / file_path.name}")

    conn.commit()
    conn.close()

    print(f"\nDone. Total inserted: {total_inserted}, Total skipped: {total_skipped}")


def cmd_connect():
    """Connect bank accounts via Basiq consent flow."""
    state = basiq.load_state()

    # Create or reuse Basiq user
    if "user_id" not in state:
        print("Creating Basiq user...")
        token = basiq.get_server_token()
        user_id = basiq.create_user(token)
        state["user_id"] = user_id
        basiq.save_state(state)
        print(f"Created user: {user_id}")
    else:
        user_id = state["user_id"]
        print(f"Using existing user: {user_id}")

    # Generate consent link
    consent_url = basiq.get_consent_link(user_id)
    print(f"\nOpen this link to connect your bank accounts:\n")
    print(f"  {consent_url}\n")
    print("Supported banks:")
    for source, inst_id in basiq.INSTITUTION_IDS.items():
        name = ACCOUNT_NAMES.get(source, source)
        print(f"  - {name} ({inst_id})")

    print("\nAfter connecting, run 'ledger sync' to pull transactions.")

    # Check for existing connections
    token = basiq.get_server_token()
    connections = basiq.list_connections(token, user_id)
    if connections:
        print(f"\nExisting connections:")
        conn_map = {}
        for c in connections:
            inst_id = c.get("institution", {}).get("id", "")
            source = basiq.INSTITUTION_TO_SOURCE.get(inst_id, "unknown")
            status = c.get("status", "unknown")
            conn_map[source] = c["id"]
            print(f"  - {source}: {status} (connection: {c['id']})")
        state["connections"] = conn_map
        basiq.save_state(state)


def cmd_sync(source: str | None = None, since: str | None = None, dry_run: bool = False):
    """Sync transactions from connected Basiq bank accounts."""
    state = basiq.load_state()
    user_id = state.get("user_id")
    if not user_id:
        print("No Basiq user found. Run 'ledger connect' first.")
        sys.exit(1)

    token = basiq.get_server_token()

    # Discover connections
    connections = basiq.list_connections(token, user_id)
    if not connections:
        print("No bank connections found. Run 'ledger connect' and link your accounts.")
        sys.exit(1)

    # Build connection map: source_type -> connection_id
    conn_map = {}
    for c in connections:
        inst_id = c.get("institution", {}).get("id", "")
        src = basiq.INSTITUTION_TO_SOURCE.get(inst_id)
        if src:
            conn_map[src] = c["id"]
            print(f"Found connection: {ACCOUNT_NAMES.get(src, src)} ({c.get('status', 'unknown')})")

    if source:
        if source not in conn_map:
            print(f"No connection found for '{source}'. Connected sources: {list(conn_map.keys())}")
            sys.exit(1)
        conn_map = {source: conn_map[source]}

    if not conn_map:
        print("No supported bank connections found.")
        sys.exit(1)

    # Auto-detect 'since' from last synced transaction if not provided
    conn = db.get_connection()
    db.init_db(conn)
    db.load_categories_from_config(conn, CONFIG_DIR / "categories.yaml")
    db.load_accounts_from_config(conn, CONFIG_DIR / "accounts.yaml")
    categorizer = Categorizer(conn, CONFIG_DIR / "categories.yaml")

    total_inserted = 0
    total_skipped = 0

    for src, connection_id in conn_map.items():
        print(f"\nSyncing {ACCOUNT_NAMES.get(src, src)}...")

        # Determine 'since' date: use provided, or find last transaction date
        effective_since = since
        if not effective_since:
            row = conn.execute(
                "SELECT MAX(date) as last_date FROM transactions WHERE source_type = ? AND reference_id LIKE 'basiq:%'",
                (src,),
            ).fetchone()
            if row and row["last_date"]:
                effective_since = row["last_date"]
                print(f"  Fetching transactions since {effective_since}")
            else:
                print(f"  Fetching all available transactions")

        # Fetch from Basiq
        txns = basiq.fetch_transactions(token, user_id, connection_id, since=effective_since)
        print(f"  Fetched {len(txns)} transactions from Basiq")

        if not txns:
            continue

        # Convert to RawTransaction
        raw_txns = basiq.basiq_to_raw_transactions(txns, src)
        print(f"  {len(raw_txns)} posted transactions to process")

        # Run through existing normalizer pipeline
        account_name = ACCOUNT_NAMES[src]
        account_id = db.ensure_account(conn, account_name, src)

        inserted, skipped = normalize_and_insert(
            conn, raw_txns, account_id, categorizer,
            payment_patterns=load_payment_patterns(CONFIG_DIR / "accounts.yaml")[0],
            dry_run=dry_run,
        )
        total_inserted += inserted
        total_skipped += skipped
        print(f"  Inserted: {inserted}, Skipped (duplicates): {skipped}")

    conn.commit()
    conn.close()

    # Update last sync time
    if not dry_run:
        state["last_sync"] = __import__("datetime").datetime.now().isoformat()
        state["connections"] = conn_map
        basiq.save_state(state)

    print(f"\nDone. Total inserted: {total_inserted}, Total skipped: {total_skipped}")


def cmd_split(backfill: bool = False, fy: int | None = None):
    """Compute business splits for transactions."""
    tax_config = load_tax_config(CONFIG_DIR / "tax.yaml")
    if not backfill:
        print("Use --backfill to compute splits for existing transactions.")
        return

    conn = db.get_connection()
    db.init_db(conn)
    effective_fy = fy or tax_config.get("financial_year", 2025)
    print(f"Backfilling splits for FY {effective_fy - 1}-{str(effective_fy)[2:]}...")
    count = backfill_splits(conn, tax_config, fy=effective_fy)
    conn.close()
    print(f"Created {count} splits.")


def cmd_tax(fy: int | None = None):
    """Show ATO tax summary from CLI."""
    tax_config = load_tax_config(CONFIG_DIR / "tax.yaml")
    effective_fy = fy or tax_config.get("financial_year", 2025)
    fy_start = f"{effective_fy - 1}-07-01"
    fy_end = f"{effective_fy}-06-30"

    conn = db.get_connection()
    db.init_db(conn)

    print(f"\n=== ATO Tax Summary: FY {effective_fy - 1}-{str(effective_fy)[2:]} ===\n")

    # Income
    income_rows = conn.execute("""
        SELECT c.name as category, SUM(t.amount) as total, COUNT(*) as count
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.date >= ? AND t.date <= ? AND c.is_income = 1
          AND t.is_transfer = 0 AND a.account_type NOT IN ('loan')
        GROUP BY c.name ORDER BY total DESC
    """, (fy_start, fy_end)).fetchall()

    print("INCOME:")
    total_income = 0
    for r in income_rows:
        print(f"  {r['category']:30s} ${r['total']:>12,.2f}  ({r['count']} txns)")
        total_income += r["total"]
    print(f"  {'Total':30s} ${total_income:>12,.2f}\n")

    # Business expenses (from splits)
    biz_rows = conn.execute("""
        SELECT ts.business_name, SUM(ts.business_amount) as total, COUNT(*) as count
        FROM transaction_splits ts
        JOIN transactions t ON t.id = ts.transaction_id
        WHERE t.date >= ? AND t.date <= ?
        GROUP BY ts.business_name
    """, (fy_start, fy_end)).fetchall()

    if biz_rows:
        print("BUSINESS EXPENSES (from splits):")
        for r in biz_rows:
            print(f"  {r['business_name']:30s} ${r['total']:>12,.2f}  ({r['count']} splits)")

    # Depreciation
    for sched in tax_config.get("depreciation_schedules", []):
        items = [i for i in sched.get("items", []) if i.get("fy") == effective_fy]
        if items:
            print(f"\n  {sched['name']}:")
            for item in items:
                print(f"    {item['description']:28s} ${item['amount']:>12,.2f}")

    # Manual entries
    manual = tax_config.get("manual_entries", {}).get(effective_fy, [])
    if manual:
        print("\nMANUAL ENTRIES:")
        for entry in manual:
            print(f"  {entry['label']:30s} ${entry['amount']:>12,.2f}")

    conn.close()


if __name__ == "__main__":
    main()
