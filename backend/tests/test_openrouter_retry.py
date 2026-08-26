"""A slow provider must not become a permanently failed invoice.

OpenRouter reports upstream trouble inside a 200 body rather than as an HTTP
status, so a gateway timeout arrives looking like a successful response. Treating
every in-band error as final stranded an invoice on one bad minute upstream.
"""

import pytest

from app.clients.openrouter import RETRYABLE_STATUS, OpenRouterClient, OpenRouterError


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def ok_body(text: str = '{"invoice_number": "X"}') -> dict:
    return {
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0},
        "model": "test/model",
    }


@pytest.fixture
def client():
    return OpenRouterClient("sk-test")


def patch_transport(monkeypatch, responses: list):
    """Feed a scripted sequence of responses, and never actually sleep."""
    calls = {"n": 0}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            r = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            return r

    monkeypatch.setattr("app.clients.openrouter.httpx.AsyncClient", lambda **kw: FakeClient())

    async def no_sleep(_):
        return None

    monkeypatch.setattr(OpenRouterClient, "_backoff", staticmethod(no_sleep))
    return calls


async def test_an_in_band_gateway_timeout_is_retried(monkeypatch, client):
    calls = patch_transport(monkeypatch, [
        FakeResponse({"error": {"code": 504, "message": "The operation was aborted"}}),
        FakeResponse(ok_body()),
    ])
    result = await client.complete(model="m", messages=[])
    assert result.text == '{"invoice_number": "X"}'
    assert calls["n"] == 2, "should have tried again rather than giving up"


@pytest.mark.parametrize("code", sorted(RETRYABLE_STATUS))
async def test_every_upstream_code_we_call_transient_is_retried(monkeypatch, client, code):
    calls = patch_transport(monkeypatch, [
        FakeResponse({"error": {"code": code, "message": "busy"}}),
        FakeResponse(ok_body()),
    ])
    await client.complete(model="m", messages=[])
    assert calls["n"] == 2, code


async def test_a_real_model_error_is_not_retried(monkeypatch, client):
    """A malformed request will fail identically however many times we send it."""
    calls = patch_transport(monkeypatch, [
        FakeResponse({"error": {"code": 400, "message": "bad request"}}),
    ])
    with pytest.raises(OpenRouterError):
        await client.complete(model="m", messages=[])
    assert calls["n"] == 1


async def test_giving_up_reports_the_upstream_reason(monkeypatch, client):
    calls = patch_transport(monkeypatch, [
        FakeResponse({"error": {"code": 503, "message": "no capacity"}}),
    ])
    with pytest.raises(OpenRouterError) as exc:
        await client.complete(model="m", messages=[], max_attempts=3)
    assert "503" in str(exc.value) or "no capacity" in str(exc.value)
    assert calls["n"] == 3
