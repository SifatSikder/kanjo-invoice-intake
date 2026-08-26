"""The verification gate: everything between "the model read it" and "we paid it".

Each check returns a severity, and the severities decide the invoice's fate:

  BLOCKER -> BLOCKED       cannot be posted at all without a business decision
  ERROR   -> NEEDS_REVIEW  a human looks at it before it goes anywhere
  WARN    -> posted, but the flag is recorded and shown
  (none)  -> posted automatically

The primary check is `arithmetic.line_sum` and its two siblings, and the reason
is worth stating plainly: **every invoice carries its own checksum.** The page
prints the line items and, separately, prints the totals. We are not asking the
model to grade its own work -- we are exploiting redundancy that already exists
on the paper. Since OCR and vision-model errors are digit-level, a misread digit
in any line amount breaks the sum. That makes it a near-perfect detector for the
one error class that actually costs money, it costs nothing to run, and it is
the same rule the accounting API applies on receipt -- so passing it locally
means the POST will succeed.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from app.models import InvoiceStatus, MatchMethod, Severity
from app.pipeline.normalize import TAX_RATES, expected_tax_by_code
from app.pipeline.partners import PartnerMatch
from app.schemas import NormalizedInvoice


@dataclass
class Check:
    name: str
    severity: Severity
    passed: bool
    message: str
    detail: dict | None = None


@dataclass
class DuplicateFindings:
    """Filled in by app.pipeline.dedupe before the checks run."""

    exact: dict | None = None
    near: list[dict] = field(default_factory=list)


@dataclass
class VerificationResult:
    checks: list[Check]

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.failed if c.severity is Severity.BLOCKER]

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.failed if c.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.failed if c.severity is Severity.WARN]

    @property
    def disposition(self) -> InvoiceStatus:
        if self.blockers:
            return InvoiceStatus.BLOCKED
        if self.errors:
            return InvoiceStatus.NEEDS_REVIEW
        return InvoiceStatus.EXTRACTED  # clean -> eligible for auto-post

    @property
    def blocking_reason(self) -> str | None:
        problems = self.blockers or self.errors
        return problems[0].message if problems else None


def _fold(value: str) -> str:
    """Compare strings the way a human would read them off the page."""
    text = unicodedata.normalize("NFKC", value or "")
    return "".join(ch for ch in text if not ch.isspace() and ch not in ",，、")


def run_checks(
    invoice: NormalizedInvoice,
    match: PartnerMatch,
    *,
    duplicates: DuplicateFindings | None = None,
    text_layer: str | None = None,
    confidence_floor: float = 0.80,
    amount_review_threshold: int = 1_000_000,
) -> VerificationResult:
    checks: list[Check] = []
    add = checks.append
    duplicates = duplicates or DuplicateFindings()
    lines = invoice.lines

    # ---------------------------------------------------------------- shape --
    add(
        Check(
            "extraction.lines_present",
            Severity.BLOCKER,
            bool(lines),
            "No line items were read from the document"
            if not lines
            else f"{len(lines)} line item(s) read",
            {"line_count": len(lines)},
        )
    )
    add(
        Check(
            "fields.invoice_number",
            Severity.ERROR,
            bool((invoice.invoice_number or "").strip()),
            "Invoice number is missing"
            if not (invoice.invoice_number or "").strip()
            else f"Invoice number {invoice.invoice_number}",
        )
    )

    # ----------------------------------------------------------- arithmetic --
    # This is the check that matters. See the module docstring.
    if lines and invoice.subtotal is not None:
        expected_subtotal = sum(line.amount for line in lines)
        ok = expected_subtotal == invoice.subtotal
        add(
            Check(
                "arithmetic.line_sum",
                Severity.ERROR,
                ok,
                f"Line items sum to {expected_subtotal:,} but the invoice states "
                f"a subtotal of {invoice.subtotal:,}"
                if not ok
                else f"Line items sum to the printed subtotal ({invoice.subtotal:,})",
                {
                    "expected_subtotal": expected_subtotal,
                    "printed_subtotal": invoice.subtotal,
                    "difference": expected_subtotal - invoice.subtotal,
                },
            )
        )
    else:
        add(
            Check(
                "arithmetic.line_sum",
                Severity.ERROR,
                False,
                "Cannot reconcile: subtotal or line items missing",
            )
        )

    if lines and invoice.tax_amount is not None:
        by_code = expected_tax_by_code(lines)
        expected_tax = sum(by_code.values())
        ok = expected_tax == invoice.tax_amount
        add(
            Check(
                "arithmetic.tax_per_code",
                Severity.ERROR,
                ok,
                f"Tax recalculated per tax code is {expected_tax:,} but the "
                f"invoice states {invoice.tax_amount:,}"
                if not ok
                else f"Tax matches per-code recalculation ({expected_tax:,})",
                {
                    "expected_tax": expected_tax,
                    "printed_tax": invoice.tax_amount,
                    "expected_tax_by_code": by_code,
                },
            )
        )
    else:
        add(Check("arithmetic.tax_per_code", Severity.ERROR, False, "Tax amount missing"))

    if lines and invoice.subtotal is not None and invoice.tax_amount is not None and invoice.total_amount is not None:
        expected_total = sum(line.amount for line in lines) + sum(expected_tax_by_code(lines).values())
        ok = expected_total == invoice.total_amount
        add(
            Check(
                "arithmetic.total",
                Severity.ERROR,
                ok,
                f"Recalculated total is {expected_total:,} but the invoice states "
                f"{invoice.total_amount:,}"
                if not ok
                else f"Total matches recalculation ({expected_total:,})",
                {"expected_total": expected_total, "printed_total": invoice.total_amount},
            )
        )
    else:
        add(Check("arithmetic.total", Severity.ERROR, False, "Total amount missing"))

    # Independent redundancy: quantity x unit price should equal the line amount
    # wherever both are printed. A mismatch here does not change what we post
    # (only `amount` is sent), so it is a WARN -- but it is a genuine signal that
    # a digit was misread somewhere on that row.
    mismatched = [
        {
            "seq": line.seq,
            "description": line.description,
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "amount": line.amount,
            "expected": line.quantity * line.unit_price,
        }
        for line in lines
        if line.quantity is not None
        and line.unit_price is not None
        and line.quantity * line.unit_price != line.amount
    ]
    add(
        Check(
            "arithmetic.line_product",
            Severity.WARN,
            not mismatched,
            f"{len(mismatched)} line(s) where quantity x unit price does not equal the amount"
            if mismatched
            else "Quantity x unit price agrees with every printed line amount",
            {"mismatched": mismatched} if mismatched else None,
        )
    )

    # -------------------------------------------------------------- supplier --
    add(
        Check(
            "partner.resolved",
            Severity.BLOCKER,
            match.resolved,
            f"Supplier '{invoice.supplier_name}' is not in the partner master, so it "
            "cannot be registered"
            if not match.resolved
            else f"Supplier resolved to {match.partner_code} via {match.method.value}",
            match.detail,
        )
    )
    add(
        Check(
            "partner.agreement",
            Severity.ERROR,
            match.agreement,
            match.detail.get("conflict", "Registration number and printed name disagree")
            if not match.agreement
            else "Registration number and printed name identify the same supplier",
            match.detail,
        )
    )
    add(
        Check(
            "partner.match_quality",
            Severity.ERROR,
            match.method is not MatchMethod.FUZZY_NAME,
            "Supplier matched only by fuzzy name similarity; confirm before posting"
            if match.method is MatchMethod.FUZZY_NAME
            else f"Supplier match method: {match.method.value}",
            {"confidence": match.confidence},
        )
    )

    # ------------------------------------------------------------- duplicates --
    # Checked against our own records, not by letting the accounting API 409 at
    # us, so the reviewer is told *which* invoice this duplicates.
    add(
        Check(
            "dedupe.exact",
            Severity.BLOCKER,
            duplicates.exact is None,
            (
                "Already registered: invoice {number} for this supplier was posted as {acc} "
                "from {file}".format(
                    number=duplicates.exact.get("invoice_number"),
                    acc=duplicates.exact.get("accounting_id") or "a previous run",
                    file=duplicates.exact.get("filename"),
                )
                if duplicates.exact
                else "No existing registration for this supplier and invoice number"
            ),
            duplicates.exact,
        )
    )
    add(
        Check(
            "dedupe.near",
            Severity.ERROR,
            not duplicates.near,
            f"{len(duplicates.near)} similar invoice(s) from the same supplier with the "
            "same total around the same date"
            if duplicates.near
            else "No near-duplicate invoices found",
            {"candidates": duplicates.near} if duplicates.near else None,
        )
    )

    # ------------------------------------------------------------------ dates --
    dates_ok = invoice.issue_date is not None and invoice.due_date is not None
    add(
        Check(
            "dates.parsed",
            Severity.ERROR,
            dates_ok,
            "Could not parse issue date and/or due date into YYYY-MM-DD"
            if not dates_ok
            else f"Dates parsed: {invoice.issue_date} -> {invoice.due_date}",
            {
                "issue_date_raw": invoice.issue_date_raw,
                "due_date_raw": invoice.due_date_raw,
                "issue_date": str(invoice.issue_date) if invoice.issue_date else None,
                "due_date": str(invoice.due_date) if invoice.due_date else None,
            },
        )
    )
    if dates_ok:
        ok = invoice.due_date >= invoice.issue_date
        add(
            Check(
                "dates.order",
                Severity.ERROR,
                ok,
                f"Due date {invoice.due_date} precedes issue date {invoice.issue_date}"
                if not ok
                else "Due date is on or after the issue date",
            )
        )

    # -------------------------------------------------------------- tax codes --
    unknown = sorted({line.tax_code for line in lines if line.tax_code not in TAX_RATES})
    add(
        Check(
            "tax_code.known",
            Severity.ERROR,
            not unknown and not invoice.unmapped_tax_rates,
            f"Unrecognised tax rate(s): {', '.join(unknown + invoice.unmapped_tax_rates)}"
            if (unknown or invoice.unmapped_tax_rates)
            else "Every line maps to a known tax code",
            {"unknown": unknown, "unmapped_rates": invoice.unmapped_tax_rates} or None,
        )
    )
    missing_units = [line.seq for line in lines if not (line.unit or "").strip()]
    add(
        Check(
            "fields.units_present",
            # A missing unit label does not change what is paid, so it does not
            # block. It is surfaced because the accounting system requires one,
            # and we will substitute a lump-sum marker rather than guess 個/箱/本.
            Severity.WARN,
            not missing_units,
            f"The unit column was not read on line(s) {', '.join(map(str, missing_units))}; "
            f"they will be registered as '式' (lump sum)"
            if missing_units
            else "Every line has a unit",
            {"lines": missing_units} if missing_units else None,
        )
    )

    # ------------------------------------------------------------- grounding --
    # Cheap hallucination detector: for documents that have a real text layer,
    # the key figures the model reported must literally appear in that text.
    if text_layer and text_layer.strip():
        folded = _fold(text_layer)
        probes = {
            "invoice_number": invoice.invoice_number,
            "subtotal": f"{invoice.subtotal:,}" if invoice.subtotal is not None else None,
            "total_amount": f"{invoice.total_amount:,}" if invoice.total_amount is not None else None,
        }
        missing = [
            key
            for key, value in probes.items()
            if value and _fold(str(value)) not in folded
        ]
        add(
            Check(
                "grounding.values_present",
                Severity.WARN,
                not missing,
                f"Values not found verbatim in the document text layer: {', '.join(missing)}"
                if missing
                else "Key values verified against the document text layer",
                {"missing": missing} if missing else None,
            )
        )

    # ------------------------------------------------------------ confidence --
    add(
        Check(
            "confidence.floor",
            Severity.ERROR,
            invoice.min_confidence >= confidence_floor,
            f"Model reported low confidence ({invoice.min_confidence:.2f}) on at least "
            f"one field; floor is {confidence_floor:.2f}"
            if invoice.min_confidence < confidence_floor
            else f"All fields at or above the confidence floor ({invoice.min_confidence:.2f})",
            {"min_confidence": invoice.min_confidence, "floor": confidence_floor},
        )
    )

    # ----------------------------------------------------------- handwriting --
    # Two checks, deliberately different severities. A 受領 ("received") stamp is
    # workflow noise and must not cost a human any time. Pen that alters bank
    # transfer details is a textbook invoice-fraud vector and must always reach
    # one -- even though bank details are not part of the accounting payload,
    # so nothing downstream would ever catch it.
    # When the pen has touched payment details the check below says so in full,
    # and this one would repeat it as a second, weaker finding about the same
    # mark. Only raise it when it is the only thing worth mentioning.
    add(
        Check(
            "handwriting.detected",
            Severity.WARN,
            not invoice.has_handwriting or invoice.handwriting_affects_payment,
            f"Handwritten annotation present: {invoice.handwriting_notes}"
            if invoice.has_handwriting
            else "No handwritten annotations detected",
            {"notes": invoice.handwriting_notes} if invoice.has_handwriting else None,
        )
    )
    add(
        Check(
            "handwriting.on_payment_details",
            Severity.ERROR,
            not invoice.handwriting_affects_payment,
            "Handwriting alters payment or bank details \u2014 confirm with the "
            "supplier before paying"
            if invoice.handwriting_affects_payment
            else "No handwriting on payment details",
            {"notes": invoice.handwriting_notes}
            if invoice.handwriting_affects_payment
            else None,
        )
    )

    # ---------------------------------------------------------------- policy --
    # Not a correctness check -- a control. Any real finance function wants a
    # human on large amounts regardless of how confident the machine is.
    if amount_review_threshold and invoice.total_amount is not None:
        ok = invoice.total_amount <= amount_review_threshold
        add(
            Check(
                "amount.threshold",
                Severity.ERROR,
                ok,
                f"Total {invoice.total_amount:,} JPY exceeds the "
                f"{amount_review_threshold:,} JPY auto-approval limit"
                if not ok
                else f"Total is within the auto-approval limit",
                {"total": invoice.total_amount, "threshold": amount_review_threshold},
            )
        )

    return VerificationResult(checks=checks)
