"""MiniMax integration: client + prompts + schemas.

Public surface for the Phase 1 spike:

    from app.services.minimax import MiniMaxClient, analyze_semantic_merge
"""

from __future__ import annotations

from app.services.minimax.client import (
    CompletionRecord,
    MiniMaxClient,
    MiniMaxError,
    coerce_json_text,
)
from app.services.minimax.prompts import (
    PROMPT_VERSION,
    SYSTEM_INTENT,
    build_user_payload,
    build_user_payload_for_fixture,
)
from app.services.minimax.schemas import (
    analyze_semantic_merge,
    parse_semantic_result,
)

__all__ = [
    "MiniMaxClient",
    "MiniMaxError",
    "CompletionRecord",
    "coerce_json_text",
    "PROMPT_VERSION",
    "SYSTEM_INTENT",
    "build_user_payload",
    "build_user_payload_for_fixture",
    "analyze_semantic_merge",
    "parse_semantic_result",
]
