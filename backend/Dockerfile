FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

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
