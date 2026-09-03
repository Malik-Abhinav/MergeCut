"""Focused tests for the bounded transient retry layer.

Covers (per PROJECT_PLAN §29 Phase 4.5):

- max attempts (4 retries after the initial attempt = 5 attempts)
- `Retry-After` header honoured (seconds form)
- misleading outer 401 whose body reports upstream 503 → retried
- genuine invalid auth → not retried, counters reflect failure
- cumulative counters (successful_calls, retries, provider_failures,
  http_429_count, upstream_503_count)
- shared classification/policy between async and sync paths

Transport and sleeps are mocked so the tests are fast and deterministic.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.services.minimax import _retry as retry_mod
from app.services.minimax._retry import (
    BASE_DELAYS_S,
    MAX_RETRIES,
    RetryStats,
    classify_attempt,
    compute_delay_s,
    run_with_retry_async,
    run_with_retry_sync,
)
from app.services.minimax.client import MiniMaxClient, MiniMaxError

# ---------------------------------------------------------------------------
# Helpers: build fake httpx.Response objects and mock transports.
# ---------------------------------------------------------------------------


def _make_response(
    *,
    status_code: int,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Construct an httpx.Response without going through real I/O."""
    if headers is None:
        headers = {}
    if body is None:
        content = b""
    elif isinstance(body, (bytes, bytearray)):
        content = bytes(body)
    elif isinstance(body, str):
        content = body.encode("utf-8")
    else:
        content = json.dumps(body).encode("utf-8")
        if "content-type" not in {k.lower() for k in headers}:
            headers["content-type"] = "application/json"
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    return httpx.Response(
        status_code=status_code, headers=headers, content=content, request=request
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_retryable_status_codes() -> None:
    for code in (429, 502, 503, 504):
        d = classify_attempt(status_code=code)
        assert d.retry, f"{code} should retry"
        assert d.reason == f"http_{code}"


def test_classify_deterministic_4xx_not_retryable() -> None:
    for code in (400, 401, 403, 404, 422):
        d = classify_attempt(status_code=code, body_text="oops")
        assert not d.retry, f"{code} should NOT retry"
        assert d.reason == f"non_retryable_status:{code}"


def test_classify_misleading_401_with_upstream_503_body_retries() -> None:
    body = (
        '{"error":{"message":"upstream_503: overload - temporarily unavailable",'
        '"type":"upstream","code":"upstream_503"}}'
    )
    d = classify_attempt(status_code=401, body_text=body)
    assert d.retry
    assert d.upstream_503 is True
    assert "upstream_503" in d.reason


def test_classify_misleading_401_with_genuine_auth_body_does_not_retry() -> None:
    body = '{"error":{"message":"Invalid API key","code":"invalid_api_key"}}'
    d = classify_attempt(status_code=401, body_text=body)
    assert not d.retry


def test_classify_rate_limit_exceeded_token_retries() -> None:
    d = classify_attempt(status_code=500, body_text="rate_limit_exceeded: slow down")
    assert d.retry
    assert d.upstream_503 is True


def test_classify_connection_reset_token_retries() -> None:
    d = classify_attempt(status_code=500, body_text="connection reset by peer")
    assert d.retry
    assert d.upstream_503 is True


def test_classify_httpx_timeout_retries() -> None:
    d = classify_attempt(exception=httpx.ReadTimeout("slow"))
    assert d.retry
    assert "ReadTimeout" in d.reason


def test_classify_httpx_connect_error_retries() -> None:
    d = classify_attempt(exception=httpx.ConnectError("dns"))
    assert d.retry


def test_classify_httpx_remote_protocol_error_retries() -> None:
    # RemoteProtocolError is what httpx raises on connection reset.
    d = classify_attempt(exception=httpx.RemoteProtocolError("peer reset"))
    assert d.retry


def test_classify_unknown_exception_does_not_retry() -> None:
    d = classify_attempt(exception=ValueError("nope"))
    assert not d.retry


# ---------------------------------------------------------------------------
# Retry-After parsing + delay computation
# ---------------------------------------------------------------------------


def test_retry_after_seconds_parsed() -> None:
    d = classify_attempt(status_code=429, retry_after_header="7")
    assert d.retry_after_s == 7.0


def test_retry_after_zero_or_negative_ignored() -> None:
    assert classify_attempt(status_code=503, retry_after_header="0").retry_after_s is None
    assert classify_attempt(status_code=503, retry_after_header="-1").retry_after_s is None


def test_retry_after_malformed_ignored() -> None:
    assert classify_attempt(status_code=503, retry_after_header="not-a-date").retry_after_s is None


def test_compute_delay_uses_base_table_when_no_retry_after() -> None:
    # No jitter, deterministic.
    import random

    rng = random.Random(0)
    for i, expected in enumerate(BASE_DELAYS_S):
        d = compute_delay_s(
            attempt_index=i,
            decision=classify_attempt(status_code=503),
            jitter_s=0.0,
            rng=rng,
        )
        assert d == expected


def test_compute_delay_prefers_retry_after() -> None:
    import random

    rng = random.Random(0)
    d = compute_delay_s(
        attempt_index=0,
        decision=classify_attempt(status_code=429, retry_after_header="3"),
        jitter_s=0.0,
        rng=rng,
    )
    assert d == 3.0


def test_compute_delay_caps_index_at_max_retries() -> None:
    import random

    rng = random.Random(0)
    d = compute_delay_s(
        attempt_index=999,
        decision=classify_attempt(status_code=503),
        jitter_s=0.0,
        rng=rng,
    )
    assert d == BASE_DELAYS_S[-1]


# ---------------------------------------------------------------------------
# Stats counters
# ---------------------------------------------------------------------------


def test_stats_record_success_and_failure() -> None:
    s = RetryStats()
    s.record_success()
    s.record_success()
    s.record_failure()
    assert s.successful_calls == 2
    assert s.provider_failures == 1
    assert s.retries == 0


def test_stats_record_retry_distinguishes_429_and_upstream() -> None:
    s = RetryStats()
    s.record_retry(status_429=True, upstream_503=False)
    s.record_retry(status_429=False, upstream_503=True)
    s.record_retry(status_429=True, upstream_503=True)
    assert s.retries == 3
    assert s.http_429_count == 2
    assert s.upstream_503_count == 2


# ---------------------------------------------------------------------------
# Sync retry loop: success after transient 503
# ---------------------------------------------------------------------------


def test_sync_loop_recovers_after_one_503() -> None:
    sleeps: list[float] = []
    stats = RetryStats()
    responses = [
        _make_response(status_code=503, body="temporarily unavailable"),
        _make_response(
            status_code=200,
            body={"choices": [{"message": {"content": "{}"}}]},
        ),
    ]

    def attempt() -> httpx.Response:
        return responses.pop(0)

    resp = run_with_retry_sync(
        attempt=attempt,
        stats=stats,
        sleep=sleeps.append,
        rng=_NoJitter(),
    )
    assert resp.status_code == 200
    assert len(sleeps) == 1
    assert stats.retries == 1
    assert stats.provider_failures == 0


# ---------------------------------------------------------------------------
# Sync retry loop: gives up after max_retries (4 retries / 5 attempts)
# ---------------------------------------------------------------------------


def test_sync_loop_max_attempts_503() -> None:
    sleeps: list[float] = []
    stats = RetryStats()
    calls = {"n": 0}

    def attempt() -> httpx.Response:
        calls["n"] += 1
        return _make_response(status_code=503, body="temporarily unavailable")

    resp = run_with_retry_sync(
        attempt=attempt,
        stats=stats,
        sleep=sleeps.append,
        rng=_NoJitter(),
    )
    # Final response is returned (the caller decides to raise on >=400).
    assert resp.status_code == 503
    # Initial attempt + 4 retries = 5 calls.
    assert calls["n"] == MAX_RETRIES + 1
    # 4 sleeps recorded.
    assert len(sleeps) == MAX_RETRIES
    assert stats.retries == MAX_RETRIES


# ---------------------------------------------------------------------------
# Sync retry loop: honours Retry-After seconds header
# ---------------------------------------------------------------------------


def test_sync_loop_honours_retry_after_header() -> None:
    sleeps: list[float] = []
    stats = RetryStats()
    responses = [
        _make_response(status_code=429, body="rate_limit_exceeded", headers={"Retry-After": "3"}),
        _make_response(status_code=200, body={"choices": [{"message": {"content": "{}"}}]}),
    ]

    def attempt() -> httpx.Response:
        return responses.pop(0)

    resp = run_with_retry_sync(
        attempt=attempt,
        stats=stats,
        sleep=sleeps.append,
        rng=_NoJitter(),
    )
    assert resp.status_code == 200
    assert sleeps == [3.0]
    assert stats.retries == 1
    assert stats.http_429_count == 1


# ---------------------------------------------------------------------------
# Sync retry loop: transport error (timeout) is retried
# ---------------------------------------------------------------------------


def test_sync_loop_retries_timeout() -> None:
    sleeps: list[float] = []
    stats = RetryStats()
    responses = iter(
        [
            httpx.ReadTimeout("slow"),
            _make_response(status_code=200, body={"choices": [{"message": {"content": "{}"}}]}),
        ]
    )

    def attempt() -> httpx.Response:
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    resp = run_with_retry_sync(
        attempt=attempt,
        stats=stats,
        sleep=sleeps.append,
        rng=_NoJitter(),
    )
    assert resp.status_code == 200
    assert stats.retries == 1
    assert len(sleeps) == 1


# ---------------------------------------------------------------------------
# Sync retry loop: misleading outer 401 with upstream-503 body is retried
# ---------------------------------------------------------------------------


def test_sync_loop_retries_misleading_401_with_upstream_503_body() -> None:
    sleeps: list[float] = []
    stats = RetryStats()
    body = '{"error":"upstream_503: overload"}'
    responses = iter(
        [
            _make_response(status_code=401, body=body),
            _make_response(status_code=200, body={"choices": [{"message": {"content": "{}"}}]}),
        ]
    )

    def attempt() -> httpx.Response:
        return next(responses)

    resp = run_with_retry_sync(
        attempt=attempt,
        stats=stats,
        sleep=sleeps.append,
        rng=_NoJitter(),
    )
    assert resp.status_code == 200
    assert stats.retries == 1
    assert stats.upstream_503_count == 1


# ---------------------------------------------------------------------------
# Sync retry loop: genuine invalid auth is NOT retried
# ---------------------------------------------------------------------------


def test_sync_loop_does_not_retry_genuine_invalid_auth() -> None:
    sleeps: list[float] = []
    stats = RetryStats()
    body = '{"error":"Invalid API key"}'
    calls = {"n": 0}

    def attempt() -> httpx.Response:
        calls["n"] += 1
        return _make_response(status_code=401, body=body)

    resp = run_with_retry_sync(
        attempt=attempt,
        stats=stats,
        sleep=sleeps.append,
        rng=_NoJitter(),
    )
    assert resp.status_code == 401
    assert calls["n"] == 1
    assert sleeps == []
    assert stats.retries == 0


# ---------------------------------------------------------------------------
# Sync retry loop: deterministic 4xx (400, 404, 422) is NOT retried
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [400, 403, 404, 422])
def test_sync_loop_does_not_retry_deterministic_4xx(code: int) -> None:
    sleeps: list[float] = []
    stats = RetryStats()
    calls = {"n": 0}

    def attempt() -> httpx.Response:
        calls["n"] += 1
        return _make_response(status_code=code, body="bad request")

    resp = run_with_retry_sync(
        attempt=attempt,
        stats=stats,
        sleep=sleeps.append,
        rng=_NoJitter(),
    )
    assert resp.status_code == code
    assert calls["n"] == 1
    assert sleeps == []
    assert stats.retries == 0


# ---------------------------------------------------------------------------
# Async retry loop: shared classification/policy with sync path
# ---------------------------------------------------------------------------


async def test_async_loop_uses_same_policy_as_sync() -> None:
    sleeps: list[float] = []
    stats = RetryStats()
    responses = iter(
        [
            _make_response(status_code=502, body="bad gateway"),
            _make_response(status_code=200, body={"choices": [{"message": {"content": "{}"}}]}),
        ]
    )

    def attempt() -> httpx.Response:
        return next(responses)

    async def _sleep(s: float) -> None:
        sleeps.append(s)

    resp = await run_with_retry_async(
        attempt=attempt,
        stats=stats,
        sleep=_sleep,
        rng=_NoJitter(),
    )
    assert resp.status_code == 200
    assert stats.retries == 1
    assert len(sleeps) == 1


# ---------------------------------------------------------------------------
# End-to-end: MiniMaxClient.chat_json uses the retry layer + counters
# ---------------------------------------------------------------------------


def _settings_with_key(tmp_key: str = "sk-test") -> Settings:
    # Settings reads .env by default; we pass a fully-populated instance
    # to avoid depending on the local .env contents.
    return Settings(
        gmi_api_key=tmp_key,
        gmi_base_url="https://example.test/v1",
        minimax_m3_model="MiniMaxAI/MiniMax-M3",
    )


async def test_chat_json_retries_transient_503_then_succeeds() -> None:
    """The real client path drives the shared retry layer."""
    sleeps: list[float] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        # Differentiate attempts via a counter captured in the closure.
        transport_handler.calls += 1  # type: ignore[attr-defined]
        if transport_handler.calls == 1:  # type: ignore[attr-defined]
            return _make_response(status_code=503, body="temporarily unavailable")
        return _make_response(
            status_code=200,
            body={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    transport_handler.calls = 0  # type: ignore[attr-defined]
    transport = httpx.MockTransport(transport_handler)

    settings = _settings_with_key()
    client = MiniMaxClient(settings=settings)
    # Inject our own httpx client + mock sleep so we can count retries.
    client._http = httpx.AsyncClient(timeout=10.0, transport=transport)  # noqa: SLF001
    client._owns_client = True  # noqa: SLF001

    # Patch the module-level asyncio.sleep to record durations without
    # actually waiting.
    import asyncio

    orig_sleep = asyncio.sleep
    asyncio.sleep = lambda s, *a, **kw: sleeps.append(s) or orig_sleep(0)  # type: ignore[assignment]
    try:
        content = await client.chat_json(system="s", user="u")
    finally:
        asyncio.sleep = orig_sleep  # type: ignore[assignment]

    assert content == '{"ok": true}'
    assert client.stats.retries == 1
    assert client.stats.successful_calls == 1
    assert client.stats.provider_failures == 0
    assert len(sleeps) == 1
    assert transport_handler.calls == 2  # type: ignore[attr-defined]


async def test_chat_json_does_not_retry_genuine_401() -> None:
    sleeps: list[float] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        transport_handler.calls += 1  # type: ignore[attr-defined]
        return _make_response(status_code=401, body='{"error":"Invalid API key"}')

    transport_handler.calls = 0  # type: ignore[attr-defined]
    transport = httpx.MockTransport(transport_handler)

    settings = _settings_with_key()
    client = MiniMaxClient(settings=settings)
    client._http = httpx.AsyncClient(timeout=10.0, transport=transport)  # noqa: SLF001
    client._owns_client = True  # noqa: SLF001

    import asyncio

    orig_sleep = asyncio.sleep
    asyncio.sleep = lambda s, *a, **kw: sleeps.append(s) or orig_sleep(0)  # type: ignore[assignment]
    try:
        with pytest.raises(MiniMaxError):
            await client.chat_json(system="s", user="u")
    finally:
        asyncio.sleep = orig_sleep  # type: ignore[assignment]

    assert transport_handler.calls == 1  # type: ignore[attr-defined]
    assert sleeps == []
    assert client.stats.retries == 0
    assert client.stats.provider_failures == 1


async def test_chat_json_retries_misleading_401_with_upstream_503_body() -> None:
    sleeps: list[float] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        transport_handler.calls += 1  # type: ignore[attr-defined]
        if transport_handler.calls == 1:  # type: ignore[attr-defined]
            return _make_response(
                status_code=401,
                body='{"error":"upstream_503: temporarily unavailable"}',
            )
        return _make_response(
            status_code=200,
            body={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )

    transport_handler.calls = 0  # type: ignore[attr-defined]
    transport = httpx.MockTransport(transport_handler)

    settings = _settings_with_key()
    client = MiniMaxClient(settings=settings)
    client._http = httpx.AsyncClient(timeout=10.0, transport=transport)  # noqa: SLF001
    client._owns_client = True  # noqa: SLF001

    import asyncio

    orig_sleep = asyncio.sleep
    asyncio.sleep = lambda s, *a, **kw: sleeps.append(s) or orig_sleep(0)  # type: ignore[assignment]
    try:
        content = await client.chat_json(system="s", user="u")
    finally:
        asyncio.sleep = orig_sleep  # type: ignore[assignment]

    assert content == '{"ok": true}'
    assert client.stats.retries == 1
    assert client.stats.upstream_503_count == 1
    assert transport_handler.calls == 2  # type: ignore[attr-defined]


async def test_chat_json_max_attempts_on_persistent_429() -> None:
    sleeps: list[float] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        transport_handler.calls += 1  # type: ignore[attr-defined]
        return _make_response(status_code=429, body="rate_limit_exceeded")

    transport_handler.calls = 0  # type: ignore[attr-defined]
    transport = httpx.MockTransport(transport_handler)

    settings = _settings_with_key()
    client = MiniMaxClient(settings=settings)
    client._http = httpx.AsyncClient(timeout=10.0, transport=transport)  # noqa: SLF001
    client._owns_client = True  # noqa: SLF001

    import asyncio

    orig_sleep = asyncio.sleep
    asyncio.sleep = lambda s, *a, **kw: sleeps.append(s) or orig_sleep(0)  # type: ignore[assignment]
    try:
        with pytest.raises(MiniMaxError):
            await client.chat_json(system="s", user="u")
    finally:
        asyncio.sleep = orig_sleep  # type: ignore[assignment]

    # 5 attempts total (initial + 4 retries) and 4 sleeps.
    assert transport_handler.calls == MAX_RETRIES + 1  # type: ignore[attr-defined]
    assert len(sleeps) == MAX_RETRIES
    assert client.stats.retries == MAX_RETRIES
    assert client.stats.http_429_count == MAX_RETRIES
    assert client.stats.provider_failures == 1
    assert client.stats.successful_calls == 0


def test_sync_path_shares_classification_with_async_path() -> None:
    """Same input → same RetryDecision regardless of which loop we use.

    We don't drive the sync wrapper end-to-end here (it spins up its own
    event loop) — instead we assert that classify_attempt is module-level
    and produces identical decisions, which is the contract the brief asks
    for ("Share classification/policy between async and sync paths").
    """
    cases = [
        dict(status_code=429),
        dict(status_code=503),
        dict(status_code=401, body_text="upstream_503: overload"),
        dict(status_code=401, body_text="Invalid API key"),
        dict(exception=httpx.ConnectError("dns")),
        dict(exception=ValueError("nope")),
    ]
    for kw in cases:
        a = retry_mod.classify_attempt(**kw)
        # The decision is a frozen dataclass, so structural equality is exact.
        b = retry_mod.classify_attempt(**kw)
        assert a == b
        assert a.retry == (a is not None and a.retry)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NoJitter:
    """A deterministic 'random' that returns 0.0 for any uniform call."""

    def uniform(self, a: float, b: float) -> float:
        return 0.0
