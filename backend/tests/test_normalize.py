"""Normalisation is where date and numeral errors would become wrong payments.

Every case here is a string that actually appears on one of the 12 sample
invoices, plus the adversarial variants a different supplier would produce.
"""

from datetime import date

import pytest

from app.pipeline.normalize import (
    parse_amount,
    parse_date,
    tax_for,
    tax_rate_to_code,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("令和8年2月5日", date(2026, 2, 5)),      # invoice_11 issue date
        ("令和8年3月31日", date(2026, 3, 31)),     # invoice_11 due date
        ("令和元年5月1日", date(2019, 5, 1)),       # 元年 == year 1
        ("平成31年4月30日", date(2019, 4, 30)),    # the era before
        ("2026年1月7日", date(2026, 1, 7)),        # invoice_01
        ("2026/01/18", date(2026, 1, 18)),        # invoice_04
        ("2026-02-28", date(2026, 2, 28)),
        ("2026.02.28", date(2026, 2, 28)),
        ("２０２６年１月７日", date(2026, 1, 7)),      # full-width digits
        ("お支払期日: 2026年2月28日", date(2026, 2, 28)),  # label still attached
    ],
)
def test_dates_parse(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "not a date", "2026-02-30", "13月"])
def test_bad_dates_return_none_rather_than_guessing(raw):
    assert parse_date(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("¥240,900", 240900),
        ("1,560,988", 1560988),
        ("△30,000", -30000),      # invoice_12 discount
        ("▲30,000", -30000),
        ("△ 30,000", -30000),
        ("(30,000)", -30000),      # accounting parentheses
        ("-30,000", -30000),
        ("１２３，４５６", 123456),   # full-width digits and comma
        ("18,000円", 18000),
        ("150000.00", 150000),
        (0, 0),
        (18000, 18000),
    ],
)
def test_amounts_parse(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "abc", "—", True])
def test_bad_amounts_return_none(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize(
    "subtotal,code,expected",
    [
        (134088, "T10", 13408),   # invoice_09: 13,408.8 floors down, never up
        (75840, "T08", 6067),     # invoice_03: 6,067.2
        (39500, "T10", 3950),
        (103200, "T08", 8256),    # invoice_08
        (6800, "T10", 680),
        (1419080, "T10", 141908), # invoice_02
        (540000, "T10", 54000),   # invoice_12, after the negative line
    ],
)
def test_tax_is_floored_exactly_as_the_accounting_api_does(subtotal, code, expected):
    assert tax_for(subtotal, code) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("10%", "T10"), ("8%", "T08"), ("8", "T08"), (10, "T10"), (0.08, "T08"), ("１０％", "T10")],
)
def test_tax_rates_map_to_codes(raw, expected):
    assert tax_rate_to_code(raw) == expected


def test_unknown_tax_rate_is_not_guessed():
    assert tax_rate_to_code("5%") is None
    assert tax_rate_to_code("") is None
    assert tax_rate_to_code(None, default="T10") == "T10"
