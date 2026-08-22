"""Shared request dependencies."""

from __future__ import annotations

from app.clients.accounting import AccountingClient
from app.clients.openrouter import OpenRouterClient
from app.config import settings


def accounting_client() -> AccountingClient:
    return AccountingClient(
        settings.accounting_api_base,
        settings.accounting_api_key,
        timeout=settings.accounting_timeout_seconds,
    )


def openrouter_client() -> OpenRouterClient:
    return OpenRouterClient(
        settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        timeout=settings.extraction_timeout_seconds,
    )
