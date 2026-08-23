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

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
