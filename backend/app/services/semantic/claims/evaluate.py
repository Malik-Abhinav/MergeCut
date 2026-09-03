"""STEP 3 — Per-claim, per-branch preservation verdicts via M3.

For each important claim, the orchestrator calls M3 three
times (once per branch: A, B, combined). Each call:

  1. Hands M3 the claim's meaning + type + importance,
  2. Hands M3 the BASE evidence_regions + equivalents,
  3. Hands M3 the BRANCH's reconstructed content (BASE with
     the branch's Phase 3 edits applied, with DELETED /
     REPLACED / TRIMMED markers),
  4. Asks M3 to return preserved/degraded/broken + the
     surviving evidence spans.

M3's verdict is *final* for the per-claim status. M3 NEVER
decides the cross-edit interaction — that is `interact.derive_interaction`.

The orchestrator can batch multiple claims into a single M3
call when the user wants to save round-trips, but for the
v4.1.0 MVP we use one call per (claim, branch) pair. This
keeps the prompt small (M3 is more accurate on focused
prompts) and the per-claim evaluation easy to debug.

For tests, the M3 client is replaced with a `_FakeClient` that
returns canned `ClaimEvaluation` JSON.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from pydantic import ValidationError

from app.models.claims import (
    BaseClaim,
    ClaimEvaluation,
    ClaimEvaluationRequest,
    ClaimStatus,
)
from app.services.minimax.client import MiniMaxClient, MiniMaxError, coerce_json_text
from app.services.semantic.claims.prompts_claims import (
    EVALUATION_PROMPT_VERSION,
    EVALUATION_REPAIR_INSTRUCTION,
    EVALUATION_SYSTEM_INTENT,
    build_evaluation_user_payload,
)

logger = logging.getLogger(__name__)


def _system_with_version() -> str:
    return f"{EVALUATION_SYSTEM_INTENT}\n\n(prompt_version={EVALUATION_PROMPT_VERSION})"


def _parse_one(raw: str, claim_id: str) -> ClaimEvaluation:
    obj = coerce_json_text(raw)
    return ClaimEvaluation.model_validate({**obj, "claim_id": claim_id})


def evaluate_one_claim_in_branch(
    *,
    claim: BaseClaim,
    branch_name: str,
    branch_reconstructed_lines: list[str],
    client: MiniMaxClient,
) -> ClaimEvaluation:
    """Call M3 to evaluate ONE claim in ONE branch.

    `branch_reconstructed_lines` is the rendered
    `ReconstructedBranchContent.lines` for the branch
    (BASE with that branch's edit applied, with gap markers).
    The orchestrator's `analyze_claims()` builds this once per
    branch (it does not need to re-render per claim) and
    reuses the lines for every claim in that branch.

    One retry on validation failure.
    """
    req = ClaimEvaluationRequest(
        claim_id=claim.claim_id,
        meaning=claim.meaning,
        claim_type=claim.claim_type,
        importance=claim.importance,
        base_evidence_regions=list(claim.evidence_regions),
        base_equivalents=list(claim.equivalents),
        branch_name=branch_name,  # type: ignore[arg-type]
        branch_reconstructed_lines=list(branch_reconstructed_lines),
    )
    user_payload = build_evaluation_user_payload(req)
    raw = client.chat_json_sync(system=_system_with_version(), user=user_payload)
    try:
        return _parse_one(raw, claim.claim_id)
    except (ValidationError, MiniMaxError) as first_err:
        logger.warning(
            "evaluate_claim.first_attempt_invalid claim_id=%s err=%s; retrying",
            claim.claim_id,
            first_err,
        )
    repair_user = user_payload + "\n\n---\n\n" + EVALUATION_REPAIR_INSTRUCTION
    raw2 = client.chat_json_sync(system=_system_with_version(), user=repair_user)
    try:
        return _parse_one(raw2, claim.claim_id)
    except (ValidationError, MiniMaxError) as final_err:
        raise MiniMaxError(
            f"M3 failed to return a valid ClaimEvaluation for "
            f"claim_id={claim.claim_id} branch={branch_name} after repair: {final_err}"
        ) from final_err


def evaluate_all_claims(
    *,
    base_claims: Sequence[BaseClaim],
    branch_reconstructions: dict[str, list[BaseClaim]],
    branch_reconstructed_lines: dict[str, list[str]],
    client: MiniMaxClient,
) -> dict[str, dict[str, ClaimEvaluation]]:
    """Evaluate every important claim in every branch.

    `branch_reconstructions` is `{branch_name: [BaseClaim, ...]}`
    (the deterministically reconstructed claim list per branch).
    `branch_reconstructed_lines` is `{branch_name: [str, ...]}`
    (the rendered text for that branch's reconstructed
    content). Both must cover the same branch names.

    Returns `{branch_name: {claim_id: ClaimEvaluation}}`. The
    orchestrator then assembles a `BranchClaims` from each
    branch's evaluations.
    """
    out: dict[str, dict[str, ClaimEvaluation]] = {}
    for branch_name, claims in branch_reconstructions.items():
        lines = branch_reconstructed_lines[branch_name]
        per_branch: dict[str, ClaimEvaluation] = {}
        for claim in claims:
            ev = evaluate_one_claim_in_branch(
                claim=claim,
                branch_name=branch_name,
                branch_reconstructed_lines=lines,
                client=client,
            )
            per_branch[claim.claim_id] = ev
        out[branch_name] = per_branch
    return out


__all__ = [
    "evaluate_one_claim_in_branch",
    "evaluate_all_claims",
    "ClaimStatus",
]
