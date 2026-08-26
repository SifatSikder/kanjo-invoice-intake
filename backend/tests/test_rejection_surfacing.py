"""An invoice that stopped must always say why.

Our own checks catch most problems before we post. The accounting system can
still refuse one afterwards -- most often because the invoice number is already
taken there, which happens whenever our local record was removed but the
registration was not. That refusal used to live only in a notes column nothing
displayed, so the queue showed a blocked row with an empty reason: the exact
silent failure this pipeline exists to prevent.
"""

from app.clients.accounting import ApiResult
from app.models import Invoice, Severity
from app.pipeline.post import _record_rejection


def invoice() -> Invoice:
    return Invoice(lines=[], checks=[], postings=[], review_events=[])


def rejection(code: str, message: str, status: int = 400) -> ApiResult:
    return ApiResult(ok=False, status=status, error_code=code, error_message=message)


def test_a_refusal_becomes_a_failed_check():
    inv = invoice()
    _record_rejection(inv, rejection(
        "DUPLICATE_INVOICE", "This invoice number is already registered for this partner", 409
    ))

    check = next(c for c in inv.checks if c.name == "accounting.accepted")
    assert check.passed is False
    assert "already registered" in check.message
    assert check.detail["error_code"] == "DUPLICATE_INVOICE"
    assert check.detail["http_status"] == 409


def test_unpostable_refusals_block_rather_than_queue_for_review():
    """A duplicate or an unknown supplier is not something a reviewer can fix."""
    for code in ("DUPLICATE_INVOICE", "PARTNER_NOT_FOUND"):
        inv = invoice()
        _record_rejection(inv, rejection(code, "nope"))
        assert inv.checks[0].severity is Severity.BLOCKER, code


def test_correctable_refusals_go_to_a_human():
    for code in ("AMOUNT_MISMATCH", "VALIDATION_ERROR", "UNKNOWN_TAX_CODE"):
        inv = invoice()
        _record_rejection(inv, rejection(code, "nope", 422))
        assert inv.checks[0].severity is Severity.ERROR, code


def test_retrying_replaces_the_verdict_rather_than_stacking_them():
    inv = invoice()
    _record_rejection(inv, rejection("VALIDATION_ERROR", "first attempt"))
    _record_rejection(inv, rejection("AMOUNT_MISMATCH", "second attempt", 422))

    recorded = [c for c in inv.checks if c.name == "accounting.accepted"]
    assert len(recorded) == 1
    assert recorded[0].message == "second attempt"


def test_a_refusal_with_no_code_still_says_something():
    inv = invoice()
    _record_rejection(inv, ApiResult(ok=False, status=500))
    assert inv.checks[0].message
    assert inv.checks[0].passed is False
