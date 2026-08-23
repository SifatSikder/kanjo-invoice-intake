"""Upload — the only way an invoice enters the system.

The client's staff handle invoices "one by one, as they arrive from suppliers".
That is the shape of the product: someone has a document in front of them and
wants it dealt with. A backlog is the same action with more files selected, so
there is no separate bulk mode and no folder on the server -- an entry point
nobody outside the machine can reach would be demo scaffolding, not a feature.

The request returns as soon as the file is safely stored and recorded, before
anything has been read. The uploader immediately sees the invoice in the queue
marked as being read, and the row updates itself as extraction, verification and
registration complete. Blocking the response for the ten seconds an extraction
takes would leave them staring at a spinner with no evidence the upload landed.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import accounting_client, openrouter_client
from app.config import settings
from app.db import SessionLocal
from app.models import InvoiceStatus
from app.pipeline.orchestrator import (
    Accepted,
    accept_document,
    get_partner_master,
    load_invoice,
    process_accepted,
)
from app.pipeline.render import (
    SUPPORTED_SUFFIXES_HINT,
    IncompleteDocument,
    RenderedDocument,
    assert_complete,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["upload"])

MAX_BYTES = 25 * 1024 * 1024
ALLOWED = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

# asyncio holds only a weak reference to a bare task, so one that is not kept
# alive here can be garbage collected mid-extraction and silently disappear.
_running: set[asyncio.Task] = set()

# Someone selecting a month of invoices at once would otherwise fire that many
# simultaneous model calls and collect rate-limit errors. Uploads are accepted
# immediately either way; this only paces the reading behind them.
_extraction_slots = asyncio.Semaphore(settings.max_concurrent_extractions)


async def _process_in_background(invoice_id: int, rendered: RenderedDocument) -> None:
    """Read, verify and register one uploaded document.

    Runs on its own session: the request that accepted the upload has long since
    returned and closed its own. The rendered pages are reused from acceptance
    rather than produced again.
    """
    try:
        async with _extraction_slots, SessionLocal() as session:
            invoice = await load_invoice(session, invoice_id)
            if invoice is None:
                return
            accounting = accounting_client()
            master = await get_partner_master(accounting)
            await process_accepted(
                session,
                Accepted(invoice=invoice, rendered=rendered),
                openrouter=openrouter_client(),
                accounting=accounting,
                master=master,
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - a failed upload must not take the server down
        logger.exception("background processing failed for invoice %s", invoice_id)
        try:
            async with SessionLocal() as session:
                invoice = await load_invoice(session, invoice_id)
                if invoice and invoice.status is InvoiceStatus.PENDING:
                    invoice.status = InvoiceStatus.EXTRACT_FAILED
                    invoice.notes = "Could not be read; see the server log"
                    await session.commit()
        except Exception:
            logger.exception("could not mark invoice %s as failed", invoice_id)


@router.post("/documents")
async def upload_documents(files: list[UploadFile] = File(...)) -> dict:
    """Accept one or more invoice files and start processing each of them."""
    if not files:
        raise HTTPException(400, "no files were uploaded")

    accepted: list[dict] = []
    scheduled: list[tuple[int, RenderedDocument]] = []

    async with SessionLocal() as session:
        for upload in files:
            name = Path(upload.filename or "upload").name
            suffix = Path(name).suffix.lower()
            if suffix not in ALLOWED:
                accepted.append({
                    "filename": name, "accepted": False,
                    "reason": f"{suffix or 'that file type'} is not supported. "
                              f"Upload {SUPPORTED_SUFFIXES_HINT}.",
                })
                continue

            # Stream to a temp file so a large scan never sits in memory, and so
            # the size limit is enforced on bytes actually received.
            tmp = Path(tempfile.mkdtemp(prefix="upload-")) / name
            size = 0
            with tmp.open("wb") as out:
                while chunk := await upload.read(1 << 20):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        out.close()
                        shutil.rmtree(tmp.parent, ignore_errors=True)
                        break
                    out.write(chunk)
            if size > MAX_BYTES:
                accepted.append({
                    "filename": name, "accepted": False,
                    "reason": f"file is larger than {MAX_BYTES // (1024*1024)} MB",
                })
                continue

            try:
                # An upload is the one path where the bytes can arrive partial.
                assert_complete(tmp)
                result = await accept_document(session, tmp, config=settings)
            except IncompleteDocument as exc:
                logger.warning("rejected incomplete upload %s: %s", name, exc)
                shutil.rmtree(tmp.parent, ignore_errors=True)
                accepted.append({
                    "filename": name, "accepted": False,
                    "reason": f"{exc}. Please upload it again.",
                })
                continue
            except Exception as exc:  # noqa: BLE001 - a corrupt file is a user error
                logger.exception("could not read %s", name)
                shutil.rmtree(tmp.parent, ignore_errors=True)
                accepted.append({
                    "filename": name, "accepted": False,
                    "reason": f"could not be opened as a document ({type(exc).__name__})",
                })
                continue

            if result.already_ingested:
                # Not an error. The same file arriving twice is exactly what the
                # client complained about, and saying so plainly is the point.
                shutil.rmtree(tmp.parent, ignore_errors=True)
                accepted.append({
                    "filename": name, "accepted": False, "duplicate": True,
                    "invoice_id": result.invoice.id,
                    "reason": "this exact file has already been uploaded",
                })
                continue

            await session.commit()
            accepted.append({
                "filename": name, "accepted": True,
                "invoice_id": result.invoice.id,
                "pages": result.rendered.page_count,
            })
            # The page images are already in storage; the upload itself is no
            # longer needed.
            shutil.rmtree(tmp.parent, ignore_errors=True)
            scheduled.append((result.invoice.id, result.rendered))

    # Started after the session closes so the rows are visible to the poller
    # immediately, and so a slow extraction never holds the upload open.
    for invoice_id, rendered in scheduled:
        task = asyncio.create_task(_process_in_background(invoice_id, rendered))
        _running.add(task)
        task.add_done_callback(_running.discard)

    return {
        "accepted": [a for a in accepted if a.get("accepted")],
        "rejected": [a for a in accepted if not a.get("accepted")],
        "processing": len(scheduled),
    }
