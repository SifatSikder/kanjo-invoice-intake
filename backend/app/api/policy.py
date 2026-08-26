"""The policy dials, as an editable resource.

Changing one re-runs the checks over every invoice that is not already filed, so
the queue reflects the rule that is actually in force rather than the one that
happened to apply when each document arrived. Nothing is registered as a side
effect: raising a limit can make an invoice eligible, but a person still presses
approve.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import accounting_client
from app.db import get_session
from app.models import Invoice, InvoiceStatus, Policy, PolicyChange
from app.pipeline.orchestrator import get_partner_master, verify_invoice
from app.pipeline.policy import FIELDS, get_policy, snapshot

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/policy", tags=["policy"])

# Statuses whose verdict can still change. A filed invoice is settled, and a
# rejected one was a human's decision rather than a rule's.
_REVERIFIABLE = (
    InvoiceStatus.EXTRACTED,
    InvoiceStatus.NEEDS_REVIEW,
    InvoiceStatus.BLOCKED,
)


class PolicyOut(BaseModel):
    auto_post_enabled: bool
    amount_review_threshold_jpy: int
    confidence_floor: float
    near_duplicate_window_days: int
    updated_by: str


class PolicyPatch(BaseModel):
    auto_post_enabled: bool | None = None
    amount_review_threshold_jpy: int | None = Field(default=None, ge=0)
    confidence_floor: float | None = Field(default=None, ge=0, le=1)
    near_duplicate_window_days: int | None = Field(default=None, ge=0, le=365)
    actor: str = "reviewer"


def to_out(policy: Policy) -> PolicyOut:
    return PolicyOut(**snapshot(policy), updated_by=policy.updated_by)


@router.get("", response_model=PolicyOut)
async def read(session: AsyncSession = Depends(get_session)) -> PolicyOut:
    policy = await get_policy(session)
    await session.commit()
    return to_out(policy)


@router.patch("", response_model=dict)
async def update(patch: PolicyPatch, session: AsyncSession = Depends(get_session)) -> dict:
    policy = await get_policy(session)
    before = snapshot(policy)

    for field in FIELDS:
        value = getattr(patch, field)
        if value is not None:
            setattr(policy, field, value)
    policy.updated_by = patch.actor
    after = snapshot(policy)

    if before == after:
        await session.commit()
        return {"changed": False, "policy": to_out(policy).model_dump(), "reverified": 0}

    await session.flush()

    # Re-judge everything still open under the new rule. An invoice held only
    # because of a limit that has just been raised should stop being held.
    master = await get_partner_master(accounting_client())
    invoices = (
        await session.scalars(
            select(Invoice)
            .where(Invoice.status.in_(_REVERIFIABLE))
            .options(
                selectinload(Invoice.lines),
                selectinload(Invoice.checks),
                selectinload(Invoice.postings),
                selectinload(Invoice.document),
                selectinload(Invoice.review_events),
            )
        )
    ).all()

    moved = 0
    for invoice in invoices:
        was = invoice.status
        result = await verify_invoice(session, invoice, master)
        invoice.status = (
            InvoiceStatus.BLOCKED if result.blockers
            else InvoiceStatus.NEEDS_REVIEW if result.errors
            else InvoiceStatus.EXTRACTED
        )
        invoice.notes = result.blocking_reason
        if invoice.status is not was:
            moved += 1

    session.add(PolicyChange(
        actor=patch.actor, before=before, after=after, reverified=len(invoices),
    ))
    await session.commit()

    logger.info(
        "policy changed by %s: %s -> %s (%s re-checked, %s moved)",
        patch.actor, before, after, len(invoices), moved,
    )
    return {
        "changed": True,
        "policy": to_out(policy).model_dump(),
        "reverified": len(invoices),
        "moved": moved,
    }


@router.get("/history")
async def history(session: AsyncSession = Depends(get_session)) -> dict:
    """What the rules were, and who changed them.

    A filing is only explicable alongside the rules in force at the time.
    """
    rows = (
        await session.scalars(
            select(PolicyChange).order_by(PolicyChange.id.desc()).limit(25)
        )
    ).all()
    return {
        "changes": [
            {
                "at": c.created_at.isoformat() if c.created_at else None,
                "actor": c.actor,
                "before": c.before,
                "after": c.after,
                "reverified": c.reverified,
            }
            for c in rows
        ]
    }
