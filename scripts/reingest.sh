#!/bin/bash
# Re-ingest all data from archive. Deletes DB, copies files back to staging, re-runs.
set -e

cd "$(dirname "$0")/.."
PYTHON=".venv/bin/python3"

echo "Deleting database..."
rm -f data/ledger.db data/ledger.db-shm data/ledger.db-wal

echo "Copying archived files to staging..."
for source in ing ing-csv paypal bankwest bankwest-csv coles hsbc amex airbnb; do
  if [ -d "data/archive/$source" ] && [ "$(ls -A data/archive/$source 2>/dev/null)" ]; then
    mkdir -p "staging/$source"
    cp data/archive/$source/* staging/$source/ 2>/dev/null || true
    echo "  $source: $(ls staging/$source/ 2>/dev/null | wc -l | tr -d ' ') files"
  fi
done

echo "Initializing database..."
$PYTHON -m etl init

echo "Ingesting all sources..."
for source in ing ing-csv paypal bankwest bankwest-csv coles hsbc amex airbnb; do
  if [ -d "staging/$source" ] && [ "$(ls -A staging/$source 2>/dev/null)" ]; then
    echo "--- $source ---"
    $PYTHON -m etl ingest --source "$source" 2>&1 | tail -1
  fi
done

# Run post-ingest hooks (create scripts/post-ingest.sh for personal adjustments)
if [ -f "scripts/post-ingest.sh" ]; then
  echo ""
  echo "=== Running post-ingest hooks ==="
  bash scripts/post-ingest.sh
fi

# Backfill business splits
echo ""
echo "=== Backfilling splits ==="
$PYTHON -m etl split --backfill --fy 2025
$PYTHON -m etl split --backfill --fy 2026

echo ""
$PYTHON -c "
from etl import db
conn = db.get_connection()
total = conn.execute('SELECT COUNT(*) as c FROM transactions').fetchone()['c']
cat_id = conn.execute(\"SELECT id FROM categories WHERE name = 'Uncategorized'\").fetchone()['id']
uncat = conn.execute('SELECT COUNT(*) as c FROM transactions WHERE category_id = ?', (cat_id,)).fetchone()['c']
xfer = conn.execute('SELECT COUNT(*) as c FROM transactions WHERE is_transfer = 1').fetchone()['c']
print(f'Done. {total} transactions, {total-uncat} categorized ({(total-uncat)/total*100:.1f}%), {xfer} transfers')
conn.close()
"
