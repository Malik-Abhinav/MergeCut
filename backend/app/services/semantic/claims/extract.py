"""STEP 1 — Extract BASE claims from a `VideoRepresentation`.

Calls M3 once with the BASE shot timeline. Validates the
response against `BaseClaim` (one retry on failure).

For tests, the M3 client is replaced with a `_FakeClient`
that returns canned JSON. The fixtures live in
`tests.fixtures.claims_fixtures`.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.models.claims import BaseClaim
from app.models.media import VideoRepresentation
from app.services.minimax.client import MiniMaxClient, MiniMaxError, coerce_json_text
from app.services.semantic.claims.prompts_claims import (
    EVALUATION_SYSTEM_INTENT,
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_REPAIR_INSTRUCTION,
    EXTRACTION_SYSTEM_INTENT,
    build_extraction_user_payload,
)

logger = logging.getLogger(__name__)


def _system_with_version() -> str:
    return f"{EXTRACTION_SYSTEM_INTENT}\n\n(prompt_version={EXTRACTION_PROMPT_VERSION})"


def _shot_lines(rep: VideoRepresentation) -> list[tuple[int, str, float, float]]:
    return [(i, shot.transcript, shot.start, shot.end) for i, shot in enumerate(rep.shots)]


def extract_base_claims(
    base: VideoRepresentation,
    client: MiniMaxClient,
) -> list[BaseClaim]:
    """Call M3 to extract the BASE claim list. One retry on failure.

    Returns a list of `BaseClaim`. Raises `MiniMaxError` if both
    attempts fail validation.
    """
    user_payload = build_extraction_user_payload(
        video_id=base.video_id, shot_lines=_shot_lines(base)
    )
    raw = client.chat_json_sync(system=_system_with_version(), user=user_payload)
    try:
        return _parse_claims(raw)
    except (ValidationError, MiniMaxError, KeyError) as first_err:
        logger.warning(
            "extract_base_claims.first_attempt_invalid err=%s; retrying with repair prompt",
            first_err,
        )
    repair_user = user_payload + "\n\n---\n\n" + EXTRACTION_REPAIR_INSTRUCTION
    raw2 = client.chat_json_sync(system=_system_with_version(), user=repair_user)
    try:
        return _parse_claims(raw2)
    except (ValidationError, MiniMaxError, KeyError) as final_err:
        raise MiniMaxError(
            f"M3 failed to return a valid claim list after repair: {final_err}"
        ) from final_err


def _parse_claims(raw: str) -> list[BaseClaim]:
    obj: dict[str, Any] = coerce_json_text(raw)
    if "claims" not in obj or not isinstance(obj["claims"], list):
        raise MiniMaxError(f"missing 'claims' list in extraction response: {list(obj.keys())}")
    return [BaseClaim.model_validate(c) for c in obj["claims"]]


__all__ = [
    "extract_base_claims",
    "EVALUATION_SYSTEM_INTENT",  # re-exported for tests
]
