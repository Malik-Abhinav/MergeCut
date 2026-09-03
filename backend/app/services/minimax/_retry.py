"""Bounded transient retry layer for the MiniMax / GMI Cloud client.

This module is intentionally tiny and self-contained so the same
classification and policy can be shared by both the async
(`MiniMaxClient.chat_json`) and the sync (`MiniMaxClient.chat_json_sync`)
HTTP paths in `client.py`.

Scope (per PROJECT_PLAN §29 Phase 4.5):

- Maximum 4 retries *after* the initial attempt (5 attempts total).
- Retryable on:
    * HTTP 429, 502, 503, 504
    * `httpx.TimeoutException`, `httpx.ConnectError`,
      `httpx.RemoteProtocolError` (covers connection reset),
      `httpx.TransportError` (catch-all)
    * Misleading outer status whose JSON / text body reports an
      upstream 503, "overload", "temporarily unavailable",
      `rate_limit_exceeded`, or "connection reset".
- NOT retryable on:
    * genuine invalid auth (e.g. outer 401 with no upstream-503 marker)
    * deterministic other 4xx (400, 403, 404, 422, ...)
    * malformed response shape / JSON / schema failures
- Delays: ~2, 5, 10, 20 seconds plus a small patchable jitter.
  Respect `Retry-After` (seconds or HTTP-date) when the server
  provides one.
- Cumulative stats: `successful_calls`, `retries`, `provider_failures`,
  `http_429_count`, `upstream_503_count`.

Everything here is pure stdlib + httpx; no new dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

# --- Constants -----------------------------------------------------------

MAX_RETRIES: int = 4
BASE_DELAYS_S: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0)
DEFAULT_JITTER_S: float = 0.25

# Outer HTTP statuses we always treat as transient.
RETRY_STATUS_CODES: frozenset[int] = frozenset({429, 502, 503, 504})

# Substrings / tokens we look for in the upstream body to detect a
# "misleading outer status" situation (provider returns e.g. 401 but
# its body is clearly about an upstream 503 / overload).
UPSTREAM_503_TOKENS: tuple[str, ...] = (
    "upstream_503",
    "upstream 503",
    "overload",
    "overloaded",
    "temporarily unavailable",
    "temporary unavailable",
    "rate_limit_exceeded",
    "rate limit exceeded",
    "rate-limit exceeded",
    "connection reset",
    "connection_reset",
)

# Tokens that mean "this is genuinely an auth error, do not retry".
AUTH_FAILURE_TOKENS: tuple[str, ...] = (
    "invalid api key",
    "invalid_api_key",
    "authentication invalid",
    "unauthorized",
    "401",
)


# --- Stats ---------------------------------------------------------------


@dataclass
class RetryStats:
    """Cumulative call counters — safe to share across paths/threads."""

    successful_calls: int = 0
    retries: int = 0
    provider_failures: int = 0
    http_429_count: int = 0
    upstream_503_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_success(self) -> None:
        with self._lock:
            self.successful_calls += 1

    def record_retry(self, *, status_429: bool = False, upstream_503: bool = False) -> None:
        with self._lock:
            self.retries += 1
            if status_429:
                self.http_429_count += 1
            if upstream_503:
                self.upstream_503_count += 1

    def record_failure(self) -> None:
        with self._lock:
            self.provider_failures += 1


# --- Classification ------------------------------------------------------


@dataclass(frozen=True)
class RetryDecision:
    """Result of classifying one attempt's outcome."""

    retry: bool
    reason: str
    retry_after_s: float | None = None
    upstream_503: bool = False
    http_429: bool = False


def _looks_like_upstream_503(body_text: str) -> bool:
    """Return True if the body text matches one of our upstream-503 tokens."""
    if not body_text:
        return False
    lower = body_text.lower()
    return any(tok in lower for tok in UPSTREAM_503_TOKENS)


def _looks_like_auth_failure(body_text: str) -> bool:
    if not body_text:
        return False
    lower = body_text.lower()
    return any(tok in lower for tok in AUTH_FAILURE_TOKENS)


def _parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Parse a `Retry-After` header value into seconds.

    Returns None if the header is absent, malformed, or non-positive.
    """
    if not value:
        return None
    value = value.strip()
    # Seconds form.
    try:
        seconds = float(value)
    except ValueError:
        # HTTP-date form.
        try:
            target = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if target is None:
            return None
        ref_now = now if now is not None else time.time()
        seconds = target.timestamp() - ref_now
    if seconds <= 0:
        return None
    return seconds


def classify_attempt(
    *,
    status_code: int | None = None,
    body_text: str = "",
    exception: BaseException | None = None,
    retry_after_header: str | None = None,
) -> RetryDecision:
    """Decide whether one attempt's outcome is retryable.

    Exactly one of `status_code` or `exception` should be supplied.
    `status_code` is None when the transport raised before getting a
    response; `exception` is None on a clean HTTP response.
    """
    # ---- Transport-level errors ----
    if exception is not None:
        if isinstance(
            exception,
            (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                httpx.TransportError,
            ),
        ):
            # RemoteProtocolError covers connection-reset cases.
            return RetryDecision(
                retry=True,
                reason=f"transport_error:{type(exception).__name__}",
                retry_after_s=_parse_retry_after(retry_after_header),
            )
        # Anything else: not retryable.
        return RetryDecision(retry=False, reason=f"non_retryable_error:{type(exception).__name__}")

    # ---- HTTP response ----
    assert status_code is not None  # invariant: if no exception, we got a status
    # Explicitly retryable statuses.
    if status_code in RETRY_STATUS_CODES:
        return RetryDecision(
            retry=True,
            reason=f"http_{status_code}",
            retry_after_s=_parse_retry_after(retry_after_header),
            http_429=(status_code == 429),
        )
    # Misleading outer status whose body reports an upstream 503 / overload.
    body = body_text or ""
    if _looks_like_upstream_503(body) and not _looks_like_auth_failure(body):
        return RetryDecision(
            retry=True,
            reason=f"upstream_503_in_body:{status_code}",
            retry_after_s=_parse_retry_after(retry_after_header),
            upstream_503=True,
        )
    # Otherwise: do not retry (genuine 4xx, 2xx handled by caller, 5xx
    # other than 502/503/504 is treated as deterministic for now).
    return RetryDecision(retry=False, reason=f"non_retryable_status:{status_code}")


# --- Delay computation ---------------------------------------------------


def compute_delay_s(
    *,
    attempt_index: int,
    decision: RetryDecision,
    jitter_s: float = DEFAULT_JITTER_S,
    rng: random.Random | None = None,
) -> float:
    """Compute the sleep before the next attempt.

    `attempt_index` is 0-based and refers to the attempt that just
    failed (0 = initial attempt). We cap at MAX_RETRIES - 1 so a
    misconfigured caller cannot index past our base delay table.
    """
    rng = rng or random.Random()
    if decision.retry_after_s is not None:
        delay = decision.retry_after_s
    else:
        idx = min(max(attempt_index, 0), MAX_RETRIES - 1)
        delay = BASE_DELAYS_S[idx]
    if jitter_s > 0:
        delay += rng.uniform(0.0, jitter_s)
    return max(delay, 0.0)


# --- Core retry loop -----------------------------------------------------


class RetryError(RuntimeError):
    """Raised when we run out of retries on a still-failing condition."""


def _sleep_sync(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


async def _sleep_async(seconds: float) -> None:
    if seconds > 0:
        await asyncio.sleep(seconds)


def run_with_retry_sync(
    *,
    attempt: Callable[[], httpx.Response],
    stats: RetryStats,
    sleep: Callable[[float], None] | None = None,
    rng: random.Random | None = None,
    jitter_s: float = DEFAULT_JITTER_S,
    max_retries: int = MAX_RETRIES,
) -> httpx.Response:
    """Drive a sync httpx POST with bounded retry. Returns the final response.

    Raises `MiniMaxError`-equivalent upstream errors after retries are
    exhausted; transport-level non-retryable errors are surfaced to the
    caller as the original exception. To keep this module free of
    circular imports, the caller's response-handling code decides what
    to raise on a non-2xx final response.
    """
    sleep = sleep or _sleep_sync
    rng = rng or random.Random()
    last_status: int | None = None
    last_body: str = ""
    for i in range(max_retries + 1):
        try:
            resp = attempt()
        except httpx.HTTPError as e:
            decision = classify_attempt(exception=e)
            if not decision.retry or i >= max_retries:
                if not decision.retry:
                    stats.record_failure()
                raise
            stats.record_retry(status_429=False, upstream_503=False)
            logger.info(
                "minimax.retry attempt=%d/4 reason=%s status=%s",
                i + 1,
                decision.reason,
                last_status,
            )
            sleep(compute_delay_s(attempt_index=i, decision=decision, jitter_s=jitter_s, rng=rng))
            continue
        # Got a response.
        last_status = resp.status_code
        last_body = resp.text or ""
        decision = classify_attempt(
            status_code=resp.status_code,
            body_text=last_body,
            retry_after_header=resp.headers.get("Retry-After"),
        )
        if not decision.retry:
            return resp
        if i >= max_retries:
            return resp
        stats.record_retry(status_429=decision.http_429, upstream_503=decision.upstream_503)
        logger.info(
            "minimax.retry attempt=%d/4 reason=%s status=%d",
            i + 1,
            decision.reason,
            resp.status_code,
        )
        sleep(compute_delay_s(attempt_index=i, decision=decision, jitter_s=jitter_s, rng=rng))
    # Defensive: loop always returns/raises; explicit for type-checkers.
    raise RetryError("retry loop exited without resolution")


async def run_with_retry_async(
    *,
    attempt: Callable[[], httpx.Response | Awaitable[httpx.Response]],
    stats: RetryStats,
    sleep: Callable[[float], Any] | None = None,
    rng: random.Random | None = None,
    jitter_s: float = DEFAULT_JITTER_S,
    max_retries: int = MAX_RETRIES,
) -> httpx.Response:
    """Async variant of `run_with_retry_sync`.

    `attempt` may be sync or async. We always `await` the result so
    callers can write either `lambda: client.post(...)` or
    `async lambda: await client.post(...)` uniformly.
    """
    sleep = sleep or _sleep_async
    rng = rng or random.Random()
    for i in range(max_retries + 1):
        try:
            resp_maybe = attempt()
            if asyncio.iscoroutine(resp_maybe):
                resp = await resp_maybe
            else:
                resp = resp_maybe
        except httpx.HTTPError as e:
            decision = classify_attempt(exception=e)
            if not decision.retry or i >= max_retries:
                if not decision.retry:
                    stats.record_failure()
                raise
            stats.record_retry(status_429=False, upstream_503=False)
            logger.info(
                "minimax.retry attempt=%d/4 reason=%s",
                i + 1,
                decision.reason,
            )
            await sleep(
                compute_delay_s(attempt_index=i, decision=decision, jitter_s=jitter_s, rng=rng)
            )
            continue
        decision = classify_attempt(
            status_code=resp.status_code,
            body_text=resp.text or "",
            retry_after_header=resp.headers.get("Retry-After"),
        )
        if not decision.retry:
            return resp
        if i >= max_retries:
            return resp
        stats.record_retry(status_429=decision.http_429, upstream_503=decision.upstream_503)
        logger.info(
            "minimax.retry attempt=%d/4 reason=%s status=%d",
            i + 1,
            decision.reason,
            resp.status_code,
        )
        await sleep(compute_delay_s(attempt_index=i, decision=decision, jitter_s=jitter_s, rng=rng))
    raise RetryError("retry loop exited without resolution")


__all__ = [
    "MAX_RETRIES",
    "BASE_DELAYS_S",
    "DEFAULT_JITTER_S",
    "RETRY_STATUS_CODES",
    "UPSTREAM_503_TOKENS",
    "AUTH_FAILURE_TOKENS",
    "RetryStats",
    "RetryDecision",
    "RetryError",
    "classify_attempt",
    "compute_delay_s",
    "run_with_retry_sync",
    "run_with_retry_async",
]
