"""Building the accounting payload.

The payload is where our record meets a contract we do not control, so it is the
only place a value may be substituted to satisfy that contract -- and when one is,
our own record still says what was actually read.
"""

from datetime import date

import pytest

from app.models import Invoice, InvoiceLine, InvoiceStatus
from app.pipeline.post import build_payload


def invoice(lines: list[InvoiceLine]) -> Invoice:
    return Invoice(
        id=1, document_id=1, status=InvoiceStatus.EXTRACTED,
        partner_code="P-1001", invoice_number="YM-2026-0107",
        issue_date=date(2026, 1, 7), due_date=date(2026, 2, 28),
        subtotal=304000, tax_amount=30400, total_amount=334400,
        lines=lines, checks=[], postings=[], review_events=[],
    )


def line(seq, amount, unit="個", qty=None, price=None, tax="T10") -> InvoiceLine:
    return InvoiceLine(seq=seq, description=f"item {seq}", quantity=qty, unit=unit,
                       unit_price=price, amount=amount, tax_code=tax)


def test_totals_come_from_the_lines_not_the_printed_figures():
    """invoice_09 prints a total 1 JPY above its own lines. The accounting system
    recalculates, so the only total it will accept is the one the lines produce."""
    inv = invoice([line(1, 101121), line(2, 32967)])
    inv.subtotal, inv.tax_amount, inv.total_amount = 134088, 13408, 147497  # as printed
    payload = build_payload(inv)
    assert payload.subtotal == 134088
    assert payload.tax_amount == 13408
    assert payload.total_amount == 147496  # not the printed 147,497


def test_missing_unit_is_substituted_only_in_the_payload():
    inv = invoice([line(1, 150000, unit=""), line(2, 18000, unit="式")])
    payload = build_payload(inv)
    assert payload.lines[0].unit == "式"     # the API rejects an empty unit
    assert inv.lines[0].unit == ""           # our record still says "not read"


def test_tax_is_summed_per_code_and_floored():
    inv = invoice([line(1, 103200, tax="T08"), line(2, 6800, tax="T10")])
    payload = build_payload(inv)
    assert payload.subtotal == 110000
    assert payload.tax_amount == 8936        # 8,256 + 680
    assert payload.total_amount == 118936


def test_negative_line_reduces_the_payload_total():
    inv = invoice([line(1, 450000), line(2, 120000), line(3, -30000)])
    payload = build_payload(inv)
    assert payload.subtotal == 540000
    assert payload.total_amount == 594000


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda i: setattr(i, "partner_code", None), "partner_code"),
        (lambda i: i.lines.clear(), "line items"),
        (lambda i: setattr(i, "due_date", None), "dates"),
    ],
)
def test_refuses_to_build_an_incomplete_payload(mutate, expected):
    inv = invoice([line(1, 1000)])
    mutate(inv)
    with pytest.raises(ValueError, match=expected):
        build_payload(inv)
