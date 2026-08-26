"""End-to-end verification against hand-built ground truth for all 12 invoices.

This is the test that matters. It feeds each sample invoice's *correct*
transcription through the real partner resolution, duplicate detection and check
ladder, and asserts the pipeline routes it to the right place.

Because it uses ground truth rather than model output, a failure here means the
business logic is wrong -- not that the model had a bad day. Model accuracy is a
separate concern, measured by evals/run_eval.py.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models import InvoiceStatus
from app.pipeline.dedupe import find_duplicates_in
from app.pipeline.verify import run_checks
from app.schemas import NormalizedInvoice, NormalizedLine

AMOUNT_THRESHOLD = 1_000_000


def build(name: str, gt: dict) -> NormalizedInvoice:
    """Turn a ground-truth entry into what the pipeline would produce from a perfect read."""
    return NormalizedInvoice(
        supplier_name=gt["supplier_name"],
        supplier_registration_no=gt["registration_no"] or "",
        invoice_number=gt["invoice_number"],
        issue_date=date.fromisoformat(gt["issue_date"]),
        due_date=date.fromisoformat(gt["due_date"]),
        issue_date_raw=gt["issue_date"],
        due_date_raw=gt["due_date"],
        subtotal=gt["subtotal"],
        tax_amount=gt["tax_amount"],
        total_amount=gt["total_amount"],
        lines=[
            NormalizedLine(
                seq=i,
                description=l["description"],
                quantity=l["quantity"],
                unit=l["unit"],
                unit_price=l["unit_price"],
                amount=l["amount"],
                tax_code=l["tax_code"],
            )
            for i, l in enumerate(gt["lines"], start=1)
        ],
        has_handwriting=gt.get("expect_handwriting", False),
        handwriting_affects_payment=gt.get("expect_handwriting_on_payment", False),
        handwriting_notes=gt.get("disposition_reason", "") if gt.get("expect_handwriting") else "",
    )


def run_pipeline(ground_truth, master):
    """Process every invoice in filename order, accumulating state as a real run would."""
    seen: list[dict] = []
    results: dict[str, tuple] = {}

    for name, gt in ground_truth.items():
        invoice = build(name, gt)
        match = master.resolve(invoice.supplier_name, invoice.supplier_registration_no)
        dupes = find_duplicates_in(
            partner_code=match.partner_code,
            invoice_number=invoice.invoice_number,
            total_amount=invoice.total_amount,
            issue_date=invoice.issue_date,
            existing=seen,
        )
        result = run_checks(
            invoice, match, duplicates=dupes,
            amount_review_threshold=AMOUNT_THRESHOLD,
        )
        results[name] = (result, match, invoice)

        # Only invoices that got as far as being real records occupy a number.
        if match.resolved and result.disposition is not InvoiceStatus.BLOCKED:
            seen.append({
                "invoice_id": len(seen) + 1,
                "partner_code": match.partner_code,
                "invoice_number": invoice.invoice_number,
                "total_amount": invoice.total_amount,
                "issue_date": invoice.issue_date,
                "filename": name,
                "accounting_id": f"ACC-{len(seen)+1:04d}",
            })
    return results


@pytest.fixture(scope="module")
def pipeline_results(ground_truth, master):
    return run_pipeline(ground_truth, master)


def expected_status(gt: dict) -> InvoiceStatus:
    return {
        "AUTO": InvoiceStatus.EXTRACTED,
        "NEEDS_REVIEW": InvoiceStatus.NEEDS_REVIEW,
        "BLOCKED": InvoiceStatus.BLOCKED,
    }[gt["expected_disposition"]]


def test_every_invoice_routes_correctly(ground_truth, pipeline_results):
    wrong = []
    for name, gt in ground_truth.items():
        result, _, _ = pipeline_results[name]
        if result.disposition is not expected_status(gt):
            wrong.append(
                f"{name}: expected {gt['expected_disposition']}, got "
                f"{result.disposition.value} ({result.blocking_reason})"
            )
    assert not wrong, "\n".join(wrong)


def test_disposition_counts(pipeline_results):
    """7 post unattended, 3 need a human, 2 cannot be posted at all."""
    counts: dict[str, int] = {}
    for result, _, _ in pipeline_results.values():
        counts[result.disposition.value] = counts.get(result.disposition.value, 0) + 1
    assert counts == {"EXTRACTED": 7, "NEEDS_REVIEW": 3, "BLOCKED": 2}


def test_duplicate_invoice_is_blocked_and_names_its_twin(pipeline_results):
    """The scenario from the client's email: the same invoice arriving twice."""
    result, _, _ = pipeline_results["invoice_07.jpg"]
    check = next(c for c in result.checks if c.name == "dedupe.exact")
    assert not check.passed
    assert check.detail["filename"] == "invoice_01.pdf"
    assert check.detail["accounting_id"] == "ACC-0001"
    assert "YM-2026-0107" in check.message


def test_unknown_supplier_is_blocked_not_guessed(pipeline_results):
    result, match, _ = pipeline_results["invoice_10.jpg"]
    assert not match.resolved
    assert result.disposition is InvoiceStatus.BLOCKED
    assert not next(c for c in result.checks if c.name == "partner.resolved").passed


def test_supplier_alias_resolves_to_the_master(pipeline_results):
    """invoice_06 prints ヤマダ製作所; the master's legal name is 株式会社山田製作所."""
    _, match, _ = pipeline_results["invoice_06.jpg"]
    assert match.partner_code == "P-1001"
    assert match.confidence == 1.0  # corroborated by the registration number


def test_document_defect_is_caught(pipeline_results):
    """invoice_09's own printed total is 1 JPY above its line items + floored tax.

    A human keying this in would have propagated the supplier's error. The
    arithmetic check catches it, and it is the only reason this invoice stops.
    """
    result, _, _ = pipeline_results["invoice_09.pdf"]
    assert result.disposition is InvoiceStatus.NEEDS_REVIEW
    total_check = next(c for c in result.checks if c.name == "arithmetic.total")
    assert not total_check.passed
    assert total_check.detail["expected_total"] == 147_496
    assert total_check.detail["printed_total"] == 147_497
    # Nothing else is wrong with it.
    assert {c.name for c in result.errors} == {"arithmetic.total"}


def test_handwriting_severity_depends_on_what_it_touches(pipeline_results):
    """Both 04 and 08 carry pen. Only one of them is worth a human's time."""
    stamp, _, _ = pipeline_results["invoice_04.jpg"]      # 受領 received stamp
    bank, _, _ = pipeline_results["invoice_08.jpg"]        # red pen on the bank account

    assert stamp.disposition is InvoiceStatus.EXTRACTED    # auto-posts
    assert not next(c for c in stamp.checks if c.name == "handwriting.detected").passed
    assert next(c for c in stamp.checks if c.name == "handwriting.on_payment_details").passed

    assert bank.disposition is InvoiceStatus.NEEDS_REVIEW
    assert not next(c for c in bank.checks if c.name == "handwriting.on_payment_details").passed


def test_large_amount_goes_to_a_human_even_when_perfectly_read(pipeline_results):
    """invoice_02 is flawless. It stops because of policy, not because of doubt."""
    result, _, _ = pipeline_results["invoice_02.pdf"]
    assert result.disposition is InvoiceStatus.NEEDS_REVIEW
    assert {c.name for c in result.errors} == {"amount.threshold"}


def test_mixed_tax_rates_reconcile(pipeline_results):
    for name in ("invoice_03.pdf", "invoice_08.jpg"):
        result, _, invoice = pipeline_results[name]
        codes = {l.tax_code for l in invoice.lines}
        assert codes == {"T08", "T10"}, name
        assert next(c for c in result.checks if c.name == "arithmetic.tax_per_code").passed, name


def test_negative_discount_line_reconciles(pipeline_results):
    """invoice_12's 値引き line is printed as △30,000 and must be read as -30,000."""
    result, _, invoice = pipeline_results["invoice_12.jpg"]
    assert any(l.amount == -30_000 for l in invoice.lines)
    assert next(c for c in result.checks if c.name == "arithmetic.line_sum").passed
    assert result.disposition is InvoiceStatus.EXTRACTED


def test_clean_invoices_have_no_errors_at_all(ground_truth, pipeline_results):
    for name, gt in ground_truth.items():
        if gt["expected_disposition"] != "AUTO":
            continue
        result, _, _ = pipeline_results[name]
        assert not result.errors and not result.blockers, (
            f"{name} should be clean but failed: {[c.name for c in result.failed]}"
        )


def test_one_pen_mark_produces_one_finding(pipeline_results):
    """invoice_08 has pen on its bank details. That is one problem, not two.

    The severe check states it in full; a second, weaker "something was
    handwritten" beside it is the same news told worse.
    """
    result, _, _ = pipeline_results["invoice_08.jpg"]
    handwriting = [c for c in result.failed if c.name.startswith("handwriting")]
    assert [c.name for c in handwriting] == ["handwriting.on_payment_details"]


def test_a_harmless_mark_is_still_mentioned(pipeline_results):
    """invoice_04's 受領 stamp changes nothing, but the reviewer should know."""
    result, _, _ = pipeline_results["invoice_04.jpg"]
    handwriting = [c for c in result.failed if c.name.startswith("handwriting")]
    assert [c.name for c in handwriting] == ["handwriting.detected"]


def test_the_note_is_never_wrapped_in_its_own_description(ground_truth, master):
    """Re-checking must not re-prefix the note.

    verify_invoice used to recover the note from the check's message rather than
    its detail, so each pass wrapped the previous sentence in another copy of
    "Handwritten annotation present:". Policy changes re-check the whole queue,
    so it compounded quickly.
    """
    from app.pipeline.verify import run_checks

    invoice = build("invoice_08.jpg", ground_truth["invoice_08.jpg"])
    invoice.has_handwriting = True
    invoice.handwriting_affects_payment = False
    invoice.handwriting_notes = "受領 1/20 経理"
    match = master.resolve(invoice.supplier_name, invoice.supplier_registration_no)

    for _ in range(3):
        result = run_checks(invoice, match, amount_review_threshold=0)
        detected = next(c for c in result.checks if c.name == "handwriting.detected")
        # Whatever a later pass reads back must be the note itself.
        invoice.handwriting_notes = (detected.detail or {}).get("notes", "")

    assert invoice.handwriting_notes == "受領 1/20 経理"
    assert detected.message.count("Handwritten annotation present") == 1
