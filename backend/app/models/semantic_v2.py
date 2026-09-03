"""Strict Pydantic schema for Phase 4 semantic analysis (rich taxonomy).

The Phase 1 `semantic.py` model is the *text-only spike contract*: it
asks M3 to return a flat per-conflict list with binary branch /
combined safety booleans. The Phase 1 build-log reports
demonstrated that this model collapses the per-branch safety axis
and treats softened wording and dropped claims identically.

Phase 4 introduces the two-axis taxonomy recorded in
`docs/architecture.md`:

  Axis 1 — `impact_level`  ∈ {preserved, degraded, broken}
  Axis 2 — `cross_edit_interaction`  ∈
              {none, amplifies_existing_issue, creates_new_conflict}

The critical MergeCut condition is the intersection:

  A.impact_level  == preserved
  B.impact_level  == preserved
  A+B.impact_level == broken
  cross_edit_interaction == creates_new_conflict

This is the case the v1 model conflated; the v2 schema keeps the
two axes separate and asks M3 to evaluate each independently.

The new types are deliberately additive — they are returned
*in addition* to the v1 fields, so a v1 consumer can still read
the response. New production code should use the v2 fields.

All Pydantic models here are `extra="forbid"` so M3 cannot
silently invent extra fields. Malformed responses are rejected by
the validator in `app.services.semantic.run` (which retries once
with a repair prompt before giving up).
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Axes (docs/architecture.md).
# ---------------------------------------------------------------------------


class ImpactLevel(enum.StrEnum):
    """How much of BASE's meaning survives in a (branch or combined) result."""

    PRESERVED = "preserved"
    DEGRADED = "degraded"
    BROKEN = "broken"


class CrossEditInteraction(enum.StrEnum):
    """How the two branches relate to each other."""

    NONE = "none"
    AMPLIFIES_EXISTING_ISSUE = "amplifies_existing_issue"
    CREATES_NEW_CONFLICT = "creates_new_conflict"


# Kept for backward compatibility with the Phase 1 schema.
ConflictType = Literal[
    "prerequisite_loss",
    "qualifier_loss",
    "exception_loss",
    "temporal_scope_change",
    "causal_dependency_break",
    "entity_scope_change",
    "narrative_dependency_break",
    "contradiction",
    "other",
]


Severity = Literal["low", "medium", "high"]


# ---------------------------------------------------------------------------
# Pieces.
# ---------------------------------------------------------------------------


class TimestampedEvidence(BaseModel):
    """One (video, [start, end], description) evidence pointer.

    Used both for the per-impact `evidence` list and for the
    per-interaction `evidence` list. `video` is one of the
    four possible source videos: "base", "branch_a", "branch_b",
    "merged".
    """

    model_config = ConfigDict(extra="forbid")

    video: Literal["base", "branch_a", "branch_b", "merged"] = Field(
        description="Which video the evidence snippet comes from."
    )
    start: float = Field(ge=0.0, description="Start timestamp in seconds.")
    end: float = Field(ge=0.0, description="End timestamp in seconds.")
    description: str = Field(min_length=1, description="What this evidence shows.")

    @field_validator("end")
    @classmethod
    def _end_at_or_after_start(cls, v: float, info) -> float:  # type: ignore[no-untyped-def]
        start = info.data.get("start")
        if start is not None and v < start:
            raise ValueError("end must be >= start")
        return v


class BranchImpact(BaseModel):
    """One branch's impact on BASE.

    Fields:
    - `branch`            : "branch_a" or "branch_b".
    - `impact_level`      : preserved / degraded / broken.
    - `affected_claims`   : which BASE claims this branch touches.
                            Free text — M3 decides granularity.
    - `preserved_equivalents` : which other claims/segments in
                                the branch carry the same meaning.
                                Empty when impact is `preserved`
                                trivially (no edit).
    - `evidence`          : timestamped evidence pointers.
    - `confidence`        : M3 confidence in [0, 1].
    - `rationale`         : M3 short justification.
    """

    model_config = ConfigDict(extra="forbid")

    branch: Literal["branch_a", "branch_b"]
    impact_level: ImpactLevel
    affected_claims: list[str] = Field(default_factory=list)
    preserved_equivalents: list[str] = Field(default_factory=list)
    evidence: list[TimestampedEvidence] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class CrossEditInteraction_(BaseModel):  # noqa: N801  (trailing _ to avoid clashing with the enum)
    """One cross-edit interaction between Branch A and Branch B.

    Multiple of these may be returned (one per candidate
    pair of A-edit + B-edit). Each is independent: M3 may
    produce one `creates_new_conflict` interaction and several
    `none` interactions in the same response.

    The `combined_impact` is M3's verdict on the impact of
    applying BOTH this A-edit and this B-edit to BASE. It is
    independent of the per-branch impacts — a branch can be
    `preserved` alone but `broken` combined.

    `base_claim`, `branch_a_effect`, `branch_b_effect`, and
    `combined_effect` are short M3-authored free-text
    descriptions. They support the cross-edit classification
    and are surfaced in the UI.
    """

    model_config = ConfigDict(extra="forbid")

    branch_a_edit_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Identifiers of the Branch A edits in this interaction. "
            "These are shot-level ids produced by the Phase 3 "
            "alignment (e.g. 'shot_0001'). Empty list when the "
            "interaction is across the whole video rather than a "
            "specific edit."
        ),
    )
    branch_b_edit_ids: list[str] = Field(default_factory=list)
    combined_impact: ImpactLevel
    interaction_type: CrossEditInteraction
    conflict_type: ConflictType | None = Field(
        default=None,
        description=(
            "When the interaction creates or amplifies a conflict, "
            "M3 names it here using the Phase 1 conflict taxonomy. "
            "None when interaction_type is 'none'."
        ),
    )
    base_claim: str = Field(
        min_length=1,
        description="The claim in BASE that the interaction turns on.",
    )
    branch_a_effect: str = Field(min_length=1, description="What Branch A does to that claim.")
    branch_b_effect: str = Field(min_length=1, description="What Branch B does to that claim.")
    combined_effect: str = Field(
        min_length=1,
        description="What applying BOTH branches does to the claim.",
    )
    evidence: list[TimestampedEvidence] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_resolution: str = Field(min_length=1)


class SemanticAnalysisV2(BaseModel):
    """Top-level Phase 4 semantic analysis result.

    Combines the v1 backward-compatible fields (kept on
    `legacy_v1_compat`) with the new two-axis taxonomy.

    The new production fields are:

    - `branch_a_impact`, `branch_b_impact` : per-branch
       `BranchImpact` (impact_level + evidence).
    - `combined_impact`                    : M3's verdict on
       the combined video's `ImpactLevel` (independent of the
       per-branch impacts — exactly the axis the v1 model
       collapsed).
    - `interactions`                       : one or more
       `CrossEditInteraction` entries describing how A and B
       relate. At least one must have `interaction_type =
       "creates_new_conflict"` whenever the canonical MergeCut
       condition holds; this is checked at validation time
       in `app.services.semantic.run`.
    - `overall_confidence`                 : M3 confidence in
       [0, 1].
    """

    model_config = ConfigDict(extra="forbid")

    # ------------------------------------------------------------------
    # New (v2) — production fields.
    # ------------------------------------------------------------------
    branch_a_impact: BranchImpact
    branch_b_impact: BranchImpact
    combined_impact: ImpactLevel = Field(
        description=(
            "Impact level of the combined (BASE + A + B) video. "
            "Independent of the per-branch impacts."
        )
    )
    interactions: list[CrossEditInteraction_] = Field(default_factory=list)
    overall_confidence: float = Field(ge=0.0, le=1.0)
    notes: str | None = Field(default=None, description="Optional short M3 note.")

    # ------------------------------------------------------------------
    # Backward-compatible v1 mirror (populated by the orchestrator
    # from the v2 fields, never read by M3). Keeping these lets a
    # v1 consumer (e.g. the existing spike runner) read the same
    # response without modification.
    # ------------------------------------------------------------------
    legacy_v1_compat: LegacyV1Compat | None = Field(default=None)

    @field_validator("interactions")
    @classmethod
    def _at_least_one_interaction(
        cls, v: list[CrossEditInteraction_]
    ) -> list[CrossEditInteraction_]:
        if not v:
            raise ValueError(
                "SemanticAnalysisV2 must include at least one CrossEditInteraction. "
                "The orchestrator must always pass at least one candidate pair."
            )
        return v


class LegacyV1Compat(BaseModel):
    """Phase 1 v1 fields derived from the v2 fields.

    Not requested from M3; populated by the orchestrator after
    the v2 model validates. Exists so a v1 consumer (the
    existing spike runner, downstream services) can still read
    the v2 response.
    """

    model_config = ConfigDict(extra="forbid")

    branch_a_safe: bool
    branch_b_safe: bool
    combined_safe: bool


# Re-bind the forward reference.
SemanticAnalysisV2.model_rebuild()


# ---------------------------------------------------------------------------
# Convenience: derive legacy v1 fields from a v2 result.
# ---------------------------------------------------------------------------


def to_legacy_v1(result: SemanticAnalysisV2) -> LegacyV1Compat:
    """Project the v2 two-axis result onto the v1 boolean axes.

    v1's `branch_a_safe` ≡ (v2.branch_a_impact.impact_level in
    {preserved}). v1's `combined_safe` ≡
    (v2.combined_impact == preserved). This is the projection
    documented in PROJECT_PLAN §15; it is *not* a loss of
    information because the v2 result still carries the per-axis
    impact levels and the interaction list.
    """
    return LegacyV1Compat(
        branch_a_safe=result.branch_a_impact.impact_level == ImpactLevel.PRESERVED,
        branch_b_safe=result.branch_b_impact.impact_level == ImpactLevel.PRESERVED,
        combined_safe=result.combined_impact == ImpactLevel.PRESERVED,
    )


__all__ = [
    "ImpactLevel",
    "CrossEditInteraction",
    "ConflictType",
    "Severity",
    "TimestampedEvidence",
    "BranchImpact",
    "CrossEditInteraction_",
    "LegacyV1Compat",
    "SemanticAnalysisV2",
    "to_legacy_v1",
]
