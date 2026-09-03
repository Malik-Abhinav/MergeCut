"""Deterministic cross-edit interaction derivation.

The product principle (Phase 4.5):

> For each claim:
>   If combined = broken
>     AND neither A nor B is broken (i.e. each is preserved or
>        degraded)
>     → interaction = creates_new_conflict
>   If combined = broken
>     AND (A = broken OR B = broken)
>     → none. Combined=broken alone does not prove amplification
>       of an existing issue without an additional evidence
>       model (no new evidence model at this phase).
>   Otherwise
>     → none.

The "preserved" vs "degraded" distinction is intentionally
irrelevant to the cross-edit verdict under the current rules.
It matters for the per-claim status (which is surfaced through
`BranchClaims` for the user to inspect) but the interaction
itself only depends on whether the claim was BROKEN in either
individual branch.

Without per-claim confidence deltas or an explicit M3
amplification-vs-new-loss call, the three per-claim verdicts
alone cannot support the more granular
`amplifies_existing_issue` classification the previous
rule-set returned. We therefore return `none` for the cases
that the previous ruleset over-classified
(`amplifies_existing_issue` for preserved+degraded+broken
or preserved+preserved+degraded); the per-claim status for
each branch is still surfaced to the user, who can manually
inspect those tuples.

The M3 model does NOT decide the interaction. The orchestrator
calls `derive_interaction()` with the three per-claim verdicts
and gets back a `CrossEditInteraction` + a short
`derivation_reason` explaining which rule fired.

These rules are tested directly in
`tests/unit/test_claims_deterministic.py` — no M3 needed.
"""

from __future__ import annotations

from app.models.claims import (
    ClaimImportance,
    ClaimInteraction,
    ClaimStatus,
    CrossEditInteraction,
)


def derive_interaction(
    *,
    claim_id: str,
    claim_meaning: str,
    claim_type,  # ClaimType
    claim_importance: ClaimImportance,
    branch_a_status: ClaimStatus,
    branch_b_status: ClaimStatus,
    combined_status: ClaimStatus,
) -> tuple[CrossEditInteraction, str]:
    """Apply the deterministic product-principle rules.

    Returns (interaction, reason). The reason is a short string
    describing which rule fired. M3 never sees this function;
    it only sees the result via the orchestrator.

    Rules (in priority order):

      R1  combined = broken
          AND A != broken (preserved or degraded)
          AND B != broken (preserved or degraded)
          → creates_new_conflict.
          (Canonical MergeCut: each branch alone keeps the
          meaning in some form; the combined edit drops it.
          "Degraded" still counts as "not broken" — the claim
          is communicated in the branch, just weakened.)

      R2  combined = broken
          AND (A = broken OR B = broken)
          → none.
          (Either individual already broke the claim. Without
          an additional evidence model — per-claim confidence
          deltas, or an explicit M3 call distinguishing
          "amplification" from "new loss" — the combined
          verdict alone cannot prove the other branch
          *amplified* an existing issue. The per-branch
          statuses still surface the brokenness so the user
          can inspect it.)

      R3  combined = degraded
          → none.
          (The previous rule-set returned
          `amplifies_existing_issue` for
          preserved+preserved+degraded and
          preserved+degraded+degraded. Under the product
          principle, those tuples do not support a
          cross-edit classification. The per-claim statuses
          surface the weakening; the cross-edit verdict is
          `none` until an additional evidence model is added.)

      R4  Otherwise → none.

    Notes:

    - "Neither A nor B is broken" means each is preserved OR
      degraded. The product principle treats the two
      symmetrically for the cross-edit verdict.
    - R2 is a conservative default. The user said explicitly:
      "if either individual branch is already broken, combined
      broken alone does not prove amplification and returns
      none unless additional evidence exists (no new evidence
      model now)".
    - R3 is a more conservative return than the previous
      `amplifies_existing_issue` for the
      (preserved, preserved, degraded) and
      (preserved, degraded, degraded) shapes. The previous rule
      was unsupported by an evidence model; under the product
      principle we return `none`.
    - The "redundant claim remains" case is handled upstream:
      the orchestrator does not call `derive_interaction()` for
      a claim whose surviving_evidence is still present in the
      combined reconstruction. (See `evaluate.py`.)
    """
    if combined_status == ClaimStatus.BROKEN:
        a_intact = branch_a_status in {ClaimStatus.PRESERVED, ClaimStatus.DEGRADED}
        b_intact = branch_b_status in {ClaimStatus.PRESERVED, ClaimStatus.DEGRADED}
        if a_intact and b_intact:
            return (
                CrossEditInteraction.CREATES_NEW_CONFLICT,
                (
                    "R1: combined=broken AND A and B each preserved/degraded "
                    "→ creates_new_conflict (canonical MergeCut: each branch "
                    "alone keeps the meaning, combined drops it)"
                ),
            )
        return (
            CrossEditInteraction.NONE,
            (
                "R2: combined=broken AND (A or B already broken) → none "
                "(without an additional evidence model, combined=broken alone "
                "does not prove amplification; per-branch statuses surface the "
                "brokenness for the user to inspect)"
            ),
        )

    if combined_status == ClaimStatus.DEGRADED:
        return (
            CrossEditInteraction.NONE,
            (
                "R3: combined=degraded → none (per the product principle, the "
                "per-claim statuses surface the weakening; the cross-edit "
                "interaction is not classified without an additional "
                "evidence model)"
            ),
        )

    return (
        CrossEditInteraction.NONE,
        "R4: combined=preserved → none (no weakening to detect)",
    )


def build_claim_interaction(
    *,
    claim_id: str,
    claim_meaning: str,
    claim_type,
    claim_importance: ClaimImportance,
    branch_a_status: ClaimStatus,
    branch_b_status: ClaimStatus,
    combined_status: ClaimStatus,
    m3_explanation: str | None = None,
    m3_recommended_resolution: str | None = None,
) -> ClaimInteraction:
    """Build a `ClaimInteraction` with the derived interaction + reason.

    The orchestrator calls this once per important claim after
    M3 has returned the three per-branch per-claim verdicts.
    """
    interaction, reason = derive_interaction(
        claim_id=claim_id,
        claim_meaning=claim_meaning,
        claim_type=claim_type,
        claim_importance=claim_importance,
        branch_a_status=branch_a_status,
        branch_b_status=branch_b_status,
        combined_status=combined_status,
    )
    return ClaimInteraction(
        claim_id=claim_id,
        claim_meaning=claim_meaning,
        claim_type=claim_type,
        claim_importance=claim_importance,
        branch_a_status=branch_a_status,
        branch_b_status=branch_b_status,
        combined_status=combined_status,
        interaction=interaction,
        derivation_reason=reason,
        m3_explanation=m3_explanation,
        m3_recommended_resolution=m3_recommended_resolution,
    )


# ---------------------------------------------------------------------------
# Aggregate derivations.
# ---------------------------------------------------------------------------


def aggregate_overall_interaction(
    interactions: list[ClaimInteraction],
) -> CrossEditInteraction:
    """Pick the most-severe interaction across the interaction list.

    Order of severity:
      creates_new_conflict > amplifies_existing_issue > none.

    For ties, prefer the highest-importance claim
    (critical > high > medium > low). The user said:
    "canonical demo fixture correct in at least 3/4 runs" —
    the demo is the canonical MergeCut case, so we must
    surface creates_new_conflict whenever ANY claim has it.
    """
    severity = {
        CrossEditInteraction.NONE: 0,
        CrossEditInteraction.AMPLIFIES_EXISTING_ISSUE: 1,
        CrossEditInteraction.CREATES_NEW_CONFLICT: 2,
    }
    importance = {
        ClaimImportance.LOW: 0,
        ClaimImportance.MEDIUM: 1,
        ClaimImportance.HIGH: 2,
        ClaimImportance.CRITICAL: 3,
    }
    if not interactions:
        return CrossEditInteraction.NONE
    return max(
        interactions,
        key=lambda it: (severity[it.interaction], importance[it.claim_importance]),
    ).interaction


def aggregate_overall_impact(
    branch: BranchClaimsAggregate,  # type: ignore[name-defined]  # noqa: F821
) -> ClaimStatus:
    """Pick the most-severe per-claim status across one branch.

    Used for `ClaimCentricAnalysis.overall_impact` (the combined
    branch). preserved < degraded < broken.
    """
    severity = {
        ClaimStatus.PRESERVED: 0,
        ClaimStatus.DEGRADED: 1,
        ClaimStatus.BROKEN: 2,
    }
    if not branch.claim_survivals:
        return ClaimStatus.PRESERVED
    return max(
        (s.status for s in branch.claim_survivals),
        key=lambda st: severity[st],
    )


__all__ = [
    "derive_interaction",
    "build_claim_interaction",
    "aggregate_overall_interaction",
    "aggregate_overall_impact",
]
