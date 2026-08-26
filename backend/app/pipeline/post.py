"""Build the accounting payload and register it.

One decision governs this module: **the payload is derived from the line items,
never from the printed totals.**

The accounting system does not take the totals at face value -- it recalculates
them from the lines and rejects anything that disagrees. So the only totals it
will ever accept are the ones the lines produce. The printed 小計 / 消費税 / 合計
are used exclusively as a *check* (see verify.arithmetic.*), which is what turned
up invoice_09's one-yen defect. Sending printed totals instead would have meant
an AMOUNT_MISMATCH and a failed registration with nothing learned.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.accounting import AccountingClient, ApiResult
from app.models import CheckResult, Invoice, InvoiceStatus, Posting, Severity
from app.pipeline.normalize import FALLBACK_UNIT, expected_tax_by_code
from app.schemas import AccountingLine, AccountingPayload

logger = logging.getLogger(__name__)

# How each documented error code changes the invoice's fate.
_STATUS_BY_ERROR = {
    "DUPLICATE_INVOICE": InvoiceStatus.BLOCKED,
    "PARTNER_NOT_FOUND": InvoiceStatus.BLOCKED,
    "AMOUNT_MISMATCH": InvoiceStatus.NEEDS_REVIEW,
    "UNKNOWN_TAX_CODE": InvoiceStatus.NEEDS_REVIEW,
    "DUE_DATE_BEFORE_ISSUE_DATE": InvoiceStatus.NEEDS_REVIEW,
    "VALIDATION_ERROR": InvoiceStatus.NEEDS_REVIEW,
    "UNAUTHORIZED": InvoiceStatus.POST_FAILED,
}


def build_payload(invoice: Invoice) -> AccountingPayload:
    """Assemble the POST body. Totals come from the lines, by design."""
    if not invoice.partner_code:
        raise ValueError("cannot build a payload without a resolved partner_code")
    if not invoice.lines:
        raise ValueError("cannot build a payload with no line items")
    if not invoice.issue_date or not invoice.due_date:
        raise ValueError("cannot build a payload without both dates")

    subtotal = sum(line.amount for line in invoice.lines)
    tax_amount = sum(expected_tax_by_code(invoice.lines).values())

    return AccountingPayload(
        partner_code=invoice.partner_code,
        invoice_number=invoice.invoice_number or "",
        issue_date=invoice.issue_date.isoformat(),
        due_date=invoice.due_date.isoformat(),
        currency="JPY",
        lines=[
            AccountingLine(
                description=line.description,
                quantity=line.quantity,
                # The API rejects an empty unit. Substituting here, at the
                # boundary, keeps our own record honest about what was read.
                unit=line.unit or FALLBACK_UNIT,
                unit_price=line.unit_price,
                amount=line.amount,
                tax_code=line.tax_code,
            )
            for line in invoice.lines
        ],
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=subtotal + tax_amount,
    )


async def post_invoice(
    session: AsyncSession,
    client: AccountingClient,
    invoice: Invoice,
) -> Posting:
    """Register one invoice, recording exactly what was sent and what came back."""
    payload = build_payload(invoice)
    body = payload.model_dump()
    # `postings` is pre-initialised by the orchestrator; avoid a lazy load here.
    attempt = len(invoice.postings) + 1

    result: ApiResult = await client.create_invoice(body)

    # --- the case the API's design leaves us to handle ----------------------
    # No idempotency key exists, so a timeout is ambiguous: the invoice may or
    # may not have registered. Retrying could register it twice -- the very
    # failure this project exists to prevent -- so we read the ledger instead.
    if result.indeterminate:
        logger.warning(
            "indeterminate POST for invoice %s; re-reading the ledger to check", invoice.id
        )
        landed = await client.confirm_registered(
            payload.partner_code, payload.invoice_number
        )
        if landed:
            posting = Posting(
                invoice_id=invoice.id, attempt=attempt, request_payload=body,
                response_status=201, response_body={"recovered": True, "record": landed},
                accounting_id=landed.get("accounting_id"), succeeded=True,
            )
            invoice.status = InvoiceStatus.POSTED
            invoice.notes = (
                "Registration confirmed by re-reading the accounting ledger after a "
                "network timeout; the request was not retried."
            )
            session.add(posting)
            return posting

        posting = Posting(
            invoice_id=invoice.id, attempt=attempt, request_payload=body,
            response_status=0, response_body={"transport_error": result.transport_error},
            error_code="TRANSPORT_ERROR", succeeded=False,
        )
        invoice.status = InvoiceStatus.POST_FAILED
        invoice.notes = f"Could not reach the accounting system: {result.transport_error}"
        session.add(posting)
        return posting

    # --- success -------------------------------------------------------------
    if result.ok:
        accounting_id = (result.data or {}).get("accounting_id")
        posting = Posting(
            invoice_id=invoice.id, attempt=attempt, request_payload=body,
            response_status=result.status, response_body=result.body,
            accounting_id=accounting_id, succeeded=True,
        )
        invoice.status = InvoiceStatus.POSTED
        session.add(posting)
        return posting

    # --- rejection -----------------------------------------------------------
    # The accounting system refusing an invoice is a verification failure like
    # any other -- it just happened one step further downstream. Recording it as
    # a check means the queue, the review screen and the audit trail all learn
    # about it through the path they already use, instead of the reason living
    # only in a notes column that nothing displays.
    _record_rejection(invoice, result)

    posting = Posting(
        invoice_id=invoice.id, attempt=attempt, request_payload=body,
        response_status=result.status, response_body=result.body,
        error_code=result.error_code, succeeded=False,
    )
    invoice.status = _STATUS_BY_ERROR.get(result.error_code or "", InvoiceStatus.POST_FAILED)
    invoice.notes = f"{result.error_code}: {result.error_message}"

    # Our pre-flight arithmetic mirrors the API's exactly, so this branch should
    # be unreachable. If it fires, the two have drifted and that is a bug in us,
    # not a bad invoice -- log it loudly rather than filing it as a review item.
    if result.error_code == "AMOUNT_MISMATCH":
        logger.error(
            "AMOUNT_MISMATCH despite passing pre-flight verification -- our arithmetic "
            "has drifted from the accounting API. invoice=%s details=%s",
            invoice.id, result.error_details,
        )

    session.add(posting)
    return posting


# Rejections that mean "never postable as-is" rather than "a human should look".
_BLOCKING_ERRORS = {"DUPLICATE_INVOICE", "PARTNER_NOT_FOUND"}


def _record_rejection(invoice: Invoice, result: ApiResult) -> None:
    """Turn an API refusal into a failed check, so it surfaces like the rest."""
    code = result.error_code or "REJECTED"
    severity = Severity.BLOCKER if code in _BLOCKING_ERRORS else Severity.ERROR

    # Replace any earlier verdict for this check so a retry does not stack up.
    invoice.checks[:] = [c for c in invoice.checks if c.name != "accounting.accepted"]
    invoice.checks.append(
        CheckResult(
            name="accounting.accepted",
            severity=severity,
            passed=False,
            message=result.error_message or f"The accounting system refused this ({code})",
            detail={
                "error_code": code,
                "http_status": result.status,
                "details": result.error_details,
            },
        )
    )
