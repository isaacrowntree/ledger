import argparse
import shutil
import sys
from pathlib import Path

from etl import db
from etl.models import RawTransaction
from etl.categorizer import Categorizer
from etl.currency import extract_fx_rates
from etl.engine import as_raw_transaction, ingest_statement
from etl.shared import SharedRules, mark_shared
from etl.normalizer import load_payment_patterns
from etl.reconcile import reconcile_account
from etl.tagger import Tagger
from etl.parsers.airbnb_csv import AirbnbCSVParser
from etl.parsers.amex_csv import AmexCSVParser
from etl.parsers.bankwest_account_pdf import BankwestLoanPDFParser, BankwestOffsetPDFParser
from etl.parsers.bankwest_csv import BankwestCSVParser
from etl.parsers.bankwest_pdf import BankwestPDFParser
from etl.parsers.cba_cc_pdf import CBACreditPDFParser
from etl.parsers.cba_pdf import CBAPDFParser
from etl.parsers.coles_csv import ColesCSVParser
from etl.parsers.coles_pdf import ColesCreditPDFParser
from etl.parsers.hsbc_pdf import HSBCPDFParser
from etl.parsers.hsbc_csv import HSBCCSVParser
from etl.parsers.ing_csv import INGCSVParser
from etl.parsers.ing_pdf import INGPDFParser
from etl.parsers.paypal_csv import PayPalCSVParser
from etl.cgt import events_from_notes, summarise
from etl.splitter import load_tax_config, backfill_splits
from etl.dedup import find_duplicates, resolve_duplicates
from etl import rental

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
    "hsbc-csv": (HSBCCSVParser, "hsbc-csv", "*.csv"),
    "coles": (ColesCreditPDFParser, "coles", "*.pdf"),
    "coles-csv": (ColesCSVParser, "coles-csv", "*.csv"),
    "bankwest": (BankwestPDFParser, "bankwest", "*.pdf"),
    "bankwest-csv": (BankwestCSVParser, "bankwest-csv", "*.csv"),
    "amex": (AmexCSVParser, "amex", "*.csv"),
    "cba": (CBAPDFParser, "cba", "*.pdf"),
    "cba-cc": (CBACreditPDFParser, "cba-cc", "*.pdf"),
    # The loan and offset statements share a layout but not an account, so
    # they are separated by staging directory rather than by filename.
    "bankwest-loan": (BankwestLoanPDFParser, "bankwest-loan", "*.pdf"),
    "bankwest-offset": (BankwestOffsetPDFParser, "bankwest-offset", "*.pdf"),
}

# Default account names for non-ING sources (ING uses file_prefix mapping)
ACCOUNT_NAMES = {
    "paypal": "PayPal",
    "ing": "ING Orange Everyday",
    "ing-csv": "ING Orange Everyday",
    "airbnb": "Airbnb",
    "hsbc": "HSBC",
    "hsbc-csv": "HSBC",
    "coles": "Coles Credit Card",
    "coles-csv": "Coles Credit Card",
    "bankwest": "Bankwest",
    "bankwest-csv": "Bankwest",
    "amex": "Amex",
    "cba": "CBA Smart Access",
    "cba-cc": "CBA Awards Credit Card",
    "bankwest-loan": "Bankwest Home Loan",
    "bankwest-offset": "Bankwest Offset",
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
    ingest_parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                               help="Only ingest transactions on or after this date")
    ingest_parser.add_argument("--until", dest="date_until", metavar="YYYY-MM-DD",
                               help="Only ingest transactions on or before this date")
    ingest_parser.add_argument("--force", action="store_true",
                               help="Ingest even if a statement fails validation")

    sub.add_parser("init", help="Initialize database and load config")

    split_parser = sub.add_parser("split", help="Compute business splits for transactions")
    split_parser.add_argument("--backfill", action="store_true", help="Backfill splits for existing transactions")
    split_parser.add_argument("--fy", type=int, help="Financial year (e.g. 2025 for FY 2024-25)")

    tax_parser = sub.add_parser("tax", help="Show ATO tax summary")
    tax_parser.add_argument("--fy", type=int, help="Financial year (e.g. 2025 for FY 2024-25)")

    shared_parser = sub.add_parser(
        "shared", help="Apply shared-expense rules to existing transactions")
    shared_parser.add_argument("--backfill", action="store_true",
                               help="Mark matching transactions not already shared")
    shared_parser.add_argument("--since", metavar="YYYY-MM-DD",
                               help="Only consider transactions from this date")
    shared_parser.add_argument("--dry-run", action="store_true",
                               help="Show what would be marked without writing")

    rec_parser = sub.add_parser("reconcile", help="Check ledger balances against statement closing balances")
    rec_parser.add_argument("--account", help="Only this account (substring match on name)")
    rec_parser.add_argument("--since", metavar="YYYY-MM-DD", help="Only consider statements from this date")
    rec_parser.add_argument("--no-chain", action="store_true",
                            help="Skip the per-record running balance check")
    rec_parser.add_argument("--verbose", action="store_true", help="List every statement anchor")

    cgt_parser = sub.add_parser("cgt", help="Capital gains from CommSec contract notes in staging/commsec/")
    cgt_parser.add_argument("--fy", type=int, required=True, help="Financial year (e.g. 2025 for FY 2024-25)")
    cgt_parser.add_argument("--carried-forward-losses", type=float, default=0.0,
                            help="Net capital losses carried forward from an earlier year (label 18V)")

    dedup_parser = sub.add_parser("dedup", help="Find and resolve cross-account duplicate transactions")
    dedup_parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")

    tag_parser = sub.add_parser("tag", help="Bulk tag/recategorize transactions matching a description pattern")
    tag_parser.add_argument("--pattern", required=True, help="SQL LIKE pattern matched against description (case-insensitive)")
    tag_parser.add_argument("--from", dest="date_from", help="Start date YYYY-MM-DD")
    tag_parser.add_argument("--to", dest="date_to", help="End date YYYY-MM-DD")
    tag_parser.add_argument("--fy", type=int, help="Financial year (alternative to --from/--to)")
    tag_parser.add_argument("--tag", help="Tag to add")
    tag_parser.add_argument("--category", help="Category to set")
    tag_parser.add_argument("--clear-transfer", action="store_true", help="Also clear is_transfer flag")
    tag_parser.add_argument("--dry-run", action="store_true", help="Preview matches without writing")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "ingest":
        cmd_ingest(source=args.source, dry_run=args.dry_run,
                   date_from=args.date_from, date_until=args.date_until,
                   force=args.force)
    elif args.command == "split":
        cmd_split(backfill=args.backfill, fy=args.fy)
    elif args.command == "tax":
        cmd_tax(fy=args.fy)
    elif args.command == "shared":
        cmd_shared(backfill=args.backfill, since=args.since, dry_run=args.dry_run)
    elif args.command == "reconcile":
        cmd_reconcile(account=args.account, since=args.since,
                      chain=not args.no_chain, verbose=args.verbose)
    elif args.command == "cgt":
        cmd_cgt(fy=args.fy, carried_forward_losses=args.carried_forward_losses)
    elif args.command == "dedup":
        cmd_dedup(dry_run=args.dry_run)
    elif args.command == "tag":
        cmd_tag(
            pattern=args.pattern,
            date_from=args.date_from, date_to=args.date_to, fy=args.fy,
            tag=args.tag, category=args.category,
            clear_transfer=args.clear_transfer, dry_run=args.dry_run,
        )
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


def cmd_ingest(source: str | None = None, dry_run: bool = False,
               date_from: str | None = None, date_until: str | None = None,
               force: bool = False):
    """Ingest staged statements.

    date_from/date_until clip each file to a date window before inserting. Use
    this to stitch formats that overlap in time: a bank's PDF and CSV exports
    describe the same transaction with different text and often a different date
    basis (transaction vs posting date), so they cannot dedup against each other
    -- ingest one format per period and clip at the boundary instead.
    """
    conn = db.get_connection()
    db.init_db(conn)
    db.load_categories_from_config(conn, CONFIG_DIR / "categories.yaml")
    db.load_accounts_from_config(conn, CONFIG_DIR / "accounts.yaml")

    categorizer = Categorizer(conn, CONFIG_DIR / "categories.yaml")
    tagger = Tagger(CONFIG_DIR / "categories.yaml")
    shared_rules = SharedRules(CONFIG_DIR / "categories.yaml")
    prefix_map = _build_file_prefix_map(CONFIG_DIR / "accounts.yaml")
    payment_patterns, sot_types = load_payment_patterns(CONFIG_DIR / "accounts.yaml")

    sources_to_process = [source] if source else list(PARSERS.keys())
    total_inserted = 0
    total_skipped = 0
    total_rejected = 0

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
            statement = parser.parse_statement(file_path)
            print(f"  Parsed {len(statement.rows)} transactions"
                  f" [{statement.balance_convention.value}]")

            extract_fx_rates(_fx_candidates(statement), conn)

            result = ingest_statement(
                conn, statement, account_id, categorizer,
                payment_patterns=payment_patterns, source_of_truth_types=sot_types,
                tagger=tagger, shared_rules=shared_rules,
                dry_run=dry_run, force=force,
                date_from=date_from, date_until=date_until,
            )

            for issue in result.issues:
                print(f"  ! {issue}")

            if result.rejected:
                print("  REFUSED -- statement does not account for its own printed "
                      "balances; nothing was ingested. Re-run with --force to override.")
                total_rejected += 1
                continue

            if result.dropped_by_window:
                window = f"{date_from or '...'} to {date_until or '...'}"
                print(f"  Window {window}: dropped {result.dropped_by_window}")

            total_inserted += result.inserted
            total_skipped += result.skipped
            print(f"  Inserted: {result.inserted}, Skipped (duplicates): {result.skipped}")

            # Move file to archive (unless dry run, or a windowed partial import --
            # the file still holds transactions outside the window)
            if not dry_run and not (date_from or date_until):
                archive_dest = ARCHIVE_DIR / staging_subdir
                archive_dest.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file_path), str(archive_dest / file_path.name))
                print(f"  Archived to: {archive_dest / file_path.name}")
            elif not dry_run:
                print("  Left in staging (windowed import)")

    conn.commit()
    conn.close()

    summary = f"\nDone. Total inserted: {total_inserted}, Total skipped: {total_skipped}"
    if total_rejected:
        summary += f", REFUSED: {total_rejected} statement(s)"
    print(summary)


def _fx_candidates(statement):
    """Statement rows in the shape the FX rate extractor reads."""
    return [as_raw_transaction(statement, row) for row in statement.rows]


def cmd_shared(backfill: bool = False, since: str | None = None, dry_run: bool = False):
    """Apply the shared-expense rules to transactions already in the ledger.

    Existing entries are never touched -- a transaction already marked (or
    deliberately left unmarked at a different split) represents a decision, and a
    rule must not overwrite it. This only fills in what the rules would have
    marked at ingest.
    """
    conn = db.get_connection()
    rules = SharedRules(CONFIG_DIR / "categories.yaml")
    if not rules.rules:
        print("No shared_rules configured in config/categories.yaml")
        conn.close()
        return

    sql = """
        SELECT t.id, t.date, t.description, t.amount, a.name AS account
        FROM transactions t JOIN accounts a ON a.id = t.account_id
        WHERE NOT EXISTS (SELECT 1 FROM shared_expenses se WHERE se.transaction_id = t.id)
    """
    params: list = []
    if since:
        sql += " AND t.date >= ?"
        params.append(since)
    sql += " ORDER BY t.date"

    marked = 0
    total_share = 0.0
    for row in conn.execute(sql, params).fetchall():
        txn = RawTransaction(date=row["date"], description=row["description"],
                             amount=row["amount"])
        split_pct = rules.split_for(txn)
        if split_pct is None:
            continue
        share = abs(row["amount"]) * split_pct / 100.0
        total_share += share
        marked += 1
        print(f"  {row['date']}  {row['description'][:44]:46s} "
              f"{row['amount']:>10,.2f}  {split_pct:g}% -> {share:,.2f}")
        if backfill and not dry_run:
            mark_shared(conn, row["id"], split_pct)

    if backfill and not dry_run:
        conn.commit()
        print(f"\nMarked {marked} transaction(s) as shared; share {total_share:,.2f}")
    else:
        print(f"\n{marked} transaction(s) would be marked; share {total_share:,.2f}"
              f"{'' if backfill else '  (use --backfill to write)'}")
    conn.close()


def cmd_reconcile(account: str | None = None, since: str | None = None,
                  chain: bool = True, verbose: bool = False):
    """Reconcile each account against the closing balances printed on its statements."""
    conn = db.get_connection()

    sql = "SELECT id, name, account_type FROM accounts"
    params: list = []
    if account:
        sql += " WHERE name LIKE ?"
        params.append(f"%{account}%")
    sql += " ORDER BY name"
    accounts = conn.execute(sql, params).fetchall()

    total_drifts = 0
    total_breaks = 0
    clean = []

    for account_id, name, account_type in accounts:
        report = reconcile_account(conn, account_id, name, account_type, since=since, chain=chain)
        if not report.anchors:
            continue

        issues = report.anchor_drifts or report.chain_breaks
        if not issues and not verbose:
            clean.append(f"{name} ({len(report.anchors)} statements)")
            continue

        print(f"\n{name}  [{account_type}]  {len(report.anchors)} statement anchors")

        if verbose:
            for a in report.anchors:
                print(f"    {a.period_end}  {a.closing_balance:>12,.2f}  {Path(a.source_file).name}")

        for d in report.anchor_drifts:
            total_drifts += 1
            # drift = ledger - statements. Positive means the ledger records more
            # money coming in (or less going out) than the statements account for.
            direction = ("spending missing, or credits duplicated" if d.drift > 0
                         else "spending duplicated, or credits missing")
            print(f"  ! {d.from_date} -> {d.to_date}  ({d.n_transactions} txns)")
            print(f"      statements imply {d.expected_delta:>12,.2f}")
            print(f"      ledger holds     {d.actual_delta:>12,.2f}")
            print(f"      drift            {d.drift:>12,.2f}   <- {direction} transactions")
            print(f"      between {Path(d.from_file).name}")
            print(f"          and {Path(d.to_file).name}")

        for b in report.chain_breaks:
            total_breaks += 1
            print(f"  ! chain break {b.date} {b.description[:40]!r} amount {b.amount:,.2f}")
            print(f"      balance went {b.prev_balance:,.2f} -> {b.balance:,.2f}, off by {b.drift:,.2f}")
            print(f"      in {Path(b.source_file).name}")

        if report.unanchored_from:
            n = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id = ? AND date > ?",
                             (account_id, report.unanchored_from)).fetchone()[0]
            print(f"  . {n} transactions after the last statement ({report.unanchored_from}) -- not yet verifiable")

    if clean:
        print("\nReconciled clean:")
        for line in clean:
            print(f"  OK  {line}")

    print(f"\n{total_drifts} statement-gap drift(s), {total_breaks} running-balance break(s).")
    conn.close()


def cmd_cgt(fy: int, carried_forward_losses: float = 0.0):
    """Report capital gains for a financial year from CommSec contract notes.

    Notes are read in place rather than archived like statements: a parcel
    bought years ago is still needed to cost a disposal today, so the whole
    history has to stay available.
    """
    notes_dir = STAGING_DIR / "commsec"
    events = events_from_notes(notes_dir)
    if not events:
        print(f"No contract notes found in {notes_dir}")
        return

    summary = summarise(events, fy, carried_forward_losses)

    print(f"\nCapital gains — FY {fy - 1}-{str(fy)[2:]}\n")
    if not summary["events"]:
        print("  No disposals in this financial year.")
        return

    print(f"  {'code':<6}{'units':>9}  {'acquired':<11}{'disposed':<11}"
          f"{'cost base':>12}{'proceeds':>12}{'gain':>12}  discount")
    for e in summary["events"]:
        print(f"  {e.code:<6}{e.units:>9,.0f}  {e.acquired}  {e.disposed}"
              f"{e.cost_base:>12,.2f}{e.proceeds:>12,.2f}{e.gain:>12,.2f}"
              f"  {'yes' if e.discountable else 'no'}")

    print()
    for label, key in [
        ("Gross capital gains", "gross_gains"),
        ("Capital losses", "losses"),
        ("Losses applied", "losses_applied"),
        ("50% discount", "discount"),
        ("Net capital gain", "net_capital_gain"),
        ("Losses carried forward", "losses_carried_forward"),
    ]:
        print(f"  {label:<24}{summary[key]:>12,.2f}")


def cmd_dedup(dry_run: bool = False):
    """Find and resolve cross-account duplicate transactions."""
    conn = db.get_connection()
    db.init_db(conn)

    dupes = find_duplicates(conn)
    print(f"Found {len(dupes)} duplicate pairs")

    if not dupes:
        conn.close()
        return

    updated = resolve_duplicates(conn, dupes, dry_run=dry_run)
    action = "Would mark" if dry_run else "Marked"
    print(f"\n{action} {updated} transactions as transfers")
    conn.close()


def cmd_tag(
    pattern: str,
    date_from: str | None = None,
    date_to: str | None = None,
    fy: int | None = None,
    tag: str | None = None,
    category: str | None = None,
    clear_transfer: bool = False,
    dry_run: bool = False,
):
    """Bulk tag/recategorize transactions matching a description pattern."""
    if not tag and not category and not clear_transfer:
        print("Specify at least one of --tag / --category / --clear-transfer")
        sys.exit(1)

    if fy:
        date_from = f"{fy - 1}-07-01"
        date_to = f"{fy}-06-30"

    conn = db.get_connection()
    db.init_db(conn)

    query = "SELECT t.id, t.date, t.amount, t.description, c.name AS cat FROM transactions t LEFT JOIN categories c ON c.id=t.category_id WHERE UPPER(t.description) LIKE UPPER(?)"
    params: list = [f"%{pattern}%"]
    if date_from:
        query += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND t.date <= ?"
        params.append(date_to)
    query += " ORDER BY t.date"
    rows = conn.execute(query, params).fetchall()

    print(f"Matched {len(rows)} transactions:")
    for r in rows:
        print(f"  {r['date']} ${r['amount']:>10.2f} [{r['cat'] or '-'}] {r['description'][:60]}")

    if dry_run or not rows:
        return

    cat_id = None
    if category:
        cat_id = db.get_category_id(conn, category)
        if cat_id is None:
            print(f"Unknown category: {category}")
            sys.exit(1)

    for r in rows:
        if cat_id is not None:
            conn.execute("UPDATE transactions SET category_id=? WHERE id=?", (cat_id, r["id"]))
        if clear_transfer:
            conn.execute("UPDATE transactions SET is_transfer=0 WHERE id=?", (r["id"],))
        if tag:
            conn.execute(
                "INSERT OR IGNORE INTO transaction_tags(transaction_id, tag) VALUES (?, ?)",
                (r["id"], tag),
            )
    conn.commit()
    conn.close()
    print(f"\nApplied: tag={tag} category={category} clear_transfer={clear_transfer}")


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

    # Rental property allocator
    rental_results = rental.compute_rental_deductions(conn, tax_config, effective_fy)
    if rental_results:
        print(rental.format_summary(rental_results))

    conn.close()


if __name__ == "__main__":
    main()
