#!/usr/bin/env bash
# Wait for the database, apply migrations, then serve. Seeding runs in the
# background so the review screen is reachable immediately and fills in as
# invoices are processed, rather than 500ing for the two minutes it takes.
set -euo pipefail

echo "waiting for the database..."
until python -c "
import asyncio, asyncpg, os
url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://')
async def go():
    conn = await asyncpg.connect(url); await conn.close()
asyncio.run(go())
" 2>/dev/null; do sleep 1; done
echo "database is up"

alembic upgrade head

# Process the sample folder on first boot so the demo has something to show.
# Ingest is idempotent (documents are keyed by content hash), so a restart
# re-runs this harmlessly and finishes instantly.
if [ "${SEED_ON_STARTUP:-false}" = "true" ]; then
  (
    echo "seeding from ${INVOICE_DIR:-/data/invoices} in the background..."
    python -m app.cli ingest 2>&1 | sed 's/^/[seed] /' \
      || echo "[seed] failed; use the Ingest button in the UI to retry"
  ) &
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
