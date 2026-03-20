import argparse
import shutil
import sys
from pathlib import Path

from etl import db
from etl.categorizer import Categorizer
from etl.currency import extract_fx_rates
from etl.normalizer import normalize_and_insert
from etl.parsers.paypal_csv import PayPalCSVParser

PROJECT_ROOT = Path(__file__).parent.parent
STAGING_DIR = PROJECT_ROOT / "staging"
ARCHIVE_DIR = PROJECT_ROOT / "data" / "archive"
CONFIG_DIR = PROJECT_ROOT / "config"

PARSERS = {
    "paypal": (PayPalCSVParser, "paypal", "*.csv"),
}

# Account names matching config/accounts.yaml
ACCOUNT_NAMES = {
    "paypal": "PayPal",
    "ing": "ING Orange Everyday",
    "airbnb": "Airbnb",
}


def main():
    parser = argparse.ArgumentParser(description="Ledger ETL - ingest bank statements")
    sub = parser.add_subparsers(dest="command")

    ingest_parser = sub.add_parser("ingest", help="Ingest files from staging/")
    ingest_parser.add_argument("--source", choices=["paypal", "ing", "airbnb"], help="Only ingest from this source")
    ingest_parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")

    sub.add_parser("init", help="Initialize database and load config")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "ingest":
        cmd_ingest(source=args.source, dry_run=args.dry_run)
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

    sources_to_process = [source] if source else list(PARSERS.keys())
    total_inserted = 0
    total_skipped = 0

    for src in sources_to_process:
        if src not in PARSERS:
            print(f"Parser for '{src}' not yet implemented, skipping.")
            continue

        parser_cls, staging_subdir, glob_pattern = PARSERS[src]
        staging_path = STAGING_DIR / staging_subdir
        files = sorted(staging_path.glob(glob_pattern))

        if not files:
            print(f"No {glob_pattern} files found in {staging_path}")
            continue

        parser = parser_cls()
        account_name = ACCOUNT_NAMES[src]
        account_id = db.ensure_account(conn, account_name, src)

        for file_path in files:
            print(f"\nProcessing: {file_path.name}")
            transactions = parser.parse(file_path)
            print(f"  Parsed {len(transactions)} transactions")

            # Extract FX rates before normalization
            extract_fx_rates(transactions, conn)

            inserted, skipped = normalize_and_insert(
                conn, transactions, account_id, categorizer, dry_run=dry_run
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


if __name__ == "__main__":
    main()
