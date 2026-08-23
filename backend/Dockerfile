FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Japanese fonts are required to RENDER these PDFs, not merely to display them
# nicely. The sample invoices reference HeiseiKakuGo-W5 and embed no font file,
# so on a slim image with no CJK font every Japanese glyph is silently dropped:
# the page renders with its numbers intact and all its labels missing. The
# reviewer would then be checking extracted values against a document they
# cannot read. IPA Gothic/Mincho cover Japanese in ~20MB, where fonts-noto-cjk
# would add well over 200MB.
# fontconfig matters as much as the fonts: pdfium discovers system fonts through
# it, so installing the font files alone changes nothing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         fontconfig fonts-ipafont-gothic fonts-ipafont-mincho \
    && fc-cache -fv > /dev/null \
    && rm -rf /var/lib/apt/lists/*

# poppler-utils supplies pdftotext for the PDF text layer.
#
# poppler-data is NOT optional here: it carries the Adobe-Japan1 CMap tables that
# map CID-encoded Japanese glyphs to Unicode. Without it poppler reports
# "Missing language pack for 'Adobe-Japan1' mapping" and extracts nothing at all
# from these invoices, and pdfium silently drops the same characters -- which is
# how the 単位 column went missing from the text layer while ASCII digits
# survived. macOS ships these resources, so the loss only appeared in the
# container.
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils poppler-data \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e . && pip install --no-cache-dir pyyaml

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 8000
CMD ["./entrypoint.sh"]
