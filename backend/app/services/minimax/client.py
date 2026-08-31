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
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings

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
        """
        if not self._settings.gmi_api_key:
            raise MiniMaxError(
                "GMI_API_KEY is not configured. "
                "Set it in .env (see .env.example) before live calls."
            )

        url = self._settings.gmi_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.gmi_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Encourage strict JSON; some providers honor this.
            "response_format": {"type": "json_object"},
        }

        t0 = time.monotonic()
        try:
            resp = await self._http.post(url, headers=headers, json=payload)
        except httpx.HTTPError as e:
            raise MiniMaxError(f"HTTP error talking to GMI Cloud: {e!s}") from e

        if resp.status_code >= 400:
            # Surface the upstream body verbatim — it usually tells us what
            # the model identifier or auth is wrong.
            raise MiniMaxError(f"GMI Cloud {resp.status_code}: {resp.text[:500]}")

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise MiniMaxError(f"Unexpected GMI response shape: {data!r}") from e
        finally:
            logger.debug(
                "minimax.chat_json model=%s latency=%.2fs",
                self._model,
                time.monotonic() - t0,
            )

    # ------------------------------------------------------------------
    # Synchronous helper (used by the spike CLI; tests can mock the async one).
    # ------------------------------------------------------------------
    def chat_json_sync(self, **kwargs: Any) -> str:
        import asyncio

        return asyncio.run(self.chat_json(**kwargs))


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
