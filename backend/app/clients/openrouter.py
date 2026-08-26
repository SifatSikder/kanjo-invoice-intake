"""OpenRouter client.

OpenRouter was chosen over calling a model vendor directly for one reason that
matters to this assignment: the brief asks which model we picked and why, and the
honest answer requires measuring several. One base URL and one key gives us a
free-tier model, a cheap one and a frontier one behind the same call, so
evals/run_eval.py can score them against the same ground truth and the model
becomes a config value rather than an architectural commitment.

It also gives failover: if a provider rate-limits, the next model in the list
takes the call rather than the batch dying at month-end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# Upstream trouble rather than a bad request: worth another attempt.
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class OpenRouterError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def json(self) -> Any:
        """Parse the response body as JSON, tolerating fenced or prefixed output."""
        text = self.text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip().rstrip("`").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Models occasionally wrap the object in prose. Take the outermost braces.
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                return json.loads(text[start : end + 1])
            raise


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 180.0,
        referer: str = "https://github.com/local/invoice-intake",
        title: str = "Invoice Intake",
    ) -> None:
        if not api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is not set")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter attributes usage to these; harmless locally.
            "HTTP-Referer": referer,
            "X-Title": title,
        }

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        json_schema: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int = 8000,
        max_attempts: int = 3,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Ask OpenRouter to bill-report the call so cost is measured, not modelled.
            "usage": {"include": True},
        }
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "invoice", "strict": True, "schema": json_schema},
            }

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        f"{self._base_url}/chat/completions",
                        headers=self._headers,
                        json=payload,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = OpenRouterError(f"network error: {exc}", retryable=True)
                logger.warning(
                    "openrouter transport error (attempt %s/%s): %s", attempt, max_attempts, exc
                )
                if attempt < max_attempts:
                    await self._backoff(attempt)
                continue

            latency_ms = int((time.perf_counter() - started) * 1000)

            if response.status_code == 200:
                body = response.json()
                # OpenRouter reports upstream failures inside a 200 body rather
                # than as an HTTP status, so a gateway timeout arrives looking
                # like a successful response. Treating every in-band error as
                # final made one slow provider a permanently failed invoice.
                if "error" in body and body["error"]:
                    err = body["error"] if isinstance(body["error"], dict) else {}
                    code = err.get("code")
                    message = err.get("message", body["error"])
                    if code in RETRYABLE_STATUS and attempt < max_attempts:
                        logger.warning(
                            "openrouter returned %s in-band (attempt %s/%s): %s",
                            code, attempt, max_attempts, message,
                        )
                        last_error = OpenRouterError(
                            f"upstream {code}: {message}", status=code, retryable=True
                        )
                        await self._backoff(attempt)
                        continue
                    raise OpenRouterError(
                        f"model returned an error: {body['error']}", status=code
                    )
                choice = (body.get("choices") or [{}])[0]
                usage = body.get("usage") or {}
                return Completion(
                    text=(choice.get("message") or {}).get("content") or "",
                    model=body.get("model", model),
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    cost_usd=float(usage.get("cost") or 0.0),
                    latency_ms=latency_ms,
                    finish_reason=choice.get("finish_reason"),
                    raw=body,
                )

            detail = response.text[:400]
            # Some models reject strict json_schema. Degrade to plain JSON mode
            # rather than losing the model from the comparison entirely.
            if response.status_code == 400 and json_schema and "response_format" in detail:
                logger.info("%s rejected json_schema; falling back to JSON mode", model)
                payload.pop("response_format", None)
                payload["response_format"] = {"type": "json_object"}
                json_schema = None
                continue

            retryable = response.status_code in RETRYABLE_STATUS
            last_error = OpenRouterError(
                f"HTTP {response.status_code} from OpenRouter: {detail}",
                status=response.status_code,
                retryable=retryable,
            )
            if not retryable:
                raise last_error
            logger.warning(
                "openrouter %s (attempt %s/%s)", response.status_code, attempt, max_attempts
            )
            await self._backoff(attempt)

        raise last_error or OpenRouterError("exhausted retries")

    @staticmethod
    async def _backoff(attempt: int) -> None:
        """Wait before trying again, with jitter.

        Retrying instantly three times is barely a retry: a provider that is
        briefly overloaded is still overloaded a millisecond later, and a batch
        of uploads all retrying in lockstep makes it worse. Jitter spreads them.
        """
        delay = min(8.0, 0.75 * (2 ** (attempt - 1)))
        await asyncio.sleep(delay + random.uniform(0, 0.4))
