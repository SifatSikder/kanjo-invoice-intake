"""Converting a transcription into typed values.

This is the seam between what the model claims it saw and what we are willing to
act on. Everything here runs on hand-written RawExtraction objects -- no network.
"""

from datetime import date

from app.pipeline.extract import normalize_extraction
from app.schemas import (
    FieldConfidence,
    RawExtraction,
    RawHandwriting,
    RawLine,
    RawTaxBreakdown,
)


def raw(**kw) -> RawExtraction:
    base = dict(
        supplier_name="株式会社山田製作所",
        supplier_registration_no="T1010001000101",
        invoice_number="YM-2026-0107",
        issue_date_raw="2026年1月7日",
        due_date_raw="2026年2月28日",
        subtotal_raw="304,000",
        tax_amount_raw="30,400",
        total_amount_raw="334,400",
    )
    base.update(kw)
    return RawExtraction(**base)


def line(desc="部品", qty="", unit="式", price="", amount="0", rate="") -> RawLine:
    return RawLine(
        description=desc, quantity_raw=qty, unit=unit,
        unit_price_raw=price, amount_raw=amount, tax_rate_raw=rate,
    )


def test_dates_and_amounts_are_converted_not_echoed():
    inv = normalize_extraction(raw(lines=[line(amount="304,000")]))
    assert inv.issue_date == date(2026, 1, 7)
    assert inv.due_date == date(2026, 2, 28)
    assert inv.issue_date_raw == "2026年1月7日"  # kept for the review screen
    assert inv.subtotal == 304000
    assert inv.total_amount == 334400


def test_triangle_discount_becomes_a_negative_line():
    """invoice_12: without this the subtotal is 60,000 JPY too high."""
    inv = normalize_extraction(raw(lines=[
        line("業務システム改修", amount="450,000"),
        line("追加ライセンス", qty="5", unit="本", price="24,000", amount="120,000"),
        line("値引き", amount="△30,000"),
    ]))
    assert [l.amount for l in inv.lines] == [450000, 120000, -30000]
    assert sum(l.amount for l in inv.lines) == 540000


def test_per_line_tax_rates_are_used_when_printed():
    """invoices 03 and 08 print a 税率 column."""
    inv = normalize_extraction(raw(lines=[
        line("冷凍食材セット", amount="103,200", rate="8%"),
        line("保冷配送料", amount="6,800", rate="10%"),
    ]))
    assert [l.tax_code for l in inv.lines] == ["T08", "T10"]
    assert not inv.unmapped_tax_rates


def test_a_single_stated_rate_applies_to_every_line():
    """Most invoices state one rate near the totals and no per-line column."""
    inv = normalize_extraction(raw(
        lines=[line(amount="150,000"), line(amount="18,000")],
        tax_breakdown=[RawTaxBreakdown(rate_raw="10%", base_raw="168,000", tax_raw="16,800")],
    ))
    assert {l.tax_code for l in inv.lines} == {"T10"}


def test_several_rates_with_no_per_line_column_refuses_to_guess():
    """Assigning tax by guesswork changes what gets filed. Force a human instead."""
    inv = normalize_extraction(raw(
        lines=[line(amount="100,000"), line(amount="10,000")],
        tax_breakdown=[
            RawTaxBreakdown(rate_raw="8%", base_raw="100,000", tax_raw="8,000"),
            RawTaxBreakdown(rate_raw="10%", base_raw="10,000", tax_raw="1,000"),
        ],
    ))
    assert inv.unmapped_tax_rates
    assert any(l.tax_code == "UNKNOWN" for l in inv.lines)


def test_no_rate_anywhere_falls_back_to_the_standard_rate():
    inv = normalize_extraction(raw(lines=[line(amount="1,000")]))
    assert inv.lines[0].tax_code == "T10"


def test_blank_quantity_stays_null_rather_than_becoming_zero():
    """A 式 row has no quantity. Reporting 0 would be a different claim."""
    inv = normalize_extraction(raw(lines=[line("梱包・輸送費", amount="18,000")]))
    assert inv.lines[0].quantity is None
    assert inv.lines[0].unit_price is None
    assert inv.lines[0].unit == "式"


def test_unusable_rows_are_dropped_not_guessed():
    """Header rows and continuation markers the model echoes must not become lines."""
    inv = normalize_extraction(raw(lines=[
        line("精密部品A-100", amount="150,000"),
        line("（明細つづき）", amount=""),   # no amount -> not a line item
        line("", amount="999"),             # no description -> not a line item
    ]))
    assert len(inv.lines) == 1
    assert [l.seq for l in inv.lines] == [1]


def test_handwriting_on_payment_details_is_distinguished_from_a_stamp():
    stamp = normalize_extraction(raw(lines=[line(amount="1")], handwritten_annotations=[
        RawHandwriting(text="受領 1/20 経理", location="near the addressee",
                       affects_payment_details=False),
    ]))
    assert stamp.has_handwriting and not stamp.handwriting_affects_payment

    pen = normalize_extraction(raw(lines=[line(amount="1")], handwritten_annotations=[
        RawHandwriting(text="→3475 に変更", location="on the bank account line",
                       affects_payment_details=True),
    ]))
    assert pen.has_handwriting and pen.handwriting_affects_payment


def test_min_confidence_is_the_worst_field_not_the_average():
    inv = normalize_extraction(raw(
        lines=[line(amount="1")],
        field_confidence=FieldConfidence(invoice_number=0.4, subtotal=0.99),
    ))
    assert inv.min_confidence == 0.4


def test_unreadable_dates_stay_none_so_the_check_can_object():
    inv = normalize_extraction(raw(lines=[line(amount="1")], issue_date_raw="", due_date_raw="?"))
    assert inv.issue_date is None and inv.due_date is None
