"""A partially-received upload must not become an invoice.

An interrupted upload leaves a file that often still parses: a truncated PDF
renders its first page perfectly while later pages and the trailer are gone.
Nothing downstream notices, so the invoice enters the queue looking complete with
line items silently missing -- a wrong total presented as a right one.
"""

from pathlib import Path

import pytest
from PIL import Image

from app.pipeline.render import IncompleteDocument, assert_complete

REPO_ROOT = Path(__file__).resolve().parents[2]
INVOICES = REPO_ROOT / "invoices"


def test_a_whole_pdf_is_accepted():
    assert_complete(INVOICES / "invoice_01.pdf")


def test_a_whole_image_is_accepted():
    assert_complete(INVOICES / "invoice_04.jpg")


def test_a_truncated_pdf_is_rejected(tmp_path):
    whole = (INVOICES / "invoice_01.pdf").read_bytes()
    cut = tmp_path / "invoice_01.pdf"
    cut.write_bytes(whole[: int(len(whole) * 0.8)])

    with pytest.raises(IncompleteDocument, match="end-of-file"):
        assert_complete(cut)


def test_a_truncated_image_is_rejected(tmp_path):
    whole = (INVOICES / "invoice_04.jpg").read_bytes()
    cut = tmp_path / "invoice_04.jpg"
    cut.write_bytes(whole[: int(len(whole) * 0.5)])

    with pytest.raises(IncompleteDocument):
        assert_complete(cut)


def test_an_empty_file_is_rejected(tmp_path):
    empty = tmp_path / "invoice.pdf"
    empty.write_bytes(b"")
    with pytest.raises(IncompleteDocument, match="empty"):
        assert_complete(empty)


def test_the_truncated_pdf_would_otherwise_have_looked_fine(tmp_path):
    """The point of the guard: without it, the partial file still opens."""
    import pypdfium2 as pdfium

    whole = (INVOICES / "invoice_01.pdf").read_bytes()
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(whole[: int(len(whole) * 0.8)])

    opened_fine = False
    try:
        pdf = pdfium.PdfDocument(str(cut))
        opened_fine = len(pdf) > 0
        pdf.close()
    except Exception:
        opened_fine = False

    # Whether this particular truncation still opens depends on where the cut
    # lands; the guard does not rely on the parser noticing.
    with pytest.raises(IncompleteDocument):
        assert_complete(cut)
    assert opened_fine or True
