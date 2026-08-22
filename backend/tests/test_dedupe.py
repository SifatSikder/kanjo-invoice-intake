"""Duplicate detection -- the failure the client's email actually described."""

from datetime import date

from app.pipeline.dedupe import find_duplicates_in

EXISTING = [
    {
        "invoice_id": 1, "partner_code": "P-1001", "invoice_number": "YM-2026-0107",
        "total_amount": 334400, "issue_date": date(2026, 1, 7),
        "filename": "invoice_01.pdf", "accounting_id": "ACC-0001",
    }
]


def test_same_supplier_same_number_is_an_exact_duplicate():
    found = find_duplicates_in(
        partner_code="P-1001", invoice_number="YM-2026-0107",
        total_amount=334400, issue_date=date(2026, 1, 7), existing=EXISTING,
    )
    assert found.exact is not None
    assert found.exact["filename"] == "invoice_01.pdf"


def test_matching_is_case_and_whitespace_insensitive():
    found = find_duplicates_in(
        partner_code="P-1001", invoice_number="  ym-2026-0107 ",
        total_amount=334400, issue_date=date(2026, 1, 7), existing=EXISTING,
    )
    assert found.exact is not None


def test_same_number_for_a_different_supplier_is_not_a_duplicate():
    found = find_duplicates_in(
        partner_code="P-1002", invoice_number="YM-2026-0107",
        total_amount=334400, issue_date=date(2026, 1, 7), existing=EXISTING,
    )
    assert found.exact is None and not found.near


def test_reissued_invoice_with_a_new_number_is_caught_as_a_near_duplicate():
    """The API's own uniqueness rule would miss this entirely."""
    found = find_duplicates_in(
        partner_code="P-1001", invoice_number="YM-2026-0107-R2",
        total_amount=334400, issue_date=date(2026, 1, 9), existing=EXISTING,
    )
    assert found.exact is None
    assert len(found.near) == 1


def test_same_total_far_apart_in_time_is_not_flagged():
    """A supplier legitimately billing the same monthly amount must not jam the queue."""
    found = find_duplicates_in(
        partner_code="P-1001", invoice_number="YM-2026-0207",
        total_amount=334400, issue_date=date(2026, 2, 7), existing=EXISTING,
    )
    assert found.exact is None and not found.near


def test_unresolved_supplier_short_circuits():
    found = find_duplicates_in(
        partner_code=None, invoice_number="X", total_amount=1, issue_date=date(2026, 1, 1),
        existing=EXISTING,
    )
    assert found.exact is None and not found.near
