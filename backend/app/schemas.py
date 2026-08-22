"""Pydantic shapes.

Three layers, deliberately kept apart:

  RawExtraction     -- what the model claims it saw, strings exactly as printed
  NormalizedInvoice -- what our deterministic code derived from that
  AccountingPayload -- what the accounting system will actually receive

Keeping them separate is what makes the review screen possible: a human can see
the transcription and the interpretation side by side and tell which one is wrong.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Layer 1: what the model returns. Every value is a string, verbatim from the
# page. The model is explicitly forbidden from computing or converting anything.
# --------------------------------------------------------------------------


class RawLine(BaseModel):
    description: str = ""
    quantity_raw: str = ""
    unit: str = ""
    unit_price_raw: str = ""
    amount_raw: str = ""
    tax_rate_raw: str = ""


class RawTaxBreakdown(BaseModel):
    """The 消費税 rows printed near the totals, e.g. '消費税8%（対象 103,200） 8,256'."""

    rate_raw: str = ""
    base_raw: str = ""
    tax_raw: str = ""


class RawHandwriting(BaseModel):
    text: str = ""
    location: str = ""
    # Set when the annotation touches bank/transfer details. A pen change to a
    # payee account is a classic invoice-fraud vector and must reach a human even
    # though bank details are not part of the accounting payload at all.
    affects_payment_details: bool = False


class FieldConfidence(BaseModel):
    supplier_name: float = 1.0
    supplier_registration_no: float = 1.0
    invoice_number: float = 1.0
    issue_date: float = 1.0
    due_date: float = 1.0
    subtotal: float = 1.0
    tax_amount: float = 1.0
    total_amount: float = 1.0
    lines: float = 1.0


class RawExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    supplier_name: str = ""
    supplier_registration_no: str = ""
    invoice_number: str = ""
    issue_date_raw: str = ""
    due_date_raw: str = ""
    subtotal_raw: str = ""
    tax_amount_raw: str = ""
    total_amount_raw: str = ""
    currency: str = "JPY"
    lines: list[RawLine] = Field(default_factory=list)
    tax_breakdown: list[RawTaxBreakdown] = Field(default_factory=list)
    handwritten_annotations: list[RawHandwriting] = Field(default_factory=list)
    field_confidence: FieldConfidence = Field(default_factory=FieldConfidence)
    notes: str = ""


# --------------------------------------------------------------------------
# Layer 2: our interpretation.
# --------------------------------------------------------------------------


class NormalizedLine(BaseModel):
    seq: int
    description: str
    quantity: int | None = None
    unit: str = "式"
    unit_price: int | None = None
    amount: int
    tax_code: str = "T10"
    tax_rate_raw: str | None = None


class NormalizedInvoice(BaseModel):
    supplier_name: str = ""
    supplier_registration_no: str = ""
    invoice_number: str = ""
    issue_date: date | None = None
    due_date: date | None = None
    issue_date_raw: str = ""
    due_date_raw: str = ""
    currency: str = "JPY"
    subtotal: int | None = None
    tax_amount: int | None = None
    total_amount: int | None = None
    lines: list[NormalizedLine] = Field(default_factory=list)
    min_confidence: float = 1.0
    has_handwriting: bool = False
    handwriting_affects_payment: bool = False
    handwriting_notes: str = ""
    unmapped_tax_rates: list[str] = Field(default_factory=list)
    notes: str = ""


# --------------------------------------------------------------------------
# Layer 3: the accounting system's contract. Field names and types here are
# dictated by the API and must not drift.
# --------------------------------------------------------------------------


class AccountingLine(BaseModel):
    description: str
    quantity: int | None
    unit: str
    unit_price: int | None
    amount: int
    tax_code: str


class AccountingPayload(BaseModel):
    partner_code: str
    invoice_number: str
    issue_date: str  # YYYY-MM-DD, enforced by the API
    due_date: str
    currency: Literal["JPY"] = "JPY"
    lines: list[AccountingLine]
    subtotal: int
    tax_amount: int
    total_amount: int


# --------------------------------------------------------------------------
# Web API shapes
# --------------------------------------------------------------------------


class CheckOut(BaseModel):
    name: str
    severity: str
    passed: bool
    message: str
    detail: dict | None = None


class LineOut(BaseModel):
    id: int | None = None
    seq: int
    description: str
    quantity: int | None
    unit: str
    unit_price: int | None
    amount: int
    tax_code: str


class InvoiceOut(BaseModel):
    id: int
    status: str
    filename: str
    document_id: int
    page_count: int
    partner_code: str | None
    partner_name_raw: str | None
    partner_registration_no: str | None
    partner_match_method: str
    partner_confidence: float
    invoice_number: str | None
    issue_date: date | None
    due_date: date | None
    issue_date_raw: str | None
    due_date_raw: str | None
    subtotal: int | None
    tax_amount: int | None
    total_amount: int | None
    min_confidence: float
    has_handwriting: bool
    notes: str | None
    accounting_id: str | None = None
    lines: list[LineOut] = Field(default_factory=list)
    checks: list[CheckOut] = Field(default_factory=list)
    created_at: datetime | None = None


class InvoiceSummary(BaseModel):
    id: int
    status: str
    filename: str
    partner_code: str | None
    partner_name_raw: str | None
    invoice_number: str | None
    issue_date: date | None
    total_amount: int | None
    accounting_id: str | None = None
    blocking_reason: str | None = None
    failed_checks: int = 0


class LinePatch(BaseModel):
    seq: int
    description: str
    quantity: int | None = None
    unit: str = "式"
    unit_price: int | None = None
    amount: int
    tax_code: str = "T10"


class InvoicePatch(BaseModel):
    """What a reviewer may change. Deliberately narrow."""

    partner_code: str | None = None
    invoice_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    subtotal: int | None = None
    tax_amount: int | None = None
    total_amount: int | None = None
    lines: list[LinePatch] | None = None
    note: str | None = None
    actor: str = "reviewer"


class DashboardStats(BaseModel):
    total_documents: int
    by_status: dict[str, int]
    auto_posted: int
    posted_after_review: int
    needs_review: int
    blocked: int
    total_cost_usd: float
    avg_latency_ms: int
    auto_pass_rate: float
    registered_in_accounting: int
