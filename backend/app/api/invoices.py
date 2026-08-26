"""Read endpoints for the review screen."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import Invoice, InvoiceStatus
from app.pipeline.orchestrator import load_invoice
from app.schemas import CheckOut, InvoiceOut, InvoiceSummary, LineOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/invoices", tags=["invoices"])

# The order the review queue should be worked in: things that can never post
# first (someone has to make a decision), then things awaiting a human.
_QUEUE_ORDER = {
    InvoiceStatus.BLOCKED: 0,
    InvoiceStatus.NEEDS_REVIEW: 1,
    InvoiceStatus.POST_FAILED: 2,
    InvoiceStatus.EXTRACT_FAILED: 3,
    InvoiceStatus.EXTRACTED: 4,
    InvoiceStatus.PENDING: 5,
    InvoiceStatus.POSTED: 6,
    InvoiceStatus.REJECTED: 7,
}


def to_summary(invoice: Invoice) -> InvoiceSummary:
    accounting_id = next((p.accounting_id for p in invoice.postings if p.succeeded), None)
    failed = [c for c in invoice.checks if not c.passed and c.severity.value in ("BLOCKER", "ERROR")]
    # If something stopped this invoice, the queue has to say what. `notes`
    # carries reasons that arrived after the checks ran -- an extraction that
    # failed, an accounting system that refused -- and a blank "what's wrong"
    # column on a blocked row is the silent failure this whole pipeline exists
    # to prevent.
    stopped = invoice.status.value in ("BLOCKED", "POST_FAILED", "EXTRACT_FAILED")
    reason = failed[0].message if failed else (invoice.notes if stopped else None)
    return InvoiceSummary(
        id=invoice.id,
        status=invoice.status.value,
        filename=invoice.document.filename,
        partner_code=invoice.partner_code,
        partner_name_raw=invoice.partner_name_raw,
        invoice_number=invoice.invoice_number,
        issue_date=invoice.issue_date,
        total_amount=invoice.total_amount,
        accounting_id=accounting_id,
        blocking_reason=reason,
        failed_checks=len(failed),
    )


def to_detail(invoice: Invoice) -> InvoiceOut:
    accounting_id = next((p.accounting_id for p in invoice.postings if p.succeeded), None)
    return InvoiceOut(
        id=invoice.id,
        status=invoice.status.value,
        filename=invoice.document.filename,
        document_id=invoice.document_id,
        document_sha=invoice.document.sha256,
        page_count=invoice.document.page_count,
        partner_code=invoice.partner_code,
        partner_name_raw=invoice.partner_name_raw,
        partner_registration_no=invoice.partner_registration_no,
        partner_match_method=invoice.partner_match_method.value,
        partner_confidence=invoice.partner_confidence,
        invoice_number=invoice.invoice_number,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        issue_date_raw=invoice.issue_date_raw,
        due_date_raw=invoice.due_date_raw,
        subtotal=invoice.subtotal,
        tax_amount=invoice.tax_amount,
        total_amount=invoice.total_amount,
        min_confidence=invoice.min_confidence,
        has_handwriting=invoice.has_handwriting,
        notes=invoice.notes,
        accounting_id=accounting_id,
        created_at=invoice.created_at,
        lines=[
            LineOut(
                id=l.id, seq=l.seq, description=l.description, quantity=l.quantity,
                unit=l.unit, unit_price=l.unit_price, amount=l.amount, tax_code=l.tax_code,
            )
            for l in invoice.lines
        ],
        checks=[
            CheckOut(
                name=c.name, severity=c.severity.value, passed=c.passed,
                message=c.message, detail=c.detail,
            )
            for c in invoice.checks
        ],
    )


@router.get("", response_model=list[InvoiceSummary])
async def list_invoices(session: AsyncSession = Depends(get_session)) -> list[InvoiceSummary]:
    invoices = (
        await session.scalars(
            select(Invoice).options(
                selectinload(Invoice.document),
                selectinload(Invoice.postings),
                selectinload(Invoice.checks),
            )
        )
    ).all()
    ordered = sorted(invoices, key=lambda i: (_QUEUE_ORDER.get(i.status, 9), i.id))
    return [to_summary(i) for i in ordered]


@router.get("/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(
    invoice_id: int, session: AsyncSession = Depends(get_session)
) -> InvoiceOut:
    invoice = await load_invoice(session, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    return to_detail(invoice)


@router.get("/{invoice_id}/pages/{page}")
async def get_page_image(
    invoice_id: int, page: int, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    """Serve the rendered page so a reviewer can read the document beside the data."""
    invoice = await load_invoice(session, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")
    path = Path(invoice.document.storage_path) / f"page-{page:02d}.jpg"
    if not path.exists():
        raise HTTPException(404, "page image not found")
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/{invoice_id}")
async def delete_invoice(
    invoice_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """Remove one invoice and the document it came from.

    Deleting a *registered* invoice removes our record, not the registration --
    the accounting system is the system of record and we cannot un-file
    something there. That is deliberately not hidden from the person doing it.

    It fails safe: re-uploading the same document afterwards is no longer caught
    by our own duplicate check, but the accounting system still refuses it with
    DUPLICATE_INVOICE, because (partner_code, invoice_number) is still taken
    there.
    """
    invoice = await load_invoice(session, invoice_id)
    if invoice is None:
        raise HTTPException(404, "invoice not found")

    document = invoice.document
    accounting_id = next((p.accounting_id for p in invoice.postings if p.succeeded), None)
    storage_path = document.storage_path if document else None

    # The Document cascades to its invoice, lines, checks, postings and review
    # events, so one delete takes the whole record.
    await session.delete(document if document else invoice)
    await session.commit()

    # Rendered pages are reproducible from nothing once the source is gone.
    if storage_path:
        shutil.rmtree(Path(storage_path), ignore_errors=True)

    logger.info("deleted invoice %s (%s)", invoice_id, accounting_id or "not registered")
    return {
        "deleted": invoice_id,
        "was_registered": accounting_id is not None,
        "accounting_id": accounting_id,
        "note": (
            "Removed from this queue. It remains registered in the accounting "
            "system as " + accounting_id + "."
        )
        if accounting_id
        else "Removed.",
    }
