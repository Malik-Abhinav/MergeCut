"""STEP 5 — M3 explanation (prose only; does NOT control classification).

For each `ClaimInteraction` with `interaction != "none"`, the
orchestrator may call M3 once to generate a human-readable
explanation + recommended resolution. M3 is told the
classification is fixed and asked to write the prose.

This module is OPTIONAL in the orchestrator pipeline: a
missing explanation just sets `m3_explanation=None` and the
rest of the analysis is unaffected. The evaluation harness
always runs the explainer.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.models.claims import ClaimInteraction
from app.services.minimax.client import MiniMaxClient, MiniMaxError, coerce_json_text
from app.services.semantic.claims.prompts_claims import (
    EXPLANATION_SYSTEM_INTENT,
    build_explanation_user_payload,
)

logger = logging.getLogger(__name__)

EXPLANATION_PROMPT_VERSION = "4.1.0"


def _system_with_version() -> str:
    return f"{EXPLANATION_SYSTEM_INTENT}\n\n(prompt_version={EXPLANATION_PROMPT_VERSION})"


def _parse(raw: str) -> dict[str, str]:
    obj = coerce_json_text(raw)
    if not isinstance(obj, dict):
        raise MiniMaxError("explain response was not a dict")
    if "explanation" not in obj or not isinstance(obj["explanation"], str):
        raise MiniMaxError("explain response missing 'explanation' string")
    return {
        "explanation": obj["explanation"],
        "recommended_resolution": obj.get("recommended_resolution", ""),
    }


def explain_interaction(
    interaction: ClaimInteraction,
    client: MiniMaxClient,
) -> ClaimInteraction:
    """Call M3 to fill in `m3_explanation` + `m3_recommended_resolution`.

    Returns a NEW `ClaimInteraction` with the M3 prose filled
    in. The classification is unchanged. On failure, returns
    the original interaction (with M3 fields left None) and
    logs the error.
    """
    user_payload = build_explanation_user_payload(
        claim_id=interaction.claim_id,
        claim_meaning=interaction.claim_meaning,
        interaction=interaction.interaction.value,
        derivation_reason=interaction.derivation_reason,
        branch_a_status=interaction.branch_a_status.value,
        branch_b_status=interaction.branch_b_status.value,
        combined_status=interaction.combined_status.value,
    )
    try:
        raw = client.chat_json_sync(system=_system_with_version(), user=user_payload)
        parsed = _parse(raw)
    except (ValidationError, MiniMaxError, KeyError) as e:
        logger.warning(
            "explain_interaction.failed claim_id=%s err=%s; returning interaction without explanation",
            interaction.claim_id,
            e,
        )
        return interaction
    return interaction.model_copy(
        update={
            "m3_explanation": parsed["explanation"],
            "m3_recommended_resolution": parsed.get("recommended_resolution") or None,
        }
    )


__all__ = [
    "explain_interaction",
    "EXPLANATION_PROMPT_VERSION",
]
