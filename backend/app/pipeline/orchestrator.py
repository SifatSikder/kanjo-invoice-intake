"""The pipeline state machine.

    upload -> render -> extract -> normalise -> resolve supplier -> dedupe
           -> verify -> {auto-post | review queue | blocked}

Two properties are worth calling out.

Idempotent intake: documents are keyed by SHA-256 of their bytes, so uploading
the same file twice does nothing the second time. The client's complaint was
about paying an invoice twice, and a pipeline that registers a re-sent document
again would be reproducing it.

Nothing is posted that has not passed every check, or been approved by a named
human whose decision is recorded in review_events.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clients.accounting import AccountingClient
from app.clients.openrouter import OpenRouterClient
from app.config import Settings, settings as default_settings
from app.models import (
    CheckResult,
    Document,
    Extraction,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    MatchMethod,
)
from app.pipeline.dedupe import find_duplicates
from app.pipeline.extract import PROMPT_VERSION, extract_document, normalize_extraction
from app.pipeline.partners import PartnerMaster, PartnerMatch
from app.pipeline.policy import read_policy
from app.pipeline.post import post_invoice
from app.pipeline.render import RenderedDocument, prepare_document
from app.pipeline.verify import VerificationResult, run_checks
from app.schemas import NormalizedInvoice, NormalizedLine

logger = logging.getLogger(__name__)

_master_cache: PartnerMaster | None = None

# Extraction runs concurrently -- it is slow, network-bound and independent per
# document. Deciding an invoice's fate and registering it does NOT, because that
# decision depends on every invoice already registered. Two copies of the same
# invoice arriving in one upload could otherwise both read "not a duplicate"
# before either had committed, and both would register: precisely the double
# payment this project exists to prevent.
#
# The guarded section is a duplicate lookup, an in-memory check ladder and one
# HTTP POST, so serialising it costs milliseconds against a ten-second
# extraction. Across multiple API workers this lock would not hold; there the
# answer is a partial unique index on (partner_code, invoice_number) in
# Postgres, which is noted in the submission as a scaling step rather than
# built here for a single-process deployment.
_registration_lock = asyncio.Lock()


async def get_partner_master(client: AccountingClient, *, refresh: bool = False) -> PartnerMaster:
    """The supplier master always comes from the accounting system, never a local copy."""
    global _master_cache
    if _master_cache is None or refresh:
        _master_cache = PartnerMaster(await client.get_partners())
        logger.info("loaded %s partners from the accounting system", len(_master_cache))
    return _master_cache


def _jsonable(value):
    """Coerce a check detail into something JSONB will accept.

    Check details are free-form diagnostic payloads assembled from database rows
    and parsed values, so they legitimately contain dates and Decimals. Rather
    than force every check to remember to stringify, normalise once here.
    """
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _persist_verification(invoice: Invoice, result: VerificationResult) -> None:
    invoice.checks.clear()
    for check in result.checks:
        invoice.checks.append(
            CheckResult(
                name=check.name,
                severity=check.severity,
                passed=check.passed,
                message=check.message,
                detail=_jsonable(check.detail),
            )
        )


def _apply_normalized(invoice: Invoice, normalized: NormalizedInvoice, match) -> None:
    invoice.partner_code = match.partner_code
    invoice.partner_match_method = match.method
    invoice.partner_confidence = match.confidence
    invoice.partner_name_raw = normalized.supplier_name
    invoice.partner_registration_no = normalized.supplier_registration_no
    invoice.invoice_number = normalized.invoice_number
    invoice.issue_date = normalized.issue_date
    invoice.due_date = normalized.due_date
    invoice.issue_date_raw = normalized.issue_date_raw
    invoice.due_date_raw = normalized.due_date_raw
    invoice.currency = normalized.currency
    invoice.subtotal = normalized.subtotal
    invoice.tax_amount = normalized.tax_amount
    invoice.total_amount = normalized.total_amount
    invoice.min_confidence = normalized.min_confidence
    invoice.has_handwriting = normalized.has_handwriting

    invoice.lines.clear()
    for line in normalized.lines:
        invoice.lines.append(
            InvoiceLine(
                seq=line.seq,
                description=line.description,
                quantity=line.quantity,
                unit=line.unit,
                unit_price=line.unit_price,
                amount=line.amount,
                tax_code=line.tax_code,
                tax_rate_raw=line.tax_rate_raw,
            )
        )


async def verify_invoice(
    session: AsyncSession,
    invoice: Invoice,
    master: PartnerMaster,
    *,
    text_layer: str | None = None,
    config: Settings | None = None,
) -> VerificationResult:
    """Re-run the full check ladder against whatever the invoice currently holds.

    Used both on first pass and after a reviewer edits, so a human cannot save a
    correction that the accounting system would reject.
    """
    config = config or default_settings
    # Handwriting severity was decided at extraction time from what the model saw;
    # a re-verify cannot re-read the page, so carry the earlier verdict forward.
    affects_payment = any(
        c.name == "handwriting.on_payment_details" and not c.passed for c in invoice.checks
    )
    handwriting_notes = next(
        (c.message for c in invoice.checks if c.name == "handwriting.detected" and not c.passed),
        "",
    )

    normalized = NormalizedInvoice(
        supplier_name=invoice.partner_name_raw or "",
        supplier_registration_no=invoice.partner_registration_no or "",
        invoice_number=invoice.invoice_number or "",
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        issue_date_raw=invoice.issue_date_raw or "",
        due_date_raw=invoice.due_date_raw or "",
        subtotal=invoice.subtotal,
        tax_amount=invoice.tax_amount,
        total_amount=invoice.total_amount,
        lines=[
            NormalizedLine(
                seq=line.seq, description=line.description, quantity=line.quantity,
                unit=line.unit, unit_price=line.unit_price, amount=line.amount,
                tax_code=line.tax_code, tax_rate_raw=line.tax_rate_raw,
            )
            for line in invoice.lines
        ],
        min_confidence=invoice.min_confidence,
        has_handwriting=invoice.has_handwriting,
        handwriting_affects_payment=affects_payment,
        handwriting_notes=handwriting_notes,
    )

    match = master.resolve(
        invoice.partner_name_raw, invoice.partner_registration_no,
        fuzzy_threshold=config.fuzzy_partner_threshold,
    )
    # A reviewer may have chosen the partner by hand; respect that over matching.
    if invoice.partner_code and match.partner_code != invoice.partner_code:
        if master.get(invoice.partner_code):
            match = PartnerMatch(
                partner_code=invoice.partner_code,
                method=MatchMethod.EXACT_NAME,
                confidence=1.0,
                detail={"source": "chosen by a reviewer"},
            )

    policy = await read_policy(session)
    duplicates = await find_duplicates(
        session,
        partner_code=match.partner_code,
        invoice_number=invoice.invoice_number,
        total_amount=invoice.total_amount,
        issue_date=invoice.issue_date,
        exclude_invoice_id=invoice.id,
        window_days=policy.near_duplicate_window_days,
    )

    result = run_checks(
        normalized, match,
        duplicates=duplicates,
        text_layer=text_layer,
        confidence_floor=policy.confidence_floor,
        amount_review_threshold=policy.amount_review_threshold_jpy,
    )
    invoice.partner_code = match.partner_code
    invoice.partner_match_method = match.method
    invoice.partner_confidence = match.confidence
    _persist_verification(invoice, result)
    return result


@dataclass
class Accepted:
    """The result of taking a document in, before anything has been read."""

    invoice: Invoice
    rendered: RenderedDocument
    already_ingested: bool = False


async def accept_document(
    session: AsyncSession,
    path: Path,
    *,
    config: Settings | None = None,
) -> Accepted:
    """Take a document in and record it, without reading it yet.

    Split out from processing so an upload can return the moment the file is
    safely stored: the person who dropped it sees it appear immediately as
    "reading", rather than watching a spinner for ten seconds with no feedback
    and no idea whether the upload even landed.

    Rendering happens here because it is fast, local, and produces the SHA-256
    that decides whether we have seen this document before.
    """
    config = config or default_settings
    rendered = prepare_document(path, storage_dir=config.storage_dir)

    existing = await session.scalar(
        select(Invoice)
        .join(Document, Document.id == Invoice.document_id)
        .where(Document.sha256 == rendered.sha256)
        .options(selectinload(Invoice.document))
    )
    if existing is not None:
        logger.info("%s already ingested (sha256 %s)", path.name, rendered.sha256[:12])
        return Accepted(invoice=existing, rendered=rendered, already_ingested=True)

    document = Document(
        filename=rendered.filename,
        sha256=rendered.sha256,
        mime_type=rendered.mime_type,
        page_count=rendered.page_count,
        storage_path=str(Path(config.storage_dir) / rendered.sha256),
        has_text_layer=rendered.has_text_layer,
    )
    session.add(document)
    await session.flush()

    # Collections are initialised explicitly. Once the row is flushed it becomes
    # persistent, and touching an unloaded collection would trigger a lazy load --
    # which is synchronous IO, and raises MissingGreenlet under asyncio.
    invoice = Invoice(
        document_id=document.id, status=InvoiceStatus.PENDING,
        lines=[], checks=[], postings=[], review_events=[],
    )
    session.add(invoice)
    await session.flush()
    return Accepted(invoice=invoice, rendered=rendered)


async def process_accepted(
    session: AsyncSession,
    accepted: Accepted,
    *,
    openrouter: OpenRouterClient,
    accounting: AccountingClient,
    master: PartnerMaster,
    config: Settings | None = None,
    auto_post: bool | None = None,
) -> Invoice:
    """Read, verify and register a document that has already been accepted."""
    config = config or default_settings
    auto_post = (await read_policy(session)).auto_post_enabled if auto_post is None else auto_post
    invoice = accepted.invoice
    rendered = accepted.rendered
    document_id = invoice.document_id
    path = Path(rendered.filename)

    # --- extraction ----------------------------------------------------------
    try:
        raw, completion = await extract_document(
            openrouter, rendered, model=config.extraction_model
        )
    except Exception as exc:  # noqa: BLE001 - one bad document must not kill a batch
        logger.exception("extraction failed for %s", path.name)
        session.add(Extraction(
            document_id=document_id, model=config.extraction_model,
            prompt_version=PROMPT_VERSION, raw_response={}, error=str(exc),
        ))
        invoice.status = InvoiceStatus.EXTRACT_FAILED
        invoice.notes = f"Extraction failed: {exc}"
        await session.flush()
        return invoice

    normalized = normalize_extraction(raw)
    extraction = Extraction(
        document_id=document_id,
        model=completion.model,
        prompt_version=PROMPT_VERSION,
        raw_response=raw.model_dump(),
        normalized=normalized.model_dump(mode="json"),
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        cost_usd=completion.cost_usd,
        latency_ms=completion.latency_ms,
    )
    session.add(extraction)
    await session.flush()

    invoice.extraction_id = extraction.id
    invoice.status = InvoiceStatus.EXTRACTED

    match = master.resolve(
        normalized.supplier_name, normalized.supplier_registration_no,
        fuzzy_threshold=config.fuzzy_partner_threshold,
    )
    _apply_normalized(invoice, normalized, match)
    await session.flush()

    # --- verification and registration, serialised (see _registration_lock) ---
    async with _registration_lock:
        return await _decide_and_register(
            session, invoice, normalized, match, rendered, result_config=config,
            accounting=accounting, auto_post=auto_post,
        )


async def _decide_and_register(
    session: AsyncSession,
    invoice: Invoice,
    normalized: NormalizedInvoice,
    match,
    rendered: RenderedDocument,
    *,
    result_config: Settings,
    accounting: AccountingClient,
    auto_post: bool,
) -> Invoice:
    """Run the checks and register. Callers must hold _registration_lock."""
    config = result_config
    policy = await read_policy(session)
    duplicates = await find_duplicates(
        session,
        partner_code=match.partner_code,
        invoice_number=normalized.invoice_number,
        total_amount=normalized.total_amount,
        issue_date=normalized.issue_date,
        exclude_invoice_id=invoice.id,
        window_days=policy.near_duplicate_window_days,
    )
    result = run_checks(
        normalized, match,
        duplicates=duplicates,
        text_layer=rendered.text_layer if rendered.has_text_layer else None,
        confidence_floor=policy.confidence_floor,
        amount_review_threshold=policy.amount_review_threshold_jpy,
    )
    _persist_verification(invoice, result)

    disposition = result.disposition
    if disposition is InvoiceStatus.BLOCKED:
        invoice.status = InvoiceStatus.BLOCKED
        invoice.notes = result.blocking_reason
    elif disposition is InvoiceStatus.NEEDS_REVIEW:
        invoice.status = InvoiceStatus.NEEDS_REVIEW
        invoice.notes = result.blocking_reason
    else:
        invoice.status = InvoiceStatus.EXTRACTED
        if auto_post:
            await session.flush()
            await post_invoice(session, accounting, invoice)

    # Committing here, still under the lock, is the point of the lock. Each
    # upload is processed on its own session and therefore its own connection,
    # so a flush would leave the row invisible to the next document's duplicate
    # lookup. Releasing the lock only after the commit is what makes that lookup
    # see everything already registered.
    await session.commit()
    return invoice


async def summarize(session: AsyncSession) -> dict:
    rows = await session.execute(
        select(Invoice.status, func.count(Invoice.id)).group_by(Invoice.status)
    )
    return {status.value: count for status, count in rows.all()}


async def load_invoice(
    session: AsyncSession, invoice_id: int, *, fresh: bool = False
) -> Invoice | None:
    """Load an invoice with everything the review screen needs.

    Pass fresh=True after writing through this session. The session keeps the
    object it already has, relationships and all, so an invoice re-read straight
    after posting comes back with the collection it was loaded with -- reporting
    no accounting reference for something that just registered.
    """
    stmt = (
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(
            selectinload(Invoice.lines),
            selectinload(Invoice.checks),
            selectinload(Invoice.postings),
            selectinload(Invoice.document),
            selectinload(Invoice.review_events),
        )
    )
    if fresh:
        stmt = stmt.execution_options(populate_existing=True)
    return await session.scalar(stmt)
