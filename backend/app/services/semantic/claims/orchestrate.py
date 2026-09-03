"""Top-level Phase 4 claim-centric orchestrator.

Pipeline:

  1. Phase 3 alignment: `align_branch_to_base(base, branch_a)`
     and `align_branch_to_base(base, branch_b)`.
  2. Build the actual-content reconstruction for BASE,
     branch_a, branch_b, and combined (the deterministic
     ``ReconstructedActualContent`` from ``represent.py``).
     This is the verbatim text the viewer hears in each view
     (no markers, no BASE-leakage into deleted / replaced
     positions). M3 reads these lines in STEP 3 to verdict
     per-claim preservation.
  3. Extract BASE claims (M3 call, STEP 1).
  4. Reconstruct the claim lists for A, B, and combined
     (DETERMINISTIC, no M3 — ``reconstruct.py``).
  5. Per-claim, per-branch M3 verdicts (STEP 3 — 3N calls
     for N claims).
  6. Derive the cross-edit interactions in deterministic
     Python (``interact.py``).
  7. M3 explanation per non-``none`` interaction (STEP 5).
  8. Aggregate ``overall_interaction`` + ``overall_impact`` +
     ``overall_confidence`` for the top-level
     ``ClaimCentricAnalysis``.

The orchestrator returns a ``ClaimCentricAnalysis`` plus a
``ClaimAnalysisArtifacts`` dataclass with the byproducts (raw
responses, per-call latency, retries, the actual-content
representation) so the evaluation harness can audit every step.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.models.claims import (
    BaseClaim,
    BranchClaims,
    ClaimCentricAnalysis,
    ClaimEvaluation,
    ClaimInteraction,
    ClaimSurvival,
)
from app.models.claims import (
    CrossEditInteraction as ClaimCrossEditInteraction,
)
from app.models.media import VideoRepresentation
from app.services.alignment.run import align_branch_to_base
from app.services.minimax.client import MiniMaxClient
from app.services.semantic.claims.evaluate import evaluate_all_claims
from app.services.semantic.claims.explain import explain_interaction
from app.services.semantic.claims.extract import extract_base_claims
from app.services.semantic.claims.interact import (
    aggregate_overall_impact,
    aggregate_overall_interaction,
    build_claim_interaction,
)
from app.services.semantic.claims.prompts_claims import (
    EVALUATION_PROMPT_VERSION,
    EXTRACTION_PROMPT_VERSION,
)
from app.services.semantic.claims.reconstruct import (
    deterministic_surrogate_status,
    reconstruct_branch_claims,
    reconstruct_combined_claims,
)
from app.services.semantic.claims.represent import (
    ReconstructedActualContent,
    reconstruct_base_actual_content,
    reconstruct_branch_actual_content,
    reconstruct_combined_actual_content,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Artifacts.
# ---------------------------------------------------------------------------


@dataclass
class RepresentationSnapshot:
    """The four actual-content snapshots produced by ``represent.py``.

    One ``ReconstructedActualContent`` per view (BASE,
    branch_a, branch_b, combined). The orchestrator stores the
    snapshots in ``ClaimAnalysisArtifacts`` so the evaluation
    harness can persist them in the forensic artifact.

    Each snapshot carries the actual verbatim text the viewer
    hears (``lines[i].text``) plus a separate ``edit_metadata``
    audit of every delete / replace / trim the Phase 3
    alignment applied. The metadata is NEVER inlined into the
    candidate content the M3 sees.
    """

    base: ReconstructedActualContent
    branch_a: ReconstructedActualContent
    branch_b: ReconstructedActualContent
    combined: ReconstructedActualContent


@dataclass
class ClaimAnalysisArtifacts:
    """All byproducts of one Phase 4 claim-centric analysis call."""

    analysis: ClaimCentricAnalysis
    base_claims: list[BaseClaim]
    branch_a_claims_reconstructed: list[BaseClaim]
    branch_b_claims_reconstructed: list[BaseClaim]
    combined_claims_reconstructed: list[BaseClaim]
    # The four actual-content snapshots (BASE / A / B / combined).
    # Stored here so the eval harness can persist them in the
    # forensic artifact. ``representation`` is the canonical place
    # the harness reads the per-fixture content from; M3 reads
    # ``branch_reconstructed_lines`` (the text view) instead.
    representation: RepresentationSnapshot
    n_extraction_calls: int
    n_evaluation_calls: int
    n_explanation_calls: int
    extraction_retries: int
    evaluation_retries: int
    extraction_prompt_version: str
    evaluation_prompt_version: str
    model: str
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Orchestrator.
# ---------------------------------------------------------------------------


def analyze_claims(
    *,
    base: VideoRepresentation,
    branch_a: VideoRepresentation,
    branch_b: VideoRepresentation,
    client: MiniMaxClient,
    branch_a_name: str = "branch_a",
    branch_b_name: str = "branch_b",
    explain: bool = True,
) -> ClaimAnalysisArtifacts:
    """End-to-end Phase 4 claim-centric analysis.

    Steps:
      1. Phase 3 alignment for A and B.
      2. Build the four actual-content snapshots (BASE / A / B /
         combined) via ``represent.py``.
      3. Extract BASE claims (M3).
      4. Reconstruct claim lists for A, B, combined (deterministic).
      5. Per-claim, per-branch M3 verdicts (3N M3 calls). M3
         reads the actual-content ``text_lines`` (no markers,
         no BASE leakage) so the per-claim verdict reflects
         only the verbatim wording the viewer hears.
      6. Derive cross-edit interactions (deterministic).
      7. M3 explanations (one M3 call per non-``none`` interaction).
      8. Aggregate the top-level ``ClaimCentricAnalysis``.
    """
    # ------------------------------------------------------------------
    # Step 1 — Phase 3 alignment.
    # ------------------------------------------------------------------
    a_alignment = align_branch_to_base(base=base, branch=branch_a, branch_name=branch_a_name)
    b_alignment = align_branch_to_base(base=base, branch=branch_b, branch_name=branch_b_name)

    # ------------------------------------------------------------------
    # Step 2 — Actual-content reconstruction (deterministic).
    # ------------------------------------------------------------------
    # The four snapshots are the canonical record of what the
    # viewer hears in each view. M3-facing lines are the
    # ``text_lines()`` view; no markers, no BASE leakage.
    base_content = reconstruct_base_actual_content(base)
    branch_a_content = reconstruct_branch_actual_content(
        branch_name="branch_a",
        base=base,
        branch_alignment=a_alignment,
        branch_video=branch_a,
    )
    branch_b_content = reconstruct_branch_actual_content(
        branch_name="branch_b",
        base=base,
        branch_alignment=b_alignment,
        branch_video=branch_b,
    )
    combined_content = reconstruct_combined_actual_content(
        a_alignment=a_alignment,
        b_alignment=b_alignment,
        branch_a=branch_a,
        branch_b=branch_b,
        base=base,
    )
    representation = RepresentationSnapshot(
        base=base_content,
        branch_a=branch_a_content,
        branch_b=branch_b_content,
        combined=combined_content,
    )

    # M3-facing actual-content lines: verbatim text per BASE
    # position. No edit markers, no BASE leakage into deleted
    # or replaced slots.
    branch_evaluation_lines = {
        "branch_a": branch_a_content.text_lines(),
        "branch_b": branch_b_content.text_lines(),
        "combined": combined_content.text_lines(),
    }

    # ------------------------------------------------------------------
    # Step 3 — Extract BASE claims.
    # ------------------------------------------------------------------
    base_claims = extract_base_claims(base, client)
    extraction_retries = 0  # (the extract module retries internally; we report 0/1 here)

    # ------------------------------------------------------------------
    # Step 4 — Reconstruct claim lists.
    # ------------------------------------------------------------------
    branch_reconstructions = {
        "branch_a": reconstruct_branch_claims(base_claims, a_alignment),
        "branch_b": reconstruct_branch_claims(base_claims, b_alignment),
    }
    branch_reconstructions["combined"] = reconstruct_combined_claims(
        branch_reconstructions["branch_a"],
        branch_reconstructions["branch_b"],
    )

    # ------------------------------------------------------------------
    # Step 5 — Per-claim, per-branch M3 verdicts.
    # ------------------------------------------------------------------
    n_evaluation_calls = sum(
        len(branch_reconstructions[b]) for b in ("branch_a", "branch_b", "combined")
    )
    per_branch_evals = evaluate_all_claims(
        base_claims=base_claims,
        branch_reconstructions=branch_reconstructions,
        branch_reconstructed_lines=branch_evaluation_lines,
        client=client,
    )

    # Build the `BranchClaims` for the top-level result.
    branch_a_branch_claims = _build_branch_claims(
        "branch_a",
        branch_reconstructions["branch_a"],
        per_branch_evals["branch_a"],
    )
    branch_b_branch_claims = _build_branch_claims(
        "branch_b",
        branch_reconstructions["branch_b"],
        per_branch_evals["branch_b"],
    )
    combined_branch_claims = _build_branch_claims(
        "combined",
        branch_reconstructions["combined"],
        per_branch_evals["combined"],
    )

    # ------------------------------------------------------------------
    # Step 6 — Derive cross-edit interactions.
    # ------------------------------------------------------------------
    interactions: list[ClaimInteraction] = []
    for base_claim in base_claims:
        cid = base_claim.claim_id
        if cid not in per_branch_evals["branch_a"]:
            continue  # M3 didn't return this claim (low-importance)
        if cid not in per_branch_evals["branch_b"]:
            continue
        if cid not in per_branch_evals["combined"]:
            continue
        a_status = per_branch_evals["branch_a"][cid].status
        b_status = per_branch_evals["branch_b"][cid].status
        c_status = per_branch_evals["combined"][cid].status
        ci = build_claim_interaction(
            claim_id=cid,
            claim_meaning=base_claim.meaning,
            claim_type=base_claim.claim_type,
            claim_importance=base_claim.importance,
            branch_a_status=a_status,
            branch_b_status=b_status,
            combined_status=c_status,
        )
        interactions.append(ci)

    # ------------------------------------------------------------------
    # Step 7 — M3 explanations (prose only).
    # ------------------------------------------------------------------
    n_explanation_calls = 0
    if explain:
        explained: list[ClaimInteraction] = []
        for ci in interactions:
            if ci.interaction != ClaimCrossEditInteraction.NONE:
                explained.append(explain_interaction(ci, client))
                n_explanation_calls += 1
            else:
                explained.append(ci)
        interactions = explained

    # ------------------------------------------------------------------
    # Step 8 — Aggregate the top-level result.
    # ------------------------------------------------------------------
    overall_interaction = aggregate_overall_interaction(interactions)
    overall_impact = aggregate_overall_impact(combined_branch_claims)

    confidences: list[float] = []
    for branch_name in ("branch_a", "branch_b", "combined"):
        for ev in per_branch_evals.get(branch_name, {}).values():
            confidences.append(ev.confidence)
    overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    analysis = ClaimCentricAnalysis(
        base_claims=base_claims,
        branch_a_claims=branch_a_branch_claims,
        branch_b_claims=branch_b_branch_claims,
        combined_claims=combined_branch_claims,
        interactions=interactions,
        overall_interaction=overall_interaction,
        overall_impact=overall_impact,
        overall_confidence=overall_confidence,
        notes="Phase 4 v4.1.0 claim-centric analysis",
    )
    return ClaimAnalysisArtifacts(
        analysis=analysis,
        base_claims=base_claims,
        branch_a_claims_reconstructed=branch_reconstructions["branch_a"],
        branch_b_claims_reconstructed=branch_reconstructions["branch_b"],
        combined_claims_reconstructed=branch_reconstructions["combined"],
        representation=representation,
        n_extraction_calls=1,
        n_evaluation_calls=n_evaluation_calls,
        n_explanation_calls=n_explanation_calls,
        extraction_retries=extraction_retries,
        evaluation_retries=0,
        extraction_prompt_version=EXTRACTION_PROMPT_VERSION,
        evaluation_prompt_version=EVALUATION_PROMPT_VERSION,
        model=client.model,
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _build_branch_claims(
    branch_name: str,
    reconstructed: list[BaseClaim],
    evaluations: dict[str, ClaimEvaluation],
) -> BranchClaims:
    """Assemble a `BranchClaims` from the reconstructed claims + M3 verdicts."""
    survivals: list[ClaimSurvival] = []
    for c in reconstructed:
        ev = evaluations.get(c.claim_id)
        if ev is None:
            # M3 did not return this claim (low-importance).
            # Fall back to the deterministic surrogate.
            surrogate = deterministic_surrogate_status([c]).get(c.claim_id)
            assert surrogate is not None
            survivals.append(
                ClaimSurvival(
                    claim_id=c.claim_id,
                    branch=branch_name,  # type: ignore[arg-type]
                    status=surrogate,
                    surviving_evidence=[],
                    rationale="(no M3 verdict; deterministic surrogate)",
                )
            )
            continue
        survivals.append(
            ClaimSurvival(
                claim_id=ev.claim_id,
                branch=branch_name,  # type: ignore[arg-type]
                status=ev.status,
                surviving_evidence=list(ev.surviving_evidence),
                rationale=ev.rationale,
            )
        )
    return BranchClaims(branch=branch_name, claim_survivals=survivals)  # type: ignore[arg-type]


__all__ = [
    "ClaimAnalysisArtifacts",
    "RepresentationSnapshot",
    "analyze_claims",
]
