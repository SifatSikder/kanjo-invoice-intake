"""FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, invoices, policy, review, upload
from app.api.deps import accounting_client
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    health = await accounting_client().health()
    if health.ok:
        logger.info("accounting system reachable at %s", settings.accounting_api_base)
    else:
        logger.warning(
            "accounting system NOT reachable at %s -- nothing can be registered "
            "until it is running", settings.accounting_api_base,
        )
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Invoice Intake",
    description=(
        "AI-assisted invoice intake with a verification gate. Extracts supplier "
        "invoices, checks the result against the accounting system's own rules, "
        "and registers only what passes -- routing the rest to a human."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # demo only; a deployment would name the review UI's origin
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(invoices.router)
app.include_router(review.router)
app.include_router(upload.router)
app.include_router(policy.router)
app.include_router(admin.router)


@app.get("/health")
async def health() -> dict:
    downstream = await accounting_client().health()
    return {
        "status": "ok",
        "accounting_api": "up" if downstream.ok else "down",
        "model": settings.extraction_model,
    }
