"""Unit tests for the MiniMax client + JSON coercion helper."""

from __future__ import annotations

import httpx
import pytest

from app.services.minimax.client import (
    MiniMaxClient,
    MiniMaxError,
    coerce_json_text,
)


def test_plain_json() -> None:
    obj = coerce_json_text('{"a": 1}')
    assert obj == {"a": 1}


def test_json_fenced_with_language_tag() -> None:
    raw = '```json\n{"a": 2}\n```'
    assert coerce_json_text(raw) == {"a": 2}


def test_json_fenced_without_language_tag() -> None:
    raw = '```\n{"a": 3}\n```'
    assert coerce_json_text(raw) == {"a": 3}


def test_garbage_raises() -> None:
    with pytest.raises(MiniMaxError):
        coerce_json_text("not json at all")


def test_client_supports_async_context_manager() -> None:
    """`async with MiniMaxClient(...) as c:` is used by the spike runner."""
    import asyncio

    async def run() -> None:
        async with MiniMaxClient() as client:
            assert isinstance(client._http, httpx.AsyncClient)  # noqa: SLF001
        # After exit, the owned client should be closed.
        assert client._http.is_closed  # noqa: SLF001

    asyncio.run(run())
