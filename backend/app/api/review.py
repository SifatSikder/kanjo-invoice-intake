"""The human half of the pipeline.

A reviewer may correct fields and approve, or reject. Two rules are enforced here
rather than left to the interface:

  1. Every edit is re-verified before it can be saved as approved. A human cannot
     save a correction that the accounting system would reject -- the same check
     ladder runs again over the edited values.
  2. BLOCKER-level findings cannot be overridden. An ERROR means "a person should
     look at this"; a BLOCKER means "this cannot be registered at all", and no
     amount of clicking should turn a duplicate or an unknown supplier into a
     posting. Those need a business decision outside this screen.

Overriding an ERROR is allowed, and is recorded in review_events with the actor,
the note, and the before/after values.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import accounting_client, openrouter_client
from app.api.invoices import to_detail
from app.db import get_session
from app.models import Invoice, InvoiceLine, InvoiceStatus, ReviewEvent
from app.pipeline.orchestrator import (
    Accepted,
    get_partner_master,
    load_invoice,
    process_accepted,
    verify_invoice,
)
from app.pipeline.render import load_rendered
from app.pipeline.post import post_invoice
from app.schemas import InvoiceOut, InvoicePatch

router = APIRouter(prefix="/api/invoices", tags=["review"])


def _snapshot(invoice: Invoice) -> dict:
    return {
        "partner_code": invoice.partner_code,
        "invoice_number": invoice.invoice_number,
        "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "subtotal": invoice.subtotal,
        "tax_amount": invoice.tax_amount,
        "total_amount": invoice.total_amount,
        "status": invoice.status.value,
        "lines": [
            {
                "seq": l.seq, "description": l.description, "quantity": l.quantity,
                "unit": l.unit, "unit_price": l.unit_price, "amount": l.amount,
                "tax_code": l.tax_code,
            }
            for l in invoice.lines
        ],
    }


async def _reverify(session: AsyncSession, invoice: Invoice):
    client = accounting_client()
    master = await get_partner_master(client)
    return await verify_invoice(session, invoice, master)


@router.patch("/{invoice_id}", response_model=InvoiceOut)
async def patch_invoice(
    invoice_id: int, patch: InvoicePatch, session: AsyncSession = Depends(get_session)
) -> InvoiceOut:
    invoice = await load_invoice(session, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    if invoice.status is InvoiceStatus.POSTED:
        raise HTTPException(
            409,
            "this invoice is already registered in the accounting system and cannot "
            "be edited here; correct it in the accounting system instead",
        )

    before = _snapshot(invoice)

    for field in ("partner_code", "invoice_number", "issue_date", "due_date",
                  "subtotal", "tax_amount", "total_amount"):
        value = getattr(patch, field)
        if value is not None:
            setattr(invoice, field, value)

    if patch.lines is not None:
        invoice.lines.clear()
        for index, line in enumerate(patch.lines, start=1):
            invoice.lines.append(
                InvoiceLine(
                    seq=index, description=line.description, quantity=line.quantity,
                    unit=line.unit, unit_price=line.unit_price, amount=line.amount,
                    tax_code=line.tax_code,
                )
            )
        await session.flush()

    result = await _reverify(session, invoice)
    invoice.status = (
        InvoiceStatus.BLOCKED if result.blockers
        else InvoiceStatus.NEEDS_REVIEW if result.errors
        else InvoiceStatus.EXTRACTED
    )
    invoice.notes = result.blocking_reason

    session.add(ReviewEvent(
        invoice_id=invoice.id, actor=patch.actor, action="edit",
        before=before, after=_snapshot(invoice), note=patch.note,
    ))
    await session.commit()

    invoice = await load_invoice(session, invoice_id, fresh=True)
    return to_detail(invoice)


@router.post("/{invoice_id}/approve", response_model=InvoiceOut)
async def approve_invoice(
    invoice_id: int,
    patch: InvoicePatch | None = None,
    session: AsyncSession = Depends(get_session),
) -> InvoiceOut:
    """Register this invoice, after re-checking whatever it currently holds."""
    invoice = await load_invoice(session, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    if invoice.status is InvoiceStatus.POSTED:
        raise HTTPException(409, "already registered")

    actor = patch.actor if patch else "reviewer"
    note = patch.note if patch else None
    before = _snapshot(invoice)

    result = await _reverify(session, invoice)

    # A BLOCKER is not an opinion to override.
    if result.blockers:
        invoice.status = InvoiceStatus.BLOCKED
        invoice.notes = result.blocking_reason
        await session.commit()
        raise HTTPException(
            409,
            {
                "message": "this invoice cannot be registered",
                "blockers": [
                    {"name": c.name, "message": c.message} for c in result.blockers
                ],
            },
        )

    # Stopping an invoice for an altered payee is only worth doing if the
    # resulting phone call is written down. Approving without saying what it
    # established would spend a reviewer's time and keep none of the answer.
    payment_altered = any(
        c.name == "handwriting.on_payment_details" for c in result.errors
    )
    decision = patch.payment_decision if patch else None
    if payment_altered and decision is None:
        raise HTTPException(
            422,
            {
                "message": "Say what you established about the payment details",
                "detail": (
                    "Someone altered the payee account by hand. Record what the "
                    "supplier confirmed before this is filed, so whoever pays it "
                    "knows which account to use."
                ),
            },
        )
    if decision is not None:
        invoice.payment_decision = {
            **decision.model_dump(),
            "printed_account": invoice.bank_details or "",
            "recorded_by": actor,
        }

    overridden = [{"name": c.name, "message": c.message} for c in result.errors]

    posting = await post_invoice(session, accounting_client(), invoice)

    session.add(ReviewEvent(
        invoice_id=invoice.id,
        actor=actor,
        action="approve",
        before=before,
        after={
            **_snapshot(invoice),
            "accounting_id": posting.accounting_id,
            "posted": posting.succeeded,
        },
        # What the human accepted responsibility for, recorded explicitly.
        note="; ".join(filter(None, [
            note,
            _describe_payment(invoice.payment_decision) if decision else None,
            f"overrode {len(overridden)} check(s): "
            + ", ".join(c["name"] for c in overridden) if overridden else None,
        ])) or None,
    ))
    await session.commit()

    invoice = await load_invoice(session, invoice_id, fresh=True)
    return to_detail(invoice)


@router.post("/{invoice_id}/reject", response_model=InvoiceOut)
async def reject_invoice(
    invoice_id: int,
    patch: InvoicePatch | None = None,
    session: AsyncSession = Depends(get_session),
) -> InvoiceOut:
    invoice = await load_invoice(session, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    if invoice.status is InvoiceStatus.POSTED:
        raise HTTPException(409, "already registered; cannot reject")

    before = _snapshot(invoice)
    invoice.status = InvoiceStatus.REJECTED
    invoice.notes = (patch.note if patch else None) or "Rejected by a reviewer"
    session.add(ReviewEvent(
        invoice_id=invoice.id, actor=patch.actor if patch else "reviewer",
        action="reject", before=before, after=_snapshot(invoice),
        note=patch.note if patch else None,
    ))
    await session.commit()

    invoice = await load_invoice(session, invoice_id, fresh=True)
    return to_detail(invoice)


@router.post("/{invoice_id}/retry", response_model=InvoiceOut)
async def retry_invoice(
    invoice_id: int, session: AsyncSession = Depends(get_session)
) -> InvoiceOut:
    """Read an invoice again after the first attempt failed.

    Extraction can fail for reasons that have nothing to do with the document --
    a provider timing out, a rate limit, a model briefly unavailable. Without
    this, one bad minute upstream leaves an invoice permanently stuck, and the
    only way out is emptying the whole queue.

    The pages are read back from storage, so this costs one model call and does
    not need the original upload.
    """
    invoice = await load_invoice(session, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    if invoice.status is InvoiceStatus.POSTED:
        raise HTTPException(409, "already registered; nothing to retry")

    document = invoice.document
    try:
        rendered = load_rendered(
            document.storage_path,
            filename=document.filename,
            sha256=document.sha256,
            mime_type=document.mime_type,
        )
    except FileNotFoundError:
        raise HTTPException(
            410,
            "the rendered pages for this document are gone; upload the invoice again",
        )

    accounting = accounting_client()
    invoice.status = InvoiceStatus.PENDING
    invoice.notes = None
    await session.flush()

    await process_accepted(
        session,
        Accepted(invoice=invoice, rendered=rendered),
        openrouter=openrouter_client(),
        accounting=accounting,
        master=await get_partner_master(accounting),
    )

    session.add(ReviewEvent(
        invoice_id=invoice.id, actor="reviewer", action="retry",
        note="Read the document again after the previous attempt failed.",
    ))
    await session.commit()

    invoice = await load_invoice(session, invoice_id, fresh=True)
    return to_detail(invoice)


_PAYMENT_OUTCOMES = {
    "pay_printed": "supplier confirmed the printed account is correct; the pen was wrong",
    "pay_altered": "supplier confirmed the account changed",
    "supplier_unreachable": "supplier could not be reached",
}


def _describe_payment(decision: dict | None) -> str | None:
    """One readable sentence for the audit trail."""
    if not decision:
        return None
    parts = [f"payment details: {_PAYMENT_OUTCOMES.get(decision['outcome'], decision['outcome'])}"]
    if decision.get("account_to_pay"):
        parts.append(f"pay {decision['account_to_pay']}")
    if decision.get("how_confirmed"):
        parts.append(decision["how_confirmed"])
    return "; ".join(parts)
