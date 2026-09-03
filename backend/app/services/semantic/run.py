"""Phase 4 orchestrator: deterministic BASE/A/B alignment → M3 semantic verdict.

This is the only entry point Phase 5+ should call for end-to-end
semantic analysis. It does, in order:

  1. Phase 3 alignment: `align_branch_to_base(base, branch_a)`
     and `align_branch_to_base(base, branch_b)`.
  2. Context packaging: build the `SemanticContext` from the
     two alignment results.
  3. M3 call: send `SYSTEM_INTENT` + rendered user payload to
     `MiniMaxClient.chat_json`.
  4. Schema validation: parse the raw response into a
     `SemanticAnalysisV2`. On failure, retry once with the
     repair instruction. If the retry also fails, raise
     `MiniMaxError` (the same contract Phase 1 used).
  5. Legacy projection: populate `legacy_v1_compat` so v1
     consumers can still read the response.

Per AGENTS.md, this module uses MiniMax M3 through GMI Cloud
(per `Settings.minimax_m3_model`). It does not silently
substitute another model.

The orchestrator is intentionally thin: all the heavy work
lives in `alignment.run`, `semantic.context`,
`semantic.prompts_v2`, and the existing `minimax.client`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.models.media import VideoRepresentation
from app.models.semantic_v2 import (
    SemanticAnalysisV2,
    to_legacy_v1,
)
from app.services.alignment.run import align_branch_to_base
from app.services.minimax.client import MiniMaxClient, MiniMaxError, coerce_json_text
from app.services.semantic.context import (
    SemanticContext,
    build_semantic_context,
)
from app.services.semantic.prompts_v2 import (
    PROMPT_VERSION,
    REPAIR_INSTRUCTION,
    SYSTEM_INTENT,
    build_user_payload,
)

logger = logging.getLogger(__name__)


@dataclass
class AnalysisArtifacts:
    """All byproducts of one Phase 4 call.

    Useful for diagnostics and the Phase 5 evaluation harness:
    the orchestrator returns the validated `SemanticAnalysisV2`
    (the production result) alongside the alignment results
    and the raw M3 response so the caller can audit every step.
    """

    analysis: SemanticAnalysisV2
    branch_a_alignment_op_count: int
    branch_b_alignment_op_count: int
    candidate_pair_count: int
    raw_response: str
    retries: int
    prompt_version: str
    model: str
    context: SemanticContext
    notes: list[str] = field(default_factory=list)


def _system_with_version() -> str:
    return f"{SYSTEM_INTENT}\n\n(prompt_version={PROMPT_VERSION})"


def _parse_v2(raw: str) -> SemanticAnalysisV2:
    obj = coerce_json_text(raw)
    return SemanticAnalysisV2.model_validate(obj)


def analyze_merge(
    *,
    base: VideoRepresentation,
    branch_a: VideoRepresentation,
    branch_b: VideoRepresentation,
    client: MiniMaxClient,
    branch_a_name: str = "branch_a",
    branch_b_name: str = "branch_b",
) -> AnalysisArtifacts:
    """End-to-end Phase 4 analysis of one (BASE, A, B) triple.

    Args:
        base: Phase 2 `VideoRepresentation` of the original.
        branch_a: Phase 2 `VideoRepresentation` of branch A.
        branch_b: Phase 2 `VideoRepresentation` of branch B.
        client: a configured `MiniMaxClient` (or a mock with
            the same `chat_json` interface).
        branch_a_name: human-readable name for branch A in the
            alignment result.
        branch_b_name: human-readable name for branch B.

    Returns:
        `AnalysisArtifacts` containing the validated
        `SemanticAnalysisV2` plus diagnostic context.

    Raises:
        `MiniMaxError` if M3 fails schema validation twice or
        if the network call itself fails.
    """
    # 1. Phase 3 alignment for both branches.
    a_alignment = align_branch_to_base(base=base, branch=branch_a, branch_name=branch_a_name)
    b_alignment = align_branch_to_base(base=base, branch=branch_b, branch_name=branch_b_name)

    # 2. Context packaging.
    ctx = build_semantic_context(
        base=base,
        branch_a_alignment=a_alignment,
        branch_b_alignment=b_alignment,
    )

    # 3. M3 call.
    user_payload = build_user_payload(ctx)
    raw = client.chat_json_sync(
        system=_system_with_version(),
        user=user_payload,
    )

    # 4. Schema validation + one retry.
    retries = 0
    try:
        result = _parse_v2(raw)
    except (ValidationError, MiniMaxError) as first_err:
        logger.warning(
            "semantic.first_attempt_invalid err=%s; retrying with repair prompt",
            first_err,
        )
        retries = 1
        raw2 = client.chat_json_sync(
            system=_system_with_version(),
            user=user_payload + "\n\n---\n\n" + REPAIR_INSTRUCTION,
        )
        try:
            result = _parse_v2(raw2)
            raw = raw2
        except (ValidationError, MiniMaxError) as final_err:
            raise MiniMaxError(
                f"M3 failed to produce a schema-valid Phase 4 v2 response after repair. "
                f"Underlying: {final_err}"
            ) from final_err

    # 5. Legacy projection.
    result = result.model_copy(update={"legacy_v1_compat": to_legacy_v1(result)})

    return AnalysisArtifacts(
        analysis=result,
        branch_a_alignment_op_count=len(a_alignment.matches),
        branch_b_alignment_op_count=len(b_alignment.matches),
        candidate_pair_count=len(ctx.candidate_pairs),
        raw_response=raw,
        retries=retries,
        prompt_version=PROMPT_VERSION,
        model=client.model,
        context=ctx,
    )


__all__ = [
    "AnalysisArtifacts",
    "analyze_merge",
]
