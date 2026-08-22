"""Duplicate detection.

The client's email names this as the failure that nearly cost them real money:
"a typo nearly caused us to pay the same invoice twice". So this runs against our
own records *before* we call the accounting system, rather than letting the API
answer with a bare 409. Two reasons:

  1. The reviewer can be told *which* invoice this duplicates, and see both.
  2. A near-duplicate -- same supplier, same total, a few days apart, but a
     different invoice number -- would sail straight past the API's exact
     (partner_code, invoice_number) uniqueness rule. That is the shape a
     re-issued or re-sent invoice actually takes.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Invoice, InvoiceStatus, Posting
from app.pipeline.verify import DuplicateFindings

# Statuses that mean "this invoice already occupies that invoice number".
# A rejected invoice does not block a corrected resubmission.
_ACTIVE = (
    InvoiceStatus.POSTED,
    InvoiceStatus.NEEDS_REVIEW,
    InvoiceStatus.BLOCKED,
    InvoiceStatus.EXTRACTED,
    InvoiceStatus.POST_FAILED,
)


def find_duplicates_in(
    *,
    partner_code: str | None,
    invoice_number: str | None,
    total_amount: int | None,
    issue_date: date | None,
    existing: list[dict],
    window_days: int = 7,
) -> DuplicateFindings:
    """Pure matching logic, so it can be unit tested without a database.

    `existing` entries need: partner_code, invoice_number, total_amount,
    issue_date, filename, accounting_id, invoice_id.
    """
    findings = DuplicateFindings()
    if not partner_code:
        return findings

    number = (invoice_number or "").strip().upper()

    for row in existing:
        if row.get("partner_code") != partner_code:
            continue

        # --- exact: the same invoice number for the same supplier -------------
        if number and (row.get("invoice_number") or "").strip().upper() == number:
            findings.exact = row
            return findings  # nothing beats an exact hit

        # --- near: same supplier, same money, close in time ------------------
        if (
            total_amount is not None
            and row.get("total_amount") == total_amount
            and issue_date
            and row.get("issue_date")
            and abs((issue_date - row["issue_date"]).days) <= window_days
        ):
            findings.near.append(row)

    return findings


async def find_duplicates(
    session: AsyncSession,
    *,
    partner_code: str | None,
    invoice_number: str | None,
    total_amount: int | None,
    issue_date: date | None,
    exclude_invoice_id: int | None = None,
    window_days: int = 7,
) -> DuplicateFindings:
    """Database-backed wrapper around `find_duplicates_in`."""
    if not partner_code:
        return DuplicateFindings()

    stmt = (
        select(Invoice, Posting.accounting_id)
        .outerjoin(
            Posting,
            (Posting.invoice_id == Invoice.id) & (Posting.succeeded.is_(True)),
        )
        .where(Invoice.partner_code == partner_code)
        .where(Invoice.status.in_(_ACTIVE))
    )
    if exclude_invoice_id is not None:
        stmt = stmt.where(Invoice.id != exclude_invoice_id)

    rows = (await session.execute(stmt)).all()
    existing = [
        {
            "invoice_id": inv.id,
            "document_id": inv.document_id,
            "partner_code": inv.partner_code,
            "invoice_number": inv.invoice_number,
            "total_amount": inv.total_amount,
            "issue_date": inv.issue_date,
            "filename": inv.document.filename if inv.document else None,
            "accounting_id": accounting_id,
            "status": inv.status.value,
        }
        for inv, accounting_id in rows
    ]
    return find_duplicates_in(
        partner_code=partner_code,
        invoice_number=invoice_number,
        total_amount=total_amount,
        issue_date=issue_date,
        existing=existing,
        window_days=window_days,
    )
