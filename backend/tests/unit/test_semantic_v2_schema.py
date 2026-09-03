"""Unit tests for `app.models.semantic_v2`.

Covers the strict Pydantic schema, the to_legacy_v1 projection,
and the invariants the orchestrator relies on (at least one
interaction; end >= start on every evidence; confidence in
[0, 1]).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.semantic_v2 import (
    BranchImpact,
    ConflictType,
    CrossEditInteraction,
    CrossEditInteraction_,
    ImpactLevel,
    SemanticAnalysisV2,
    TimestampedEvidence,
    to_legacy_v1,
)

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _evidence(
    video: str = "base",
    start: float = 0.0,
    end: float = 1.0,
    description: str = "test evidence",
) -> TimestampedEvidence:
    return TimestampedEvidence(video=video, start=start, end=end, description=description)


def _branch_impact(branch: str, level: ImpactLevel) -> BranchImpact:
    return BranchImpact(
        branch=branch,
        impact_level=level,
        affected_claims=["claim X"],
        preserved_equivalents=[],
        evidence=[_evidence()],
        confidence=0.9,
        rationale="test",
    )


def _interaction(
    *,
    combined: ImpactLevel = ImpactLevel.BROKEN,
    interaction_type: CrossEditInteraction = CrossEditInteraction.CREATES_NEW_CONFLICT,
    conflict_type: ConflictType | None = "prerequisite_loss",
    base_claim: str = "claim X",
    branch_a_effect: str = "A softens claim X",
    branch_b_effect: str = "B softens claim X",
    combined_effect: str = "Combined breaks claim X",
    confidence: float = 0.9,
    resolution: str = "Keep A's version, discard B's softening",
) -> CrossEditInteraction_:
    return CrossEditInteraction_(
        branch_a_edit_ids=["shot_0001"],
        branch_b_edit_ids=["shot_0001"],
        combined_impact=combined,
        interaction_type=interaction_type,
        conflict_type=conflict_type,
        base_claim=base_claim,
        branch_a_effect=branch_a_effect,
        branch_b_effect=branch_b_effect,
        combined_effect=combined_effect,
        evidence=[_evidence()],
        confidence=confidence,
        recommended_resolution=resolution,
    )


def _full_v2() -> SemanticAnalysisV2:
    return SemanticAnalysisV2(
        branch_a_impact=_branch_impact("branch_a", ImpactLevel.PRESERVED),
        branch_b_impact=_branch_impact("branch_b", ImpactLevel.PRESERVED),
        combined_impact=ImpactLevel.BROKEN,
        interactions=[_interaction()],
        overall_confidence=0.9,
        notes="",
    )


# ---------------------------------------------------------------------------
# Enums.
# ---------------------------------------------------------------------------


def test_impact_level_values() -> None:
    assert ImpactLevel.PRESERVED.value == "preserved"
    assert ImpactLevel.DEGRADED.value == "degraded"
    assert ImpactLevel.BROKEN.value == "broken"


def test_cross_edit_interaction_values() -> None:
    assert CrossEditInteraction.NONE.value == "none"
    assert CrossEditInteraction.AMPLIFIES_EXISTING_ISSUE.value == "amplifies_existing_issue"
    assert CrossEditInteraction.CREATES_NEW_CONFLICT.value == "creates_new_conflict"


# ---------------------------------------------------------------------------
# TimestampedEvidence.
# ---------------------------------------------------------------------------


def test_evidence_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        TimestampedEvidence(video="base", start=5.0, end=2.0, description="x")


def test_evidence_accepts_end_equal_start() -> None:
    e = TimestampedEvidence(video="base", start=2.0, end=2.0, description="x")
    assert e.end == 2.0


def test_evidence_rejects_unknown_video() -> None:
    with pytest.raises(ValidationError):
        TimestampedEvidence(video="unknown", start=0.0, end=1.0, description="x")  # type: ignore[arg-type]


def test_evidence_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        TimestampedEvidence(  # type: ignore[call-arg]
            video="base", start=0.0, end=1.0, description="x", extra_key="oops"
        )


# ---------------------------------------------------------------------------
# BranchImpact.
# ---------------------------------------------------------------------------


def test_branch_impact_requires_rationale() -> None:
    with pytest.raises(ValidationError):
        BranchImpact(
            branch="branch_a",
            impact_level=ImpactLevel.PRESERVED,
            confidence=0.9,
            rationale="",
        )


def test_branch_impact_rejects_unknown_branch() -> None:
    with pytest.raises(ValidationError):
        BranchImpact(
            branch="branch_z",  # type: ignore[arg-type]
            impact_level=ImpactLevel.PRESERVED,
            confidence=0.9,
            rationale="x",
        )


def test_branch_impact_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        BranchImpact(
            branch="branch_a",
            impact_level=ImpactLevel.PRESERVED,
            confidence=1.5,
            rationale="x",
        )
    with pytest.raises(ValidationError):
        BranchImpact(
            branch="branch_a",
            impact_level=ImpactLevel.PRESERVED,
            confidence=-0.1,
            rationale="x",
        )


# ---------------------------------------------------------------------------
# CrossEditInteraction_.
# ---------------------------------------------------------------------------


def test_interaction_conflict_type_must_be_known_or_null() -> None:
    # None is allowed (for "none" interactions).
    inter = _interaction(
        interaction_type=CrossEditInteraction.NONE,
        conflict_type=None,
    )
    assert inter.conflict_type is None
    # String outside the literal set is rejected.
    with pytest.raises(ValidationError):
        _interaction(conflict_type="invented_type")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SemanticAnalysisV2.
# ---------------------------------------------------------------------------


def test_v2_requires_at_least_one_interaction() -> None:
    with pytest.raises(ValidationError) as exc:
        SemanticAnalysisV2(
            branch_a_impact=_branch_impact("branch_a", ImpactLevel.PRESERVED),
            branch_b_impact=_branch_impact("branch_b", ImpactLevel.PRESERVED),
            combined_impact=ImpactLevel.PRESERVED,
            interactions=[],
            overall_confidence=0.9,
        )
    assert "at least one CrossEditInteraction" in str(exc.value)


def test_v2_rejects_extra_top_level_keys() -> None:
    with pytest.raises(ValidationError):
        SemanticAnalysisV2.model_validate(
            {
                "branch_a_impact": _branch_impact("branch_a", ImpactLevel.PRESERVED).model_dump(),
                "branch_b_impact": _branch_impact("branch_b", ImpactLevel.PRESERVED).model_dump(),
                "combined_impact": "preserved",
                "interactions": [
                    _interaction(
                        interaction_type=CrossEditInteraction.NONE, conflict_type=None
                    ).model_dump()
                ],
                "overall_confidence": 0.9,
                "smuggled_key": "x",
            }
        )


def test_v2_full_canonical() -> None:
    """The canonical MergeCut case builds end-to-end."""
    result = _full_v2()
    assert result.branch_a_impact.impact_level == ImpactLevel.PRESERVED
    assert result.branch_b_impact.impact_level == ImpactLevel.PRESERVED
    assert result.combined_impact == ImpactLevel.BROKEN
    assert result.interactions[0].interaction_type == CrossEditInteraction.CREATES_NEW_CONFLICT


def test_v2_legacy_field_optional() -> None:
    result = _full_v2()
    assert result.legacy_v1_compat is None


# ---------------------------------------------------------------------------
# Legacy projection.
# ---------------------------------------------------------------------------


def test_to_legacy_v1_all_preserved() -> None:
    """When every axis is preserved, all three v1 booleans are true."""
    v2 = SemanticAnalysisV2(
        branch_a_impact=_branch_impact("branch_a", ImpactLevel.PRESERVED),
        branch_b_impact=_branch_impact("branch_b", ImpactLevel.PRESERVED),
        combined_impact=ImpactLevel.PRESERVED,
        interactions=[
            _interaction(
                combined=ImpactLevel.PRESERVED,
                interaction_type=CrossEditInteraction.NONE,
                conflict_type=None,
            )
        ],
        overall_confidence=0.9,
    )
    legacy = to_legacy_v1(v2)
    assert legacy.branch_a_safe is True
    assert legacy.branch_b_safe is True
    assert legacy.combined_safe is True


def test_to_legacy_v1_canonical() -> None:
    """Canonical MergeCut: branches safe, combined broken."""
    v2 = _full_v2()  # branches preserved, combined broken
    legacy = to_legacy_v1(v2)
    assert legacy.branch_a_safe is True
    assert legacy.branch_b_safe is True
    assert legacy.combined_safe is False  # the v1 axis the v1 model collapsed


def test_to_legacy_v1_one_branch_broken() -> None:
    v2 = SemanticAnalysisV2(
        branch_a_impact=_branch_impact("branch_a", ImpactLevel.BROKEN),
        branch_b_impact=_branch_impact("branch_b", ImpactLevel.PRESERVED),
        combined_impact=ImpactLevel.BROKEN,
        interactions=[
            _interaction(
                combined=ImpactLevel.BROKEN,
                interaction_type=CrossEditInteraction.AMPLIFIES_EXISTING_ISSUE,
                conflict_type="other",
            )
        ],
        overall_confidence=0.9,
    )
    legacy = to_legacy_v1(v2)
    assert legacy.branch_a_safe is False
    assert legacy.branch_b_safe is True
    assert legacy.combined_safe is False


def test_to_legacy_v1_degraded_treated_as_not_safe() -> None:
    """v1's `safe` axis is binary. A `degraded` v2 branch
    projects to `safe=False` in v1 — strictly correct because
    v1 had no degraded level."""
    v2 = SemanticAnalysisV2(
        branch_a_impact=_branch_impact("branch_a", ImpactLevel.DEGRADED),
        branch_b_impact=_branch_impact("branch_b", ImpactLevel.PRESERVED),
        combined_impact=ImpactLevel.DEGRADED,
        interactions=[
            _interaction(
                combined=ImpactLevel.DEGRADED,
                interaction_type=CrossEditInteraction.AMPLIFIES_EXISTING_ISSUE,
                conflict_type="qualifier_loss",
            )
        ],
        overall_confidence=0.7,
    )
    legacy = to_legacy_v1(v2)
    assert legacy.branch_a_safe is False
    assert legacy.branch_b_safe is True
    assert legacy.combined_safe is False
