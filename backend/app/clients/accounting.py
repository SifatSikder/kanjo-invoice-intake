"""Client for the existing accounting system.

Its specification is fixed and we may not change it, so every constraint it
imposes is handled on our side:

  - dates must be YYYY-MM-DD                  -> normalize.parse_date
  - amounts are integers in JPY               -> normalize.parse_amount
  - tax arrives as a code, never a rate       -> normalize.tax_rate_to_code
  - only suppliers in the master may post     -> pipeline.partners
  - it recalculates the totals and rejects    -> pipeline.verify (pre-flight)
    anything that disagrees

The one constraint we cannot fix is the absence of an idempotency key: if a POST
times out, we cannot tell whether it registered. Blind retry risks a double
registration, which is the exact failure the client complained about. So we never
blind-retry -- `confirm_registered` re-reads the ledger instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ApiResult:
    ok: bool
    status: int
    data: dict | None = None
    error_code: str | None = None
    error_message: str = ""
    error_details: dict | None = None
    body: dict | None = None
    transport_error: str | None = None

    @property
    def indeterminate(self) -> bool:
        """True when we do not know whether the server acted on our request."""
        return self.transport_error is not None


class AccountingClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 20.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self._timeout = timeout

    async def _request(self, method: str, path: str, json: Any = None) -> ApiResult:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, f"{self._base_url}{path}", headers=self._headers, json=json
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.error("accounting API transport failure on %s %s: %s", method, path, exc)
            return ApiResult(ok=False, status=0, transport_error=str(exc))

        try:
            body = response.json()
        except ValueError:
            return ApiResult(
                ok=False, status=response.status_code,
                error_code="UNPARSEABLE_RESPONSE", error_message=response.text[:400],
            )

        if body.get("success"):
            return ApiResult(ok=True, status=response.status_code, data=body.get("data"), body=body)

        error = body.get("error") or {}
        return ApiResult(
            ok=False,
            status=response.status_code,
            error_code=error.get("code"),
            error_message=error.get("message", ""),
            error_details=error.get("details"),
            body=body,
        )

    async def health(self) -> ApiResult:
        return await self._request("GET", "/health")

    async def get_partners(self) -> list[dict]:
        result = await self._request("GET", "/partners")
        if not result.ok:
            raise RuntimeError(f"could not load the partner master: {result.error_message}")
        return (result.data or {}).get("partners", [])

    async def get_tax_codes(self) -> list[dict]:
        result = await self._request("GET", "/tax-codes")
        if not result.ok:
            raise RuntimeError(f"could not load tax codes: {result.error_message}")
        return (result.data or {}).get("tax_codes", [])

    async def list_invoices(self) -> list[dict]:
        result = await self._request("GET", "/invoices")
        if not result.ok:
            raise RuntimeError(f"could not list invoices: {result.error_message}")
        return (result.data or {}).get("invoices", [])

    async def create_invoice(self, payload: dict) -> ApiResult:
        return await self._request("POST", "/invoices", json=payload)

    async def delete_all_invoices(self) -> ApiResult:
        return await self._request("DELETE", "/invoices")

    async def confirm_registered(self, partner_code: str, invoice_number: str) -> dict | None:
        """Did this invoice actually land? Used after an indeterminate POST.

        This is the safe alternative to retrying a request that may already have
        succeeded. It is only reliable because (partner_code, invoice_number) is
        unique in the accounting system.
        """
        try:
            for record in await self.list_invoices():
                if (
                    record.get("partner_code") == partner_code
                    and record.get("invoice_number") == invoice_number
                ):
                    return record
        except RuntimeError:
            logger.exception("could not confirm registration for %s/%s", partner_code, invoice_number)
        return None
