"""Deterministic normalisation of everything the model transcribed.

Governing rule of this project: **the model transcribes, our code computes.**

The LLM is asked only to report the characters it can see on the page. Every
derived value -- a Gregorian date, an integer amount, a tax code -- is produced
here, by code that is unit tested against the real sample invoices. Date
arithmetic and numeral conversion are exactly where language models fail
quietly, and a quiet failure in an accounts-payable pipeline is a wrong payment.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date

# Mirrors TAX_RATES in the mock accounting API. Deliberately float, and
# deliberately used with math.floor below, so that our pre-flight arithmetic is
# bit-identical to the recalculation the API performs on receipt. Switching to
# Decimal here would be "more correct" and would make us disagree with the
# system we have to integrate with.
TAX_RATES: dict[str, float] = {"T10": 0.10, "T08": 0.08}

# Japanese era -> the number you add to the era year to get the Gregorian year.
# Reiwa 1 = 2019, Heisei 1 = 1989, Showa 1 = 1926.
ERA_OFFSETS: dict[str, int] = {
    "令和": 2018,
    "R": 2018,
    "平成": 1988,
    "H": 1988,
    "昭和": 1925,
    "S": 1925,
}

_ERA_PATTERN = re.compile(
    r"(令和|平成|昭和|[RHS])\s*(元|\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"
)
_KANJI_YMD = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_NUMERIC_YMD = re.compile(r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})")

# Characters Japanese invoices use to mark a negative amount. A discount line
# printed as "△30,000" means minus 30,000; reading it as positive silently
# inflates the invoice, and the totals check would then fail for the wrong reason.
_NEGATIVE_PREFIXES = ("△", "▲", "▽", "-", "−", "－", "‐", "―")
_CURRENCY_NOISE = re.compile(r"[¥￥,、\s円]")


def to_halfwidth(value: str) -> str:
    """NFKC folds full-width digits/latin (１２３, ＡＢＣ) to ASCII."""
    return unicodedata.normalize("NFKC", value)


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return to_halfwidth(str(value)).strip()


def parse_date(raw: str | None) -> date | None:
    """Parse any date format seen on a Japanese invoice into a real date.

    Handles: 令和8年2月5日 / 平成31年4月1日 / 2026年1月7日 / 2026-01-07 /
    2026/01/18 / 2026.01.18, with full-width digits and 元年 for year one.
    Returns None rather than guessing when the string is not a date.
    """
    text = normalize_text(raw)
    if not text:
        return None

    era = _ERA_PATTERN.search(text)
    if era:
        era_name, era_year, month, day = era.groups()
        year_num = 1 if era_year == "元" else int(era_year)
        try:
            return date(ERA_OFFSETS[era_name] + year_num, int(month), int(day))
        except ValueError:
            return None

    for pattern in (_KANJI_YMD, _NUMERIC_YMD):
        match = pattern.search(text)
        if match:
            year, month, day = (int(g) for g in match.groups())
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def parse_amount(raw: str | int | float | None) -> int | None:
    """Parse a printed JPY amount into a signed integer.

    Strips currency noise, folds full-width digits, and honours the Japanese
    negative markers (△ ▲) as well as accounting parentheses.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)

    text = to_halfwidth(str(raw)).strip()
    if not text:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    while text[:1] in _NEGATIVE_PREFIXES:
        negative = not negative if text[:1] in ("-", "−", "－", "‐", "―") else True
        text = text[1:].strip()

    cleaned = _CURRENCY_NOISE.sub("", text)
    if not cleaned:
        return None
    # Amounts are integers in JPY; tolerate a trailing ".00" from the model.
    cleaned = re.sub(r"\.0+$", "", cleaned)
    if not re.fullmatch(r"\d+", cleaned):
        return None

    value = int(cleaned)
    return -value if negative else value


def parse_quantity(raw: str | int | float | None) -> int | None:
    """Quantity may legitimately be absent (a 式 / 'lot' line)."""
    return parse_amount(raw)


def tax_rate_to_code(raw: str | int | float | None, *, default: str | None = None) -> str | None:
    """Map a printed tax rate ('10%', '8', 0.08) to the accounting system's code.

    The API takes a code, never a rate. Returns None when the rate is present but
    unrecognised, so the caller can raise UNKNOWN_TAX_CODE rather than guess.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default

    text = to_halfwidth(str(raw)).strip().replace("%", "").strip()
    try:
        value = float(text)
    except ValueError:
        upper = text.upper()
        return upper if upper in TAX_RATES else None

    if value <= 1:  # expressed as a fraction, e.g. 0.08
        value *= 100
    rounded = round(value)
    if rounded == 10:
        return "T10"
    if rounded == 8:
        return "T08"
    return None


def tax_for(subtotal: int, tax_code: str) -> int:
    """Consumption tax for one tax code, rounded down.

    Identical to the accounting API: math.floor(subtotal * rate), same float rate.
    """
    return math.floor(subtotal * TAX_RATES[tax_code])


def subtotal_by_tax_code(lines) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for line in lines:
        code = line.tax_code if hasattr(line, "tax_code") else line["tax_code"]
        amount = line.amount if hasattr(line, "amount") else line["amount"]
        buckets[code] = buckets.get(code, 0) + amount
    return buckets


def expected_tax_by_code(lines) -> dict[str, int]:
    return {
        code: tax_for(subtotal, code)
        for code, subtotal in subtotal_by_tax_code(lines).items()
        if code in TAX_RATES
    }


def normalize_unit(raw: str | None) -> str:
    """The API requires a non-empty unit string; 式 ('lot') is the JP default."""
    text = normalize_text(raw)
    return text if text else "式"
