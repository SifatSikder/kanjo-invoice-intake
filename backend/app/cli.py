"""Operational commands.

    python -m app.cli status    show where every invoice ended up
    python -m app.cli reset     clear our records and the accounting ledger

Invoices enter the system by being uploaded, not from a folder on the server, so
there is no ingest command here. These two exist for inspecting and clearing
state without going through the web app.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.clients.accounting import AccountingClient
from app.config import settings
from app.db import SessionLocal
from app.models import Extraction, Invoice, InvoiceStatus

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("cli")

_LABEL = {
    InvoiceStatus.POSTED: "POSTED",
    InvoiceStatus.NEEDS_REVIEW: "REVIEW",
    InvoiceStatus.BLOCKED: "BLOCKED",
    InvoiceStatus.POST_FAILED: "FAILED",
    InvoiceStatus.EXTRACT_FAILED: "EXTRACT!",
    InvoiceStatus.EXTRACTED: "READY",
    InvoiceStatus.REJECTED: "REJECTED",
    InvoiceStatus.PENDING: "PENDING",
}


def _accounting() -> AccountingClient:
    return AccountingClient(
        settings.accounting_api_base,
        settings.accounting_api_key,
        timeout=settings.accounting_timeout_seconds,
    )


async def cmd_status(args) -> int:
    accounting = _accounting()

    async with SessionLocal() as session:
        invoices = (
            await session.scalars(
                select(Invoice)
                .options(selectinload(Invoice.document), selectinload(Invoice.postings),
                         selectinload(Invoice.checks))
                .order_by(Invoice.id)
            )
        ).all()

        cost = (await session.execute(select(Extraction))).scalars().all()
        total_cost = sum(float(e.cost_usd or 0) for e in cost)
        total_latency = [e.latency_ms for e in cost if e.latency_ms]

    print(f"\n{'file':<18} {'status':<9} {'partner':<8} {'invoice no':<15} "
          f"{'total':>12}  {'acc id':<9} reason")
    print("-" * 118)
    counts: dict[str, int] = {}
    for inv in invoices:
        label = _LABEL.get(inv.status, inv.status.value)
        counts[label] = counts.get(label, 0) + 1
        acc = next((p.accounting_id for p in inv.postings if p.succeeded), "") or ""
        reason = (inv.notes or "")[:44]
        total = f"{inv.total_amount:,}" if inv.total_amount is not None else "-"
        print(f"{inv.document.filename:<18} {label:<9} {inv.partner_code or '-':<8} "
              f"{(inv.invoice_number or '-'):<15} {total:>12}  {acc:<9} {reason}")

    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if cost:
        print(f"extraction cost: ${total_cost:.4f} across {len(cost)} call(s) "
              f"(${total_cost/max(len(cost),1):.4f}/invoice), "
              f"median latency {sorted(total_latency)[len(total_latency)//2] if total_latency else 0}ms")

    try:
        registered = await accounting.list_invoices()
        print(f"registered in the accounting system: {len(registered)}")
    except RuntimeError as exc:
        print(f"could not read the accounting ledger: {exc}")
    return 0


async def cmd_reset(args) -> int:
    accounting = _accounting()
    async with SessionLocal() as session:
        for table in ("review_events", "postings", "check_results", "invoice_lines",
                      "invoices", "extractions", "documents"):
            await session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        await session.commit()
    result = await accounting.delete_all_invoices()
    removed = (result.data or {}).get("removed", 0) if result.ok else "?"
    logger.info("cleared local records and removed %s invoice(s) from the accounting system", removed)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="show where every invoice ended up")
    status.set_defaults(func=cmd_status)

    reset = sub.add_parser("reset", help="clear local records and the accounting ledger")
    reset.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
