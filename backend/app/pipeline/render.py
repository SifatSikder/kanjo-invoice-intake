"""Turn any incoming document into what the extractor needs: page images and,
where one exists, the embedded text layer.

Three of the twelve samples are PDFs with a real text layer, one is a PDF that
contains only a scan, and the rest are camera/copier images. Rather than pick one
path, we send the model *both* the page image and the text layer together:

  - the text layer gives character-exact digits, with no OCR step to misread them
  - the image gives spatial layout, so the model knows which number is in which
    column and which row it belongs to

Neither alone is as good, and since we were making the vision call anyway, adding
the text costs a few hundred tokens. The text layer also doubles as the reference
for the grounding check in verify.py: values the model reports must actually
appear in it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

# Vision models gain nothing from more than ~1600px on the long edge for a
# document page, and image tokens scale with area, so this is the main cost dial.
MAX_EDGE_PX = 1600
JPEG_QUALITY = 88
# A PDF that yields less than this many characters is treated as a scan.
TEXT_LAYER_MIN_CHARS = 40

# Shown to a person when they upload something we cannot open.
SUPPORTED_SUFFIXES_HINT = "a PDF, JPG, PNG, TIFF or WebP"


@dataclass
class RenderedPage:
    index: int          # 1-based
    image_bytes: bytes
    media_type: str
    width: int
    height: int
    stored_path: str | None = None

    def to_data_url(self) -> str:
        encoded = base64.b64encode(self.image_bytes).decode("ascii")
        return f"data:{self.media_type};base64,{encoded}"


@dataclass
class RenderedDocument:
    filename: str
    sha256: str
    mime_type: str
    page_count: int
    pages: list[RenderedPage] = field(default_factory=list)
    text_layer: str = ""

    @property
    def has_text_layer(self) -> bool:
        return len(self.text_layer.strip()) >= TEXT_LAYER_MIN_CHARS


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encode(image: Image.Image) -> tuple[bytes, str, int, int]:
    """Downscale and encode. JPEG for photographs, PNG when there is alpha."""
    image = ImageOps.exif_transpose(image)  # honour camera/scanner rotation
    if image.width > MAX_EDGE_PX or image.height > MAX_EDGE_PX:
        image.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX), Image.LANCZOS)

    buffer = io.BytesIO()
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGB")
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue(), "image/jpeg", image.width, image.height


def _render_pdf(path: Path) -> tuple[list[RenderedPage], str, int]:
    pdf = pdfium.PdfDocument(str(path))
    try:
        pages: list[RenderedPage] = []
        text_parts: list[str] = []
        for index, page in enumerate(pdf, start=1):
            # Scale so the long edge lands near MAX_EDGE_PX. PDF user units are
            # 1/72 inch, so scale 1.0 == 72 DPI.
            long_edge_pt = max(page.get_width(), page.get_height())
            scale = min(4.0, max(1.5, MAX_EDGE_PX / long_edge_pt))
            image = page.render(scale=scale).to_pil()
            data, media_type, width, height = _encode(image)
            pages.append(RenderedPage(index, data, media_type, width, height))

            textpage = page.get_textpage()
            text_parts.append(textpage.get_text_bounded())
        return pages, "\n".join(text_parts), len(pages)
    finally:
        pdf.close()


def _render_image(path: Path) -> tuple[list[RenderedPage], str, int]:
    with Image.open(path) as image:
        data, media_type, width, height = _encode(image.copy())
    # A photograph has no text layer; the model has to read it from pixels.
    return [RenderedPage(1, data, media_type, width, height)], "", 1


def prepare_document(path: Path, *, storage_dir: Path | None = None) -> RenderedDocument:
    """Render `path` for extraction, optionally persisting page images for the UI."""
    path = Path(path)
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    if path.suffix.lower() == ".pdf":
        pages, text_layer, page_count = _render_pdf(path)
        mime_type = "application/pdf"
    else:
        pages, text_layer, page_count = _render_image(path)

    document = RenderedDocument(
        filename=path.name,
        sha256=sha256_of(path),
        mime_type=mime_type,
        page_count=page_count,
        pages=pages,
        text_layer=text_layer,
    )

    if storage_dir is not None:
        target = Path(storage_dir) / document.sha256
        target.mkdir(parents=True, exist_ok=True)
        for page in document.pages:
            page_path = target / f"page-{page.index:02d}.jpg"
            page_path.write_bytes(page.image_bytes)
            page.stored_path = str(page_path)
        if document.text_layer:
            (target / "text-layer.txt").write_text(document.text_layer, encoding="utf-8")

    return document
