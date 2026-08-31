"""Validation + retry helper for MiniMax M3 semantic analysis.

Validates a raw model response against `SemanticAnalysisResult`, retrying
once with a repair prompt if validation fails (PROJECT_PLAN §14.4).

Never silently parses random prose: if both attempts fail, raises
`MiniMaxError` with the underlying validation details so the spike runner
records it as a model-side failure rather than masking the problem.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.models.semantic import SemanticAnalysisResult
from app.services.minimax.client import MiniMaxClient, MiniMaxError, coerce_json_text
from app.services.minimax.prompts import REPAIR_INSTRUCTION

logger = logging.getLogger(__name__)


def parse_semantic_result(raw: str) -> SemanticAnalysisResult:
    """Parse + validate one raw model response. No retry."""
    obj = coerce_json_text(raw)
    return SemanticAnalysisResult.model_validate(obj)


async def analyze_semantic_merge(
    client: MiniMaxClient,
    *,
    base_context: str,
    branch_a_change: str,
    branch_b_change: str,
    mechanical_diff: str,
) -> tuple[SemanticAnalysisResult, str]:
    """Call M3 and return (validated_result, raw_text).

    Retries once with the repair instruction if the first response fails
    schema validation. Returns the validated result or raises.

    v2.0.0: derives the reconstructed branch views and passes them to
    `build_user_payload` so M3 can apply the per-branch safety decision
    rule against the *full* remaining branch content.
    """
    # Imported here to avoid a top-level import cycle in tests.
    from app.services.minimax.branch_view import build_branch_view
    from app.services.minimax.prompts import build_user_payload

    user_payload = build_user_payload(
        base_context=base_context,
        branch_a_change=branch_a_change,
        branch_b_change=branch_b_change,
        mechanical_diff=mechanical_diff,
        branch_a_full=build_branch_view(base_context, branch_a_change),
        branch_b_full=build_branch_view(base_context, branch_b_change),
    )

    raw = await client.chat_json(system=_system_with_version(), user=user_payload)
    try:
        return parse_semantic_result(raw), raw
    except (ValidationError, MiniMaxError) as first_err:
        logger.warning(
            "minimax.first_attempt_invalid err=%s; retrying with repair prompt",
            first_err,
        )

    repair_user = user_payload + "\n\n---\n\n" + REPAIR_INSTRUCTION
    raw2 = await client.chat_json(system=_system_with_version(), user=repair_user)
    try:
        return parse_semantic_result(raw2), raw2
    except (ValidationError, MiniMaxError) as final_err:
        raise MiniMaxError(
            f"M3 failed to produce a schema-valid response after repair. Underlying: {final_err}"
        ) from final_err


def _system_with_version() -> str:
    from app.services.minimax.prompts import PROMPT_VERSION, SYSTEM_INTENT

    return f"{SYSTEM_INTENT}\n\n(prompt_version={PROMPT_VERSION})"
