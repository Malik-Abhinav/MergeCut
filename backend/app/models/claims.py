"""Strict Pydantic schema for the Phase 4 claim-centric pipeline.

This replaces the edit-centric v2 schema (`app.models.semantic_v2`).
The user explicitly asked for a redesign because the edit-centric
prompt was failing the canonical prerequisite case 0/4 (M3 saw
"prerequisite is still in BASE" instead of "both branches removed
their own copy, combined has none").

Architecture (per the user's brief):

  STEP 1  Extract BASE claims (M3 call, schema: BaseClaim).
  STEP 2  Reconstruct the claim lists for A, B, A+B using the
          Phase 3 alignment (DETERMINISTIC, no M3).
  STEP 3  Per-claim preservation verdicts (M3 call, schema:
          ClaimSurvival per branch).
  STEP 4  Cross-edit interaction is DERIVED in deterministic
          Python from the per-claim verdicts (no M3).
  STEP 5  M3 only generates the human-readable EXPLANATION for
          each interaction; the explanation does NOT influence
          the classification.

All Pydantic models here are `extra="forbid"` so M3 cannot
silently invent extra fields. Malformed responses are rejected
by the orchestrator (which retries once with a repair prompt).
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Claim types.
# ---------------------------------------------------------------------------


class ClaimType(enum.StrEnum):
    """The kind of semantic claim a BASE claim represents.

    The taxonomy mirrors the user's brief verbatim:

    - prerequisite         : a precondition ("before X, do Y")
    - qualifier            : a narrowing/limiting condition
    - exception            : a "unless..." condition
    - temporal_scope       : a time/duration constraint
    - causal_dependency    : a cause/effect relationship
    - entity_scope         : a who/what constraint
    - instruction          : a directive
    - prohibition          : a "do not..." directive
    - narrative_dependency : a context that the meaning depends on
    - other                : everything else
    """

    PREREQUISITE = "prerequisite"
    QUALIFIER = "qualifier"
    EXCEPTION = "exception"
    TEMPORAL_SCOPE = "temporal_scope"
    CAUSAL_DEPENDENCY = "causal_dependency"
    ENTITY_SCOPE = "entity_scope"
    INSTRUCTION = "instruction"
    PROHIBITION = "prohibition"
    NARRATIVE_DEPENDENCY = "narrative_dependency"
    OTHER = "other"


class ClaimImportance(enum.StrEnum):
    """How load-bearing this claim is for the BASE's meaning.

    - critical : the claim is the load-bearing one (prerequisite,
                 safety threshold, exception). Loss of this claim
                 is `broken`.
    - high     : the claim is a primary directive or constraint.
    - medium   : the claim is a supporting detail.
    - low      : the claim is decorative.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Claim evidence region.
# ---------------------------------------------------------------------------


class ClaimEvidenceRegion(BaseModel):
    """One [start, end] span in the BASE timeline that carries the
    claim's meaning."""

    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0.0, description="Start timestamp in seconds.")
    end: float = Field(ge=0.0, description="End timestamp in seconds.")
    description: str = Field(
        min_length=1,
        description=(
            "Short description of the evidence (e.g. 'the prerequisite "
            "is stated explicitly in this segment')."
        ),
    )

    @field_validator("end")
    @classmethod
    def _end_at_or_after_start(cls, v: float, info) -> float:  # type: ignore[no-untyped-def]
        start = info.data.get("start")
        if start is not None and v < start:
            raise ValueError("end must be >= start")
        return v


# ---------------------------------------------------------------------------
# BASE claim.
# ---------------------------------------------------------------------------


class BaseClaim(BaseModel):
    """One semantic claim extracted from BASE.

    - `claim_id`         : stable id (e.g. "C1"). Used as the join
                            key across branches.
    - `meaning`          : the natural-language meaning the claim
                            communicates. Free text. M3 should write
                            this so that "meaning is preserved" is
                            testable against this string.
    - `claim_type`       : the claim taxonomy bucket.
    - `importance`       : critical / high / medium / low.
    - `evidence_regions` : the (start, end) spans in BASE where
                            this claim is communicated.
    - `equivalents`      : other spans in BASE that express the
                            SAME meaning (paraphrases, restatements,
                            parallel prohibitions). When the Phase 3
                            edit list removes one of these spans, the
                            claim is still preserved in the branch
                            (the equivalent survives). This is the
                            core of the per-branch "preserved"
                            verdict.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, description="Stable id, e.g. 'C1'.")
    meaning: str = Field(
        min_length=1,
        description="The natural-language meaning of this claim.",
    )
    claim_type: ClaimType
    importance: ClaimImportance
    evidence_regions: list[ClaimEvidenceRegion] = Field(
        default_factory=list,
        description="(start, end) spans in BASE where this claim is communicated.",
    )
    equivalents: list[ClaimEvidenceRegion] = Field(
        default_factory=list,
        description=(
            "Other BASE spans that communicate the SAME meaning "
            "(paraphrases, restatements, parallel prohibitions). "
            "Used to decide whether a branch still preserves the claim."
        ),
    )


# ---------------------------------------------------------------------------
# Per-claim, per-branch preservation verdict.
# ---------------------------------------------------------------------------


class ClaimStatus(enum.StrEnum):
    """Per-claim, per-branch preservation verdict.

    M3 returns exactly one of these per claim per branch:

    - preserved : the meaning survives somewhere in the branch
                   (in the original evidence region, in an
                   equivalent, or in a new restatement).
    - degraded  : the meaning is communicated in the branch but
                   weakened (qualifier narrowed, scope tightened,
                   hedge dropped).
    - broken    : the meaning has been dropped or contradicted in
                   the branch. The claim's evidence region is gone
                   AND no equivalent survives.
    """

    PRESERVED = "preserved"
    DEGRADED = "degraded"
    BROKEN = "broken"


class ClaimSurvival(BaseModel):
    """M3's per-claim, per-branch preservation verdict.

    - `claim_id`                : the BASE claim id.
    - `branch`                  : 'branch_a' or 'branch_b' (the
                                  orchestrator adds 'combined' as a
                                  third branch with its own
                                  reconstructed list).
    - `status`                  : preserved / degraded / broken.
    - `surviving_evidence`      : the (start, end) spans in the
                                  branch where the meaning is
                                  still carried. Empty when broken.
    - `rationale`               : short M3 explanation.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    branch: Literal["branch_a", "branch_b", "combined"]
    status: ClaimStatus
    surviving_evidence: list[ClaimEvidenceRegion] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class BranchClaims(BaseModel):
    """The per-branch per-claim verdicts for one branch."""

    model_config = ConfigDict(extra="forbid")

    branch: Literal["branch_a", "branch_b", "combined"]
    claim_survivals: list[ClaimSurvival] = Field(
        default_factory=list,
        description=(
            "One entry per important BASE claim. The orchestrator "
            "MAY return verdicts for only a subset of the BASE "
            "claims (e.g. it could skip 'low'-importance claims); "
            "missing entries default to `preserved` in the "
            "derivation step (see `app.services.semantic.claims.interact`)."
        ),
    )


# ---------------------------------------------------------------------------
# Cross-edit interaction (DERIVED, not authored by M3).
# ---------------------------------------------------------------------------


class CrossEditInteraction(enum.StrEnum):
    """The two-axis taxonomy from `docs/architecture.md`.

    NB: this is the same enum as `app.models.semantic_v2.CrossEditInteraction`
    but is re-declared in this module to keep the claim-centric
    schema self-contained. The orchestrator projects the
    claim-centric interactions onto the v2 `interactions` field
    when a Phase 4 v2 consumer needs them.
    """

    NONE = "none"
    AMPLIFIES_EXISTING_ISSUE = "amplifies_existing_issue"
    CREATES_NEW_CONFLICT = "creates_new_conflict"


class ClaimInteraction(BaseModel):
    """One cross-edit interaction, DERIVED from per-claim verdicts.

    The `interaction` field is computed by `interact.derive_interaction()`
    from the three per-claim verdicts (A, B, combined) plus the
    claim's importance. M3 does NOT write this field.

    `derivation_reason` is a short human-readable explanation of
    which rule fired. The v2 schema's `recommended_resolution` is
    M3-authored (per the user's STEP 5 — explanation does not
    control the classification but the orchestrator may ask M3
    to suggest a resolution).
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_meaning: str
    claim_type: ClaimType
    claim_importance: ClaimImportance
    branch_a_status: ClaimStatus
    branch_b_status: ClaimStatus
    combined_status: ClaimStatus
    interaction: CrossEditInteraction
    derivation_reason: str = Field(
        min_length=1,
        description=(
            "Plain-English description of which derivation rule fired. "
            "Deterministic; produced by `app.services.semantic.claims.interact`."
        ),
    )
    m3_explanation: str | None = Field(
        default=None,
        description=(
            "M3-authored human-readable explanation. May be None if "
            "the explain step was skipped or failed. Does NOT "
            "influence the classification."
        ),
    )
    m3_recommended_resolution: str | None = Field(
        default=None,
        description="M3-authored suggested fix. Does NOT influence the classification.",
    )


# ---------------------------------------------------------------------------
# Top-level claim-centric analysis.
# ---------------------------------------------------------------------------


class ClaimCentricAnalysis(BaseModel):
    """Top-level Phase 4 result (claim-centric).

    Aggregation order:

      base_claims        : the BASE claim list (from STEP 1).
      branch_a_claims    : M3 per-claim verdicts for A (STEP 3).
      branch_b_claims    : M3 per-claim verdicts for B (STEP 3).
      combined_claims    : M3 per-claim verdicts for A+B (STEP 3).
      interactions       : the derived cross-edit interactions
                           (one per important BASE claim). The
                           `overall_interaction` is the "most
                           severe" interaction across the
                           interaction list, with ties broken by
                           claim importance.
      overall_impact     : the most-severe per-claim impact
                           across the combined list (preserved <
                           degraded < broken).
      overall_confidence : mean of the per-claim M3 confidences
                           (when present).
    """

    model_config = ConfigDict(extra="forbid")

    base_claims: list[BaseClaim] = Field(default_factory=list)
    branch_a_claims: BranchClaims
    branch_b_claims: BranchClaims
    combined_claims: BranchClaims
    interactions: list[ClaimInteraction] = Field(default_factory=list)
    overall_interaction: CrossEditInteraction
    overall_impact: ClaimStatus
    overall_confidence: float = Field(ge=0.0, le=1.0)
    notes: str | None = None


# ---------------------------------------------------------------------------
# Per-claim M3 response shapes (used by evaluate_claims()).
# ---------------------------------------------------------------------------


class ClaimEvaluationRequest(BaseModel):
    """One claim's worth of context M3 sees in STEP 3."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    meaning: str
    claim_type: ClaimType
    importance: ClaimImportance
    base_evidence_regions: list[ClaimEvidenceRegion]
    base_equivalents: list[ClaimEvidenceRegion]
    branch_name: Literal["branch_a", "branch_b", "combined"]
    # The reconstructed transcript of the branch (BASE with that
    # branch's edits applied). M3 reads this and decides
    # preserved / degraded / broken.
    branch_reconstructed_lines: list[str]


class ClaimEvaluation(BaseModel):
    """M3's response to a single ClaimEvaluationRequest."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    status: ClaimStatus
    surviving_evidence: list[ClaimEvidenceRegion] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


__all__ = [
    "ClaimType",
    "ClaimImportance",
    "ClaimEvidenceRegion",
    "BaseClaim",
    "ClaimStatus",
    "ClaimSurvival",
    "BranchClaims",
    "CrossEditInteraction",
    "ClaimInteraction",
    "ClaimCentricAnalysis",
    "ClaimEvaluationRequest",
    "ClaimEvaluation",
]
