"""Dashboard figures, the supplier master, and batch operations."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import accounting_client
from app.config import settings
from app.db import get_session
from app.models import Document, Extraction, Invoice, InvoiceStatus, ReviewEvent
from app.schemas import DashboardStats

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["admin"])

@router.get("/stats", response_model=DashboardStats)
async def stats(session: AsyncSession = Depends(get_session)) -> DashboardStats:
    by_status = {
        status.value: count
        for status, count in (
            await session.execute(
                select(Invoice.status, func.count(Invoice.id)).group_by(Invoice.status)
            )
        ).all()
    }

    documents = await session.scalar(select(func.count(Document.id))) or 0
    cost = await session.scalar(select(func.coalesce(func.sum(Extraction.cost_usd), 0))) or 0
    latency = await session.scalar(select(func.avg(Extraction.latency_ms))) or 0

    # An invoice counts as "posted after review" when a human approved it.
    reviewed_ids = set(
        (
            await session.scalars(
                select(ReviewEvent.invoice_id).where(ReviewEvent.action == "approve")
            )
        ).all()
    )
    posted_ids = set(
        (
            await session.scalars(
                select(Invoice.id).where(Invoice.status == InvoiceStatus.POSTED)
            )
        ).all()
    )
    posted_after_review = len(posted_ids & reviewed_ids)
    auto_posted = len(posted_ids) - posted_after_review

    needs_review = by_status.get(InvoiceStatus.NEEDS_REVIEW.value, 0)
    blocked = by_status.get(InvoiceStatus.BLOCKED.value, 0)

    try:
        registered = len(await accounting_client().list_invoices())
    except RuntimeError:
        registered = -1

    total = sum(by_status.values()) or 1
    return DashboardStats(
        total_documents=documents,
        by_status=by_status,
        auto_posted=auto_posted,
        posted_after_review=posted_after_review,
        needs_review=needs_review,
        blocked=blocked,
        total_cost_usd=round(float(cost), 6),
        avg_latency_ms=int(latency),
        # The number that decides whether this project pays for itself: the share
        # of invoices that never cost a human a minute.
        auto_pass_rate=round(auto_posted / total, 4),
        registered_in_accounting=registered,
    )


@router.get("/partners")
async def partners() -> dict:
    """The supplier master, for the review screen's picker."""
    return {"partners": await accounting_client().get_partners()}


@router.post("/admin/reset")
async def reset(session: AsyncSession = Depends(get_session)) -> dict:
    """Clear our records and the accounting ledger, for a clean demo run."""
    for table in ("review_events", "postings", "check_results", "invoice_lines",
                  "invoices", "extractions", "documents"):
        await session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    await session.commit()
    result = await accounting_client().delete_all_invoices()
    return {"cleared": True, "accounting_removed": (result.data or {}).get("removed", 0)}


@router.get("/config")
async def config() -> dict:
    """The policy dials, surfaced so the review screen can explain its own decisions."""
    return {
        "extraction_model": settings.extraction_model,
        "auto_post_enabled": settings.auto_post_enabled,
        "confidence_floor": settings.confidence_floor,
        "amount_review_threshold_jpy": settings.amount_review_threshold_jpy,
        "near_duplicate_window_days": settings.near_duplicate_window_days,
    }
