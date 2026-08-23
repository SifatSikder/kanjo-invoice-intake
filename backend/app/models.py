"""Persistence model.

Three of these tables exist purely so that a posting can be explained after the
fact: `check_results` records every verification decision, `postings` records the
exact bytes we sent and got back, and `review_events` records which human changed
what. Together they answer "how would you find out if something was registered
incorrectly?" without anyone having to re-run the pipeline.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# JSONB on Postgres, plain JSON on SQLite so the test suite can run without a server.
JSONVariant = JSON().with_variant(JSONB(), "postgresql")


class InvoiceStatus(str, enum.Enum):
    PENDING = "PENDING"           # document ingested, nothing read yet
    EXTRACTED = "EXTRACTED"       # model has read it, checks not yet run
    NEEDS_REVIEW = "NEEDS_REVIEW"  # a check failed; a human must look
    BLOCKED = "BLOCKED"           # cannot be posted at all without a business decision
    POSTED = "POSTED"             # registered in the accounting system
    REJECTED = "REJECTED"         # a human declined it
    POST_FAILED = "POST_FAILED"   # the accounting API refused it
    EXTRACT_FAILED = "EXTRACT_FAILED"


class Severity(str, enum.Enum):
    BLOCKER = "BLOCKER"  # never auto-post, never post without a human decision
    ERROR = "ERROR"      # route to the review queue
    WARN = "WARN"        # post, but surface the flag
    INFO = "INFO"        # recorded for the audit trail only


class MatchMethod(str, enum.Enum):
    REGISTRATION_NO = "REGISTRATION_NO"
    EXACT_NAME = "EXACT_NAME"
    ALIAS = "ALIAS"
    FUZZY_NAME = "FUZZY_NAME"
    UNRESOLVED = "UNRESOLVED"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # Content hash makes re-uploading the same file a no-op, so a document that
    # is sent twice is never read or registered twice.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    has_text_layer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    invoices: Mapped[list[Invoice]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Extraction(Base, TimestampMixin):
    """One model call. Kept even when superseded, so model changes are auditable."""

    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_response: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    normalized: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 8), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped[Document] = relationship(back_populates="extractions")


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    extraction_id: Mapped[int | None] = mapped_column(
        ForeignKey("extractions.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        SAEnum(InvoiceStatus, native_enum=False, length=32),
        nullable=False,
        default=InvoiceStatus.PENDING,
        index=True,
    )

    # --- supplier resolution -------------------------------------------------
    partner_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    partner_name_raw: Mapped[str | None] = mapped_column(String(512), nullable=True)
    partner_registration_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    partner_match_method: Mapped[MatchMethod] = mapped_column(
        SAEnum(MatchMethod, native_enum=False, length=32),
        nullable=False,
        default=MatchMethod.UNRESOLVED,
    )
    partner_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # --- the payload the accounting system will receive ----------------------
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="JPY")
    subtotal: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tax_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- as-read values, kept verbatim for the review screen -----------------
    issue_date_raw: Mapped[str | None] = mapped_column(String(128), nullable=True)
    due_date_raw: Mapped[str | None] = mapped_column(String(128), nullable=True)
    min_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    has_handwriting: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="invoices")
    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.seq",
    )
    checks: Mapped[list[CheckResult]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    postings: Mapped[list[Posting]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )
    review_events: Mapped[list[ReviewEvent]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (UniqueConstraint("invoice_id", "seq", name="uq_invoice_line_seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False)
    quantity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    unit: Mapped[str] = mapped_column(String(64), nullable=False, default="式")
    unit_price: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_code: Mapped[str] = mapped_column(String(16), nullable=False, default="T10")
    tax_rate_raw: Mapped[str | None] = mapped_column(String(32), nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class CheckResult(Base, TimestampMixin):
    """One verification decision. The audit trail for why something posted or did not."""

    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[Severity] = mapped_column(
        SAEnum(Severity, native_enum=False, length=16), nullable=False
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detail: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="checks")


class Posting(Base, TimestampMixin):
    """Exactly what we sent to the accounting system and exactly what came back."""

    __tablename__ = "postings"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    accounting_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    invoice: Mapped[Invoice] = relationship(back_populates="postings")


class ReviewEvent(Base, TimestampMixin):
    """Who changed what, and what it looked like before."""

    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="reviewer")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="review_events")
