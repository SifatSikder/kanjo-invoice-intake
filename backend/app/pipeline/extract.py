"""Ask a vision model to transcribe an invoice, then convert what it says.

The prompt does one job and refuses the rest: **report the characters printed on
the page**. It is explicitly forbidden from doing arithmetic, converting a
Japanese era date, choosing a tax code, or identifying the supplier against any
master. Every one of those is done afterwards by app/pipeline/normalize.py, in
code that is unit tested.

That split is the whole safety argument. A model that is only ever asked "what
does this say?" fails in a way the totals check can catch. A model asked "what is
the total?" can return a confident, plausible, wrong number that reconciles with
nothing, and there is no check that finds it.
"""

from __future__ import annotations

import logging

from app.clients.openrouter import Completion, OpenRouterClient
from app.pipeline.normalize import (
    normalize_text,
    normalize_unit,
    parse_amount,
    parse_date,
    parse_quantity,
    tax_rate_to_code,
)
from app.pipeline.render import RenderedDocument
from app.schemas import NormalizedInvoice, NormalizedLine, RawExtraction

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"

_STR = {"type": "string"}

EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "supplier_name", "supplier_registration_no", "invoice_number",
        "issue_date_raw", "due_date_raw", "subtotal_raw", "tax_amount_raw",
        "total_amount_raw", "currency", "bank_details_raw", "lines", "tax_breakdown",
        "handwritten_annotations", "field_confidence", "notes",
    ],
    "properties": {
        "supplier_name": _STR,
        "supplier_registration_no": _STR,
        "invoice_number": _STR,
        "issue_date_raw": _STR,
        "due_date_raw": _STR,
        "subtotal_raw": _STR,
        "tax_amount_raw": _STR,
        "total_amount_raw": _STR,
        "currency": _STR,
        "bank_details_raw": _STR,
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["description", "quantity_raw", "unit", "unit_price_raw",
                             "amount_raw", "tax_rate_raw"],
                "properties": {
                    "description": _STR, "quantity_raw": _STR, "unit": _STR,
                    "unit_price_raw": _STR, "amount_raw": _STR, "tax_rate_raw": _STR,
                },
            },
        },
        "tax_breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rate_raw", "base_raw", "tax_raw"],
                "properties": {"rate_raw": _STR, "base_raw": _STR, "tax_raw": _STR},
            },
        },
        "handwritten_annotations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "location", "affects_payment_details"],
                "properties": {
                    "text": _STR, "location": _STR,
                    "affects_payment_details": {"type": "boolean"},
                },
            },
        },
        "field_confidence": {
            "type": "object",
            "additionalProperties": False,
            "required": ["supplier_name", "supplier_registration_no", "invoice_number",
                         "issue_date", "due_date", "subtotal", "tax_amount",
                         "total_amount", "lines"],
            "properties": {
                k: {"type": "number"} for k in
                ("supplier_name", "supplier_registration_no", "invoice_number",
                 "issue_date", "due_date", "subtotal", "tax_amount", "total_amount", "lines")
            },
        },
        "notes": _STR,
    },
}

SYSTEM_PROMPT = """\
You transcribe Japanese business invoices (請求書 / 御請求書) into JSON.

You are a TRANSCRIBER, not an accountant. Report exactly what is printed.

ABSOLUTE RULES
1. NEVER calculate, sum, convert, or correct anything. If the printed numbers do
   not add up, report them as printed. Detecting that is someone else's job, and
   silently "fixing" it destroys the evidence.
2. Copy every amount EXACTLY as printed, keeping separators and any leading
   minus marker. A discount shown as "△30,000" or "▲30,000" must be reported as
   "△30,000" -- do not convert it to -30000 or 30000.
3. Copy every date EXACTLY as printed. "令和8年2月5日" stays "令和8年2月5日".
   Do NOT convert Japanese era years to the Western calendar.
4. If a cell is blank, return an empty string. Never invent a value.

WHO IS THE SUPPLIER
The supplier is the company ISSUING the invoice and requesting payment. On these
layouts it appears on the RIGHT side, usually above an address, a TEL and a
登録番号 (registration number).
The company followed by 御中 (typically top LEFT, "株式会社サンプル商事 経理部 御中")
is the RECIPIENT being billed. NEVER report the 御中 company as the supplier.
Report supplier_registration_no from the 登録番号 near the supplier's name.

LINE ITEMS (品名・摘要)
- One entry per printed row, in printed order.
- 数量 = quantity, 単位 = unit, 単価 = unit price, 金額 = amount, 税率 = tax rate.
- The 単位 column is easy to miss because it is narrow. Read it for every row and
  copy it exactly: 個, 箱, 本, 袋, 件, 式, 時間, セット, kg, m. Return "" only when
  that cell is genuinely blank.
- Service and lump-sum rows often have 単位 "式" with no quantity or unit price.
  Return empty strings for those, not zeros.
- Only fill tax_rate_raw when the row itself shows a rate in a 税率 column.
  Leave it "" when the invoice states a single tax rate near the totals instead.
- MULTI-PAGE: if the document continues across pages, merge every line item from
  every page into one list, in order. Totals normally appear on the final page.
  Do not repeat a header row or a "(明細つづき)" continuation marker as a line.

TOTALS
subtotal_raw = 小計, tax_amount_raw = the consumption tax total (消費税), and
total_amount_raw = 合計 or 御請求金額. When several 消費税 rows are printed (for
example 8% and 10% separately), list each in tax_breakdown with its rate, its
base (the 対象 amount) and its tax, AND report their sum in tax_amount_raw.

BANK TRANSFER DETAILS
Copy the お振込先 line exactly as printed -- bank, branch, account type and
number -- into bank_details_raw. Copy only what is PRINTED there. If a pen has
altered it, the alteration belongs in handwritten_annotations, not here, so that
the original and the change can be told apart.

HANDWRITING
Report anything handwritten, stamped or added in coloured pen, separately from
the printed content -- never merge it into a printed field. Set
affects_payment_details to true when the annotation touches bank or transfer
details (お振込先, account numbers, payee name) or changes an amount or a date.
Set it to false for routine workflow marks such as a 受領 received stamp, a
department name, or 至急.

CONFIDENCE
field_confidence values run 0.0-1.0 and must be honest. Lower the score when
print is faint, skewed, cut off, overwritten by pen, or genuinely ambiguous. An
overconfident wrong answer is far more damaging here than an admitted doubt.
"""


def build_messages(document: RenderedDocument) -> list[dict]:
    """One call carrying every page image plus the text layer, when there is one."""
    content: list[dict] = []

    header = (
        f"Invoice document: {document.filename}\n"
        f"Pages: {document.page_count}\n"
    )
    if document.has_text_layer:
        header += (
            "\nThis PDF has an embedded text layer, reproduced below. Use it to "
            "confirm the exact characters of digits and identifiers, which is where "
            "reading from pixels goes wrong.\n"
            "IMPORTANT: the text layer can be INCOMPLETE. Some columns extract "
            "badly and go missing from it entirely. It is corroboration, never an "
            "authority that overrides the page. If a value is visible in the image "
            "but absent from the text below, report what the image shows -- never "
            "return an empty field just because the text layer lacks it.\n"
            f"\n--- BEGIN TEXT LAYER ---\n{document.text_layer}\n--- END TEXT LAYER ---\n"
        )
    else:
        header += (
            "\nThis document has no text layer (it is a scan or photograph). Read it "
            "from the images. If a value is genuinely unreadable, return an empty "
            "string and lower the confidence for that field rather than guessing.\n"
        )
    content.append({"type": "text", "text": header})

    for page in document.pages:
        if document.page_count > 1:
            content.append({"type": "text", "text": f"--- page {page.index} of {document.page_count} ---"})
        content.append({"type": "image_url", "image_url": {"url": page.to_data_url()}})

    content.append({
        "type": "text",
        "text": "Transcribe this invoice into the required JSON structure. "
                "Report what is printed; do not compute or convert anything.",
    })

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


async def extract_document(
    client: OpenRouterClient, document: RenderedDocument, *, model: str
) -> tuple[RawExtraction, Completion]:
    completion = await client.complete(
        model=model,
        messages=build_messages(document),
        json_schema=EXTRACTION_SCHEMA,
    )
    payload = completion.json()
    return RawExtraction.model_validate(payload), completion


# ---------------------------------------------------------------------------
# Raw transcription -> our interpretation. Pure, deterministic, unit tested.
# ---------------------------------------------------------------------------


def _resolve_tax_codes(raw: RawExtraction) -> tuple[list[str | None], list[str]]:
    """Decide a tax code per line, or admit that we cannot.

    Preference order:
      1. a rate printed on the line itself (invoices 03 and 08 have a 税率 column)
      2. a single rate stated once near the totals -- applies to every line
      3. give up. With several tax buckets and no per-line rates, assigning them
         would be guesswork, and guessing wrong changes the tax we file.
    """
    unmapped: list[str] = []
    per_line = [normalize_text(line.tax_rate_raw) for line in raw.lines]

    breakdown_codes: list[str] = []
    for entry in raw.tax_breakdown:
        code = tax_rate_to_code(entry.rate_raw)
        if code:
            breakdown_codes.append(code)
        elif normalize_text(entry.rate_raw):
            unmapped.append(normalize_text(entry.rate_raw))
    distinct = sorted(set(breakdown_codes))

    fallback: str | None
    if len(distinct) == 1:
        fallback = distinct[0]
    elif not distinct:
        fallback = "T10"  # the standard rate; no other rate is mentioned anywhere
    else:
        fallback = None   # ambiguous -> force a human

    codes: list[str | None] = []
    for rate_raw in per_line:
        if rate_raw:
            code = tax_rate_to_code(rate_raw)
            if code is None:
                unmapped.append(rate_raw)
            codes.append(code)
        else:
            codes.append(fallback)

    if fallback is None and any(c is None for c in codes):
        unmapped.append(
            f"invoice states {len(distinct)} tax rates but the line items carry no 税率 column"
        )
    return codes, unmapped


def normalize_extraction(raw: RawExtraction) -> NormalizedInvoice:
    """Convert a transcription into typed, computed values."""
    tax_codes, unmapped = _resolve_tax_codes(raw)

    lines: list[NormalizedLine] = []
    for index, (line, code) in enumerate(zip(raw.lines, tax_codes), start=1):
        amount = parse_amount(line.amount_raw)
        description = normalize_text(line.description)
        if amount is None or not description:
            # A row we cannot price is not a line item -- most often a header or a
            # continuation marker the model echoed. Dropping it would change the
            # subtotal silently, so record it and let the totals check object.
            logger.info("skipping unusable line %s (%r / %r)", index, description, line.amount_raw)
            continue
        lines.append(
            NormalizedLine(
                seq=len(lines) + 1,
                description=description,
                quantity=parse_quantity(line.quantity_raw),
                unit=normalize_unit(line.unit),
                unit_price=parse_amount(line.unit_price_raw),
                amount=amount,
                tax_code=code or "UNKNOWN",
                tax_rate_raw=normalize_text(line.tax_rate_raw) or None,
            )
        )

    handwriting = raw.handwritten_annotations
    affects_payment = any(a.affects_payment_details for a in handwriting)
    notes = "; ".join(
        f"{normalize_text(a.text)} ({normalize_text(a.location)})".strip()
        for a in handwriting
        if normalize_text(a.text)
    )

    confidences = raw.field_confidence.model_dump().values()

    return NormalizedInvoice(
        supplier_name=normalize_text(raw.supplier_name),
        supplier_registration_no=normalize_text(raw.supplier_registration_no),
        invoice_number=normalize_text(raw.invoice_number),
        issue_date=parse_date(raw.issue_date_raw),
        due_date=parse_date(raw.due_date_raw),
        issue_date_raw=normalize_text(raw.issue_date_raw),
        due_date_raw=normalize_text(raw.due_date_raw),
        currency=normalize_text(raw.currency) or "JPY",
        bank_details=normalize_text(raw.bank_details_raw),
        subtotal=parse_amount(raw.subtotal_raw),
        tax_amount=parse_amount(raw.tax_amount_raw),
        total_amount=parse_amount(raw.total_amount_raw),
        lines=lines,
        min_confidence=min(confidences) if confidences else 1.0,
        has_handwriting=bool(handwriting),
        handwriting_affects_payment=affects_payment,
        handwriting_notes=notes,
        unmapped_tax_rates=sorted(set(unmapped)),
        notes=normalize_text(raw.notes),
    )
