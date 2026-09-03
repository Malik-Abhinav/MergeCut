"""MiniMax M3 client abstraction for GMI Cloud.

This module is the *only* place that talks to GMI Cloud directly. All other
modules go through `MiniMaxClient` so that:

1. Provider/model identifiers stay in one place (AGENTS.md rule 7).
2. Tests can swap in a recorded fixture client without monkey-patching httpx.
3. We can swap chat ↔ multimodal cleanly later (Phase 2+).

The client is intentionally minimal for Phase 1: it sends a chat-completion
request to the OpenAI-compatible endpoint exposed by GMI Cloud and returns
the raw assistant message. Validation against the Pydantic schema happens
upstream in `parse_semantic_result` (see `backend/app/services/minimax/schemas.py`).

Phase 4.5 added a bounded transient-retry layer (see `_retry.py`). The
classification and policy are shared between the async and sync paths
so a flaky upstream causes the same behavior in spike and live runs.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.services.minimax._retry import (
    RetryStats,
    run_with_retry_async,
)

logger = logging.getLogger(__name__)


class MiniMaxError(RuntimeError):
    """Raised for any MiniMax / GMI Cloud transport or auth failure."""


@dataclass(frozen=True)
class CompletionRecord:
    """One observed model call — used by the spike runner for reproducibility."""

    project: str
    model: str
    prompt_version: str
    request_ts: float
    latency_s: float
    retries: int
    raw_response: str
    parsed: dict[str, Any] | None = None


class MiniMaxClient:
    """Thin async wrapper around the GMI Cloud OpenAI-compatible endpoint."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        model: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._model = model or self._settings.minimax_m3_model
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=self._settings.request_timeout_s)
        self.stats: RetryStats = RetryStats()

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._settings.gmi_base_url

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> MiniMaxClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Request building (shared by async + sync paths).
    # ------------------------------------------------------------------
    def _build_request(self, kwargs: dict[str, Any]) -> tuple[str, dict[str, str], dict[str, Any]]:
        url = self._settings.gmi_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.gmi_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": kwargs.get("temperature", 0.0),
            "max_tokens": kwargs.get("max_tokens", 2048),
            "messages": [
                {"role": "system", "content": kwargs["system"]},
                {"role": "user", "content": kwargs["user"]},
            ],
            # Encourage strict JSON; some providers honor this.
            "response_format": {"type": "json_object"},
        }
        return url, headers, payload

    def _extract_content(self, data: Any) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise MiniMaxError(f"Unexpected GMI response shape: {data!r}") from e

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> str:
        """Send a chat completion and return the raw assistant text.

        The endpoint is the OpenAI-compatible `/chat/completions` route.
        Authentication is via `Authorization: Bearer ${GMI_API_KEY}`.

        Transient upstream failures (429 / 502 / 503 / 504, transport
        errors, misleading outer statuses whose body reports upstream
        503 / overload / rate_limit_exceeded / connection reset) are
        retried with bounded backoff. Auth failures and deterministic
        4xx are surfaced immediately. See `_retry.py` for the policy.
        """
        if not self._settings.gmi_api_key:
            raise MiniMaxError(
                "GMI_API_KEY is not configured. "
                "Set it in .env (see .env.example) before live calls."
            )

        url, headers, payload = self._build_request(
            {"system": system, "user": user, "temperature": temperature, "max_tokens": max_tokens}
        )

        async def _attempt() -> httpx.Response:
            return await self._http.post(url, headers=headers, json=payload)

        t0 = time.monotonic()
        try:
            resp = await run_with_retry_async(attempt=_attempt, stats=self.stats)
        except httpx.HTTPError as e:
            self.stats.record_failure()
            raise MiniMaxError(f"HTTP error talking to GMI Cloud: {e!s}") from e

        if resp.status_code >= 400:
            self.stats.record_failure()
            logger.warning(
                "minimax.chat_json final_status=%d model=%s", resp.status_code, self._model
            )
            raise MiniMaxError(f"GMI Cloud {resp.status_code}: {resp.text[:500]}")

        data = resp.json()
        try:
            content = self._extract_content(data)
        except MiniMaxError:
            self.stats.record_failure()
            raise
        self.stats.record_success()
        logger.debug(
            "minimax.chat_json model=%s latency=%.2fs",
            self._model,
            time.monotonic() - t0,
        )
        return content

    # ------------------------------------------------------------------
    # Synchronous helper (used by the spike CLI; tests can mock the async one).
    # ------------------------------------------------------------------
    def chat_json_sync(self, **kwargs: Any) -> str:
        """Synchronous wrapper.

        The async client uses a long-lived `httpx.AsyncClient`
        which is bound to the event loop that first used it.
        Calling `asyncio.run` per call creates a new event loop
        each time, which would fail with "Event loop is closed"
        on the second call. We work around this by closing and
        re-creating the underlying httpx client inside the
        short-lived event loop.
        """
        if not self._settings.gmi_api_key:
            raise MiniMaxError(
                "GMI_API_KEY is not configured. "
                "Set it in .env (see .env.example) before live calls."
            )

        url, headers, payload = self._build_request(kwargs)

        import asyncio

        import httpx as _httpx

        async def _do_call() -> str:
            async with _httpx.AsyncClient(timeout=self._settings.request_timeout_s) as http:

                async def _attempt() -> httpx.Response:
                    return await http.post(url, headers=headers, json=payload)

                t0 = time.monotonic()
                try:
                    resp = await run_with_retry_async(attempt=_attempt, stats=self.stats)
                except httpx.HTTPError as e:
                    self.stats.record_failure()
                    raise MiniMaxError(f"HTTP error talking to GMI Cloud: {e!s}") from e

                if resp.status_code >= 400:
                    self.stats.record_failure()
                    logger.warning(
                        "minimax.chat_json_sync final_status=%d model=%s",
                        resp.status_code,
                        self._model,
                    )
                    raise MiniMaxError(f"GMI Cloud {resp.status_code}: {resp.text[:500]}")

                data = resp.json()
                try:
                    content = self._extract_content(data)
                except MiniMaxError:
                    self.stats.record_failure()
                    raise
                self.stats.record_success()
                logger.debug(
                    "minimax.chat_json_sync model=%s latency=%.2fs",
                    self._model,
                    time.monotonic() - t0,
                )
                return content

        return asyncio.run(_do_call())


# ---------------------------------------------------------------------------
# Helpers used by the spike runner.
# ---------------------------------------------------------------------------


def coerce_json_text(raw: str) -> dict[str, Any]:
    """Tolerantly parse model output into a JSON dict.

    Some providers wrap JSON in ```json ... ``` fences; strip them first.
    Anything else is forwarded to `json.loads`.
    """
    text = raw.strip()
    if text.startswith("```"):
        # Remove the first ``` fence (with optional language tag) and the last one.
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise MiniMaxError(f"Model returned non-JSON: {e}: {raw[:300]!r}") from e
