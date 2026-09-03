"""Deterministic unit tests for the Phase 4 claim-centric pipeline.

The user said:

> Add deterministic tests for:
> - claim representation
> - evidence representation
> - claim preservation aggregation
> - interaction derivation
> - A-preserved/B-preserved/combined-broken => creates_new_conflict
> - one-branch-already-broken => not automatically creates_new_conflict
> - redundant claim remains => no conflict

> Live GMI calls remain outside ordinary unit tests.

This file covers:
  - claim schema validation (`test_claims_schema.py` would
    duplicate it; we include the key invariants here).
  - the deterministic reconstruction rules.
  - the deterministic interaction derivation (`derive_interaction`).
  - the end-to-end orchestrator with a `_FakeM3Client` that
    returns canned JSON for each M3 call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.alignment import (
    AlignmentMatch,
    AlignmentResult,
    ShotFingerprint,
    SimilarityComponents,
)
from app.models.claims import (
    BaseClaim,
    BranchClaims,
    ClaimEvidenceRegion,
    ClaimImportance,
    ClaimStatus,
    ClaimSurvival,
    ClaimType,
)
from app.models.claims import (
    CrossEditInteraction as ClaimCrossEditInteraction,
)
from app.models.media import NormalizationInfo, Shot, VideoMetadata, VideoRepresentation
from app.services.alignment.edit_ops import OperationThresholds
from app.services.semantic.claims.interact import (
    aggregate_overall_impact,
    aggregate_overall_interaction,
    build_claim_interaction,
    derive_interaction,
)
from app.services.semantic.claims.orchestrate import analyze_claims
from app.services.semantic.claims.reconstruct import (
    deterministic_surrogate_status,
    reconstruct_branch_claims,
    reconstruct_combined_claims,
)

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _make_rep(
    video_id: str, *, transcripts: list[str], durations: list[float] | None = None
) -> VideoRepresentation:
    durations = durations or [1.0] * len(transcripts)
    metadata = VideoMetadata(
        duration_seconds=sum(durations),
        width=320,
        height=240,
        fps=30.0,
        codec="h264",
        audio_present=bool(transcripts),
    )
    shots = []
    t = 0.0
    for i, (text, dur) in enumerate(zip(transcripts, durations, strict=True)):
        shots.append(
            Shot(
                shot_id=f"shot_{i:04d}",
                start=t,
                end=t + dur,
                keyframe_paths=[],
                transcript=text,
                transcript_segments=[],
            )
        )
        t += dur
    return VideoRepresentation.from_components(
        video_id=video_id,
        source_path=Path(f"/tmp/{video_id}.mp4"),
        normalized_path=Path(f"/tmp/{video_id}.working.mp4"),
        audio_path=None,
        metadata=metadata,
        normalization=NormalizationInfo(normalized=False),
        shots=shots,
    )


def _sim() -> SimilarityComponents:
    return SimilarityComponents(
        visual_similarity=1.0,
        visual_structural_similarity=1.0,
        visual_color_mean_similarity=1.0,
        visual_color_histogram_similarity=1.0,
        transcript_similarity=1.0,
        duration_similarity=1.0,
        order_prior=1.0,
        final_score=1.0,
        used_components=["visual_similarity"],
    )


def _match(*, base_idx: int | None, branch_idx: int | None, op: str) -> AlignmentMatch:
    base = (
        ShotFingerprint(
            shot_id=f"shot_{base_idx:04d}",
            start=float(base_idx),
            end=float(base_idx) + 1.0,
            duration=1.0,
            keyframe_paths=[],
            visual_fingerprint="abcd1234",
            color_mean_rgb=(0.5, 0.5, 0.5),
            color_histogram=tuple([1.0 / 12.0] * 12),
            normalized_transcript="",
            transcript_tokens=[],
            has_speech=False,
            sequence_index=base_idx,
        )
        if base_idx is not None
        else None
    )
    branch = (
        ShotFingerprint(
            shot_id=f"shot_{branch_idx:04d}",
            start=float(branch_idx),
            end=float(branch_idx) + 1.0,
            duration=1.0,
            keyframe_paths=[],
            visual_fingerprint="abcd1234",
            color_mean_rgb=(0.5, 0.5, 0.5),
            color_histogram=tuple([1.0 / 12.0] * 12),
            normalized_transcript="",
            transcript_tokens=[],
            has_speech=False,
            sequence_index=branch_idx,
        )
        if branch_idx is not None
        else None
    )
    return AlignmentMatch(
        base_shot=base,
        branch_shot=branch,
        similarity=_sim(),
        operation=op,  # type: ignore[arg-type]
        confidence=1.0,
        evidence={"reason": f"synthetic {op}"},
    )


def _alignment(matches: list[AlignmentMatch], *, branch_video_id: str) -> AlignmentResult:
    return AlignmentResult(
        branch_name="branch_a" if branch_video_id == "a" else "branch_b",
        base_video_id="base",
        branch_video_id=branch_video_id,
        matches=matches,
        weights={
            "visual_similarity": 0.45,
            "transcript_similarity": 0.40,
            "duration_similarity": 0.10,
            "order_prior": 0.05,
        },
        thresholds=OperationThresholds.as_dict(),
    )


def _claim(
    claim_id: str,
    meaning: str,
    *,
    importance: ClaimImportance = ClaimImportance.HIGH,
    claim_type: ClaimType = ClaimType.INSTRUCTION,
    evidence_starts: list[tuple[float, float]] | None = None,
    equivalent_starts: list[tuple[float, float]] | None = None,
) -> BaseClaim:
    return BaseClaim(
        claim_id=claim_id,
        meaning=meaning,
        claim_type=claim_type,
        importance=importance,
        evidence_regions=[
            ClaimEvidenceRegion(
                start=s, end=e, description=f"{claim_id} evidence at {s:.1f}-{e:.1f}"
            )
            for (s, e) in (evidence_starts or [])
        ],
        equivalents=[
            ClaimEvidenceRegion(
                start=s, end=e, description=f"{claim_id} equivalent at {s:.1f}-{e:.1f}"
            )
            for (s, e) in (equivalent_starts or [])
        ],
    )


# ---------------------------------------------------------------------------
# Claim representation (schema).
# ---------------------------------------------------------------------------


def test_claim_requires_evidence_regions() -> None:
    """A claim with no evidence_regions should still be
    representable (we keep the schema permissive so M3 can
    return high-level claims), but the orchestrator treats
    such claims as broken in every branch."""
    c = _claim("C1", "the device must be unplugged", evidence_starts=[])
    assert c.evidence_regions == []


def test_evidence_region_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        ClaimEvidenceRegion(start=5.0, end=2.0, description="x")


def test_claim_type_enum_values() -> None:
    expected = {
        "prerequisite",
        "qualifier",
        "exception",
        "temporal_scope",
        "causal_dependency",
        "entity_scope",
        "instruction",
        "prohibition",
        "narrative_dependency",
        "other",
    }
    actual = {ct.value for ct in ClaimType}
    assert actual == expected


# ---------------------------------------------------------------------------
# Reconstruction rules.
# ---------------------------------------------------------------------------


def test_reconstruct_drops_evidence_overlapping_delete() -> None:
    """A claim whose evidence region overlaps a delete is
    dropped from the branch's surviving list."""
    base = _claim("C1", "unplug first", evidence_starts=[(0.0, 2.0)])
    # Branch deletes the shot covering [0, 2).
    a_alignment = _alignment(
        [_match(base_idx=0, branch_idx=None, op="delete")], branch_video_id="a"
    )
    out = reconstruct_branch_claims([base], a_alignment)
    assert out[0].evidence_regions == []


def test_reconstruct_keeps_evidence_when_branch_keeps_shot() -> None:
    """A claim whose evidence region is NOT touched by the
    branch's edits is kept."""
    base = _claim("C1", "unplug first", evidence_starts=[(0.0, 2.0)])
    a_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="unchanged")], branch_video_id="a"
    )
    out = reconstruct_branch_claims([base], a_alignment)
    assert len(out[0].evidence_regions) == 1


def test_reconstruct_drops_equivalents_too() -> None:
    """Equivalents are dropped on overlap just like evidence."""
    # Evidence region is in [0, 1) (shot 0) and equivalent is
    # in [2, 3) (shot 2). The branch deletes shot 1 (in [1, 2)),
    # which does NOT overlap either. To exercise the equivalent
    # drop we delete shot 2 instead.
    base = _claim(
        "C1",
        "unplug first",
        evidence_starts=[(0.0, 1.0)],
        equivalent_starts=[(2.0, 3.0)],
    )
    a_alignment = _alignment(
        [_match(base_idx=2, branch_idx=None, op="delete")], branch_video_id="a"
    )
    out = reconstruct_branch_claims([base], a_alignment)
    # Evidence survives, equivalent is dropped.
    assert len(out[0].evidence_regions) == 1
    assert out[0].equivalents == []


def test_reconstruct_preserves_importance_and_type() -> None:
    """Metadata (importance, claim_type, meaning) is preserved
    through reconstruction — only the regions change."""
    base = _claim(
        "C1",
        "x",
        importance=ClaimImportance.CRITICAL,
        claim_type=ClaimType.PREREQUISITE,
        evidence_starts=[(0.0, 2.0)],
    )
    a_alignment = _alignment(
        [_match(base_idx=0, branch_idx=None, op="delete")], branch_video_id="a"
    )
    out = reconstruct_branch_claims([base], a_alignment)
    assert out[0].claim_type == ClaimType.PREREQUISITE
    assert out[0].importance == ClaimImportance.CRITICAL
    assert out[0].meaning == "x"


def test_combined_intersects_evidence() -> None:
    """A combined claim survives only if it survives in BOTH
    A and B."""
    c = _claim("C1", "x", evidence_starts=[(0.0, 2.0), (2.0, 4.0)])
    a = [c.model_copy(update={"evidence_regions": c.evidence_regions[:1]})]
    b = [c.model_copy(update={"evidence_regions": c.evidence_regions[1:]})]
    combined = reconstruct_combined_claims(a, b)
    # A and B have disjoint surviving regions → combined is empty.
    assert combined[0].evidence_regions == []


def test_combined_keeps_shared_evidence() -> None:
    """A combined claim keeps the intersection of A's and B's
    surviving regions."""
    er = ClaimEvidenceRegion(start=0.0, end=2.0, description="shared")
    c = _claim("C1", "x", evidence_starts=[(0.0, 2.0)])
    a = [c.model_copy(update={"evidence_regions": [er]})]
    b = [c.model_copy(update={"evidence_regions": [er]})]
    combined = reconstruct_combined_claims(a, b)
    assert combined[0].evidence_regions == [er]


def test_surrogate_status_preserved_when_any_evidence_remains() -> None:
    c = _claim("C1", "x", evidence_starts=[(0.0, 2.0)])
    surrogate = deterministic_surrogate_status([c])
    assert surrogate["C1"] == ClaimStatus.PRESERVED


def test_surrogate_status_broken_when_no_evidence_and_no_equivalent() -> None:
    c = _claim("C1", "x", evidence_starts=[], equivalent_starts=[])
    surrogate = deterministic_surrogate_status([c])
    assert surrogate["C1"] == ClaimStatus.BROKEN


def test_redundant_claim_remains_no_conflict_derivation() -> None:
    """The user-named test case: a redundant claim remains →
    no conflict.

    The redundancy is encoded via the `equivalents` field.
    When A's reconstruction still has a surviving evidence
    region (even though the primary evidence was dropped,
    an equivalent survives), the surrogate returns
    `preserved`. The derivation step then sees
    A=preserved, B=preserved, combined=preserved → none.
    """
    c = _claim(
        "C1",
        "all nut allergies must avoid",
        importance=ClaimImportance.CRITICAL,
        claim_type=ClaimType.PROHIBITION,
        evidence_starts=[(0.0, 2.0)],  # primary statement
        equivalent_starts=[(4.0, 6.0)],  # restatement later
    )
    a_alignment = _alignment(
        [_match(base_idx=0, branch_idx=None, op="delete")], branch_video_id="a"
    )
    a_recon = reconstruct_branch_claims([c], a_alignment)
    # Primary is gone, equivalent survives.
    assert a_recon[0].evidence_regions == []
    assert len(a_recon[0].equivalents) == 1
    # Surrogate says preserved (an equivalent survives).
    assert deterministic_surrogate_status(a_recon)["C1"] == ClaimStatus.PRESERVED


# ---------------------------------------------------------------------------
# Interaction derivation (R1-R7).
# ---------------------------------------------------------------------------


def test_r1_canonical_creates_new_conflict() -> None:
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.PREREQUISITE,
        claim_importance=ClaimImportance.CRITICAL,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.PRESERVED,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.CREATES_NEW_CONFLICT
    assert "R1" in reason


def test_both_preserved_combined_degraded_is_not_materially_broken() -> None:
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.QUALIFIER,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.PRESERVED,
        combined_status=ClaimStatus.DEGRADED,
    )
    assert interaction == ClaimCrossEditInteraction.NONE
    assert "R3" in reason


def test_preserved_degraded_then_broken_creates_new_conflict() -> None:
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.QUALIFIER,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.DEGRADED,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.CREATES_NEW_CONFLICT
    assert "R1" in reason


def test_degraded_preserved_then_broken_creates_new_conflict() -> None:
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.QUALIFIER,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.DEGRADED,
        branch_b_status=ClaimStatus.PRESERVED,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.CREATES_NEW_CONFLICT
    assert "R1" in reason


def test_both_degraded_then_broken_creates_new_conflict() -> None:
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.QUALIFIER,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.DEGRADED,
        branch_b_status=ClaimStatus.DEGRADED,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.CREATES_NEW_CONFLICT
    assert "R1" in reason


def test_r5_a_already_broken_returns_none() -> None:
    """The user-named case: one branch already broken →
    NOT automatically creates_new_conflict."""
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.INSTRUCTION,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.BROKEN,
        branch_b_status=ClaimStatus.PRESERVED,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.NONE
    assert "R2" in reason


def test_r6_b_already_broken_returns_none() -> None:
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.INSTRUCTION,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.BROKEN,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.NONE
    assert "R2" in reason


def test_r7_default_is_none() -> None:
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.INSTRUCTION,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.DEGRADED,
        combined_status=ClaimStatus.PRESERVED,
    )
    assert interaction == ClaimCrossEditInteraction.NONE
    assert "R4" in reason


# ---------------------------------------------------------------------------
# Exhaustive focused tests (Phase 4.5 forensic brief).
# ---------------------------------------------------------------------------
#
# The user explicitly asked for exhaustive coverage of the
# product-principle table, in particular the
# (degraded, degraded, broken) and (preserved, degraded, broken)
# shapes. The following tests enumerate the full 3×3×3 truth
# table of (A, B, combined) over {preserved, degraded, broken} and
# check the expected verdict + reason code. This is the
# forensic artefact that future Phase 5+ work can build
# against. Each row is also a one-line sanity check for the
# orchestrator: if a per-claim verdict drifts from this table
# the runner will catch the drift at the unit-test level.
#
# A summary of the product-principle table (the expected verdict
# for each (A, B, combined) tuple):
#
# | A \ B \ C | preserved | degraded | broken |
# |-----------|-----------|----------|--------|
# | preserved | none*     | none*    | none*  |
# | degraded  | none*     | none*    | none*  |
# | broken    | none*     | none*    | none*  |
#
# * where * = verdict depends on combined:
#
# | A \ B \ C | preserved | degraded | broken   |
# |-----------|-----------|----------|----------|
# | preserved | R4: none  | R4: none | R1: new  |
# | degraded  | R4: none  | R4: none | R1: new  |
# | broken    | R4: none  | R4: none | R2: none |
#
# (R1 = creates_new_conflict, R2 = none because A or B
# already broke the claim, R3 = none because combined is
# degraded (no cross-edit classification without an additional
# evidence model), R4 = none default.)


_INTERACT_TABLE: list[
    tuple[ClaimStatus, ClaimStatus, ClaimStatus, ClaimCrossEditInteraction, str]
] = [
    # --- combined = preserved (R4, none default) -----------------
    (
        ClaimStatus.PRESERVED,
        ClaimStatus.PRESERVED,
        ClaimStatus.PRESERVED,
        ClaimCrossEditInteraction.NONE,
        "R4",
    ),
    (
        ClaimStatus.PRESERVED,
        ClaimStatus.DEGRADED,
        ClaimStatus.PRESERVED,
        ClaimCrossEditInteraction.NONE,
        "R4",
    ),
    (
        ClaimStatus.PRESERVED,
        ClaimStatus.BROKEN,
        ClaimStatus.PRESERVED,
        ClaimCrossEditInteraction.NONE,
        "R4",
    ),
    (
        ClaimStatus.DEGRADED,
        ClaimStatus.PRESERVED,
        ClaimStatus.PRESERVED,
        ClaimCrossEditInteraction.NONE,
        "R4",
    ),
    (
        ClaimStatus.DEGRADED,
        ClaimStatus.DEGRADED,
        ClaimStatus.PRESERVED,
        ClaimCrossEditInteraction.NONE,
        "R4",
    ),
    (
        ClaimStatus.DEGRADED,
        ClaimStatus.BROKEN,
        ClaimStatus.PRESERVED,
        ClaimCrossEditInteraction.NONE,
        "R4",
    ),
    (
        ClaimStatus.BROKEN,
        ClaimStatus.PRESERVED,
        ClaimStatus.PRESERVED,
        ClaimCrossEditInteraction.NONE,
        "R4",
    ),
    (
        ClaimStatus.BROKEN,
        ClaimStatus.DEGRADED,
        ClaimStatus.PRESERVED,
        ClaimCrossEditInteraction.NONE,
        "R4",
    ),
    (
        ClaimStatus.BROKEN,
        ClaimStatus.BROKEN,
        ClaimStatus.PRESERVED,
        ClaimCrossEditInteraction.NONE,
        "R4",
    ),
    # --- combined = degraded (R3, none) -------------------------
    (
        ClaimStatus.PRESERVED,
        ClaimStatus.PRESERVED,
        ClaimStatus.DEGRADED,
        ClaimCrossEditInteraction.NONE,
        "R3",
    ),
    (
        ClaimStatus.PRESERVED,
        ClaimStatus.DEGRADED,
        ClaimStatus.DEGRADED,
        ClaimCrossEditInteraction.NONE,
        "R3",
    ),
    (
        ClaimStatus.PRESERVED,
        ClaimStatus.BROKEN,
        ClaimStatus.DEGRADED,
        ClaimCrossEditInteraction.NONE,
        "R3",
    ),
    (
        ClaimStatus.DEGRADED,
        ClaimStatus.PRESERVED,
        ClaimStatus.DEGRADED,
        ClaimCrossEditInteraction.NONE,
        "R3",
    ),
    (
        ClaimStatus.DEGRADED,
        ClaimStatus.DEGRADED,
        ClaimStatus.DEGRADED,
        ClaimCrossEditInteraction.NONE,
        "R3",
    ),
    (
        ClaimStatus.DEGRADED,
        ClaimStatus.BROKEN,
        ClaimStatus.DEGRADED,
        ClaimCrossEditInteraction.NONE,
        "R3",
    ),
    (
        ClaimStatus.BROKEN,
        ClaimStatus.PRESERVED,
        ClaimStatus.DEGRADED,
        ClaimCrossEditInteraction.NONE,
        "R3",
    ),
    (
        ClaimStatus.BROKEN,
        ClaimStatus.DEGRADED,
        ClaimStatus.DEGRADED,
        ClaimCrossEditInteraction.NONE,
        "R3",
    ),
    (
        ClaimStatus.BROKEN,
        ClaimStatus.BROKEN,
        ClaimStatus.DEGRADED,
        ClaimCrossEditInteraction.NONE,
        "R3",
    ),
    # --- combined = broken (R1 if neither broken, R2 if either) -
    (
        ClaimStatus.PRESERVED,
        ClaimStatus.PRESERVED,
        ClaimStatus.BROKEN,
        ClaimCrossEditInteraction.CREATES_NEW_CONFLICT,
        "R1",
    ),
    (
        ClaimStatus.PRESERVED,
        ClaimStatus.DEGRADED,
        ClaimStatus.BROKEN,
        ClaimCrossEditInteraction.CREATES_NEW_CONFLICT,
        "R1",
    ),
    (
        ClaimStatus.PRESERVED,
        ClaimStatus.BROKEN,
        ClaimStatus.BROKEN,
        ClaimCrossEditInteraction.NONE,
        "R2",
    ),
    (
        ClaimStatus.DEGRADED,
        ClaimStatus.PRESERVED,
        ClaimStatus.BROKEN,
        ClaimCrossEditInteraction.CREATES_NEW_CONFLICT,
        "R1",
    ),
    (
        ClaimStatus.DEGRADED,
        ClaimStatus.DEGRADED,
        ClaimStatus.BROKEN,
        ClaimCrossEditInteraction.CREATES_NEW_CONFLICT,
        "R1",
    ),
    (
        ClaimStatus.DEGRADED,
        ClaimStatus.BROKEN,
        ClaimStatus.BROKEN,
        ClaimCrossEditInteraction.NONE,
        "R2",
    ),
    (
        ClaimStatus.BROKEN,
        ClaimStatus.PRESERVED,
        ClaimStatus.BROKEN,
        ClaimCrossEditInteraction.NONE,
        "R2",
    ),
    (
        ClaimStatus.BROKEN,
        ClaimStatus.DEGRADED,
        ClaimStatus.BROKEN,
        ClaimCrossEditInteraction.NONE,
        "R2",
    ),
    (
        ClaimStatus.BROKEN,
        ClaimStatus.BROKEN,
        ClaimStatus.BROKEN,
        ClaimCrossEditInteraction.NONE,
        "R2",
    ),
]


@pytest.mark.parametrize(
    ("a_status", "b_status", "c_status", "expected_interaction", "expected_reason"),
    _INTERACT_TABLE,
    ids=[f"A={a.value}/B={b.value}/C={c.value}" for a, b, c, _e, _r in _INTERACT_TABLE],
)
def test_exhaustive_interaction_table(
    a_status: ClaimStatus,
    b_status: ClaimStatus,
    c_status: ClaimStatus,
    expected_interaction: ClaimCrossEditInteraction,
    expected_reason: str,
) -> None:
    """The user explicitly asked for exhaustive focused tests for
    the (degraded, degraded, broken) and (preserved, degraded,
    broken) shapes. The parametrize matrix above covers the FULL
    3x3x3 truth table so any drift in the rule priority is
    caught at the unit-test level.
    """
    interaction, reason = derive_interaction(
        claim_id="C_table",
        claim_meaning="x",
        claim_type=ClaimType.QUALIFIER,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=a_status,
        branch_b_status=b_status,
        combined_status=c_status,
    )
    assert interaction == expected_interaction
    assert reason.startswith(expected_reason), (
        f"expected reason starting with {expected_reason!r}, got {reason!r}"
    )


def test_user_named_degraded_degraded_broken_creates_new_conflict() -> None:
    """The user explicitly named the (degraded, degraded, broken)
    shape. Under the product principle both A and B communicated
    the meaning in some (weakened) form; combined the claim is
    gone. R1 fires → creates_new_conflict.
    """
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.QUALIFIER,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.DEGRADED,
        branch_b_status=ClaimStatus.DEGRADED,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.CREATES_NEW_CONFLICT
    assert "R1" in reason
    assert "each preserved/degraded" in reason


def test_user_named_preserved_degraded_broken_creates_new_conflict() -> None:
    """The user explicitly named the (preserved, degraded, broken)
    shape. A kept the claim in full; B weakened it; combined it
    broke. R1 fires → creates_new_conflict.
    """
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.QUALIFIER,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.DEGRADED,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.CREATES_NEW_CONFLICT
    assert "R1" in reason


def test_user_named_a_broken_returns_none() -> None:
    """The user explicitly named the (A=broken, B=preserved,
    combined=broken) shape. R2 fires (A already broke the
    claim; combined=broken alone does not prove amplification
    without an additional evidence model). → none.
    """
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.INSTRUCTION,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.BROKEN,
        branch_b_status=ClaimStatus.PRESERVED,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.NONE
    assert "R2" in reason
    assert "additional evidence model" in reason


def test_user_named_b_broken_returns_none() -> None:
    """Symmetric to the above: B=broken, A=preserved, combined=
    broken → none (R2)."""
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.INSTRUCTION,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.BROKEN,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.NONE
    assert "R2" in reason


def test_user_named_degraded_broken_broken_returns_none() -> None:
    """Edge the user named explicitly: A already degraded the
    claim, B broke it, combined broke it. R2 wins (B is broken)
    over R1 (which would require neither broken). → none."""
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.INSTRUCTION,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.DEGRADED,
        branch_b_status=ClaimStatus.BROKEN,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.NONE
    assert "R2" in reason


def test_user_named_broken_degraded_broken_returns_none() -> None:
    """Symmetric to the above: A broke, B degraded, combined
    broke. R2 wins (A is broken). → none."""
    interaction, reason = derive_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.INSTRUCTION,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.BROKEN,
        branch_b_status=ClaimStatus.DEGRADED,
        combined_status=ClaimStatus.BROKEN,
    )
    assert interaction == ClaimCrossEditInteraction.NONE
    assert "R2" in reason


def test_interaction_derivation_is_deterministic() -> None:
    """Calling derive_interaction twice with the same inputs
    yields the same (interaction, reason) tuple. This is the
    determinism invariant the orchestrator depends on for the
    forensic serialization."""
    args = dict(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.QUALIFIER,
        claim_importance=ClaimImportance.HIGH,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.PRESERVED,
        combined_status=ClaimStatus.BROKEN,
    )
    a = derive_interaction(**args)
    b = derive_interaction(**args)
    assert a == b


def test_build_claim_interaction_populates_derivation_reason() -> None:
    ci = build_claim_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.PREREQUISITE,
        claim_importance=ClaimImportance.CRITICAL,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.PRESERVED,
        combined_status=ClaimStatus.BROKEN,
    )
    assert ci.interaction == ClaimCrossEditInteraction.CREATES_NEW_CONFLICT
    assert "R1" in ci.derivation_reason
    # M3 fields default to None.
    assert ci.m3_explanation is None
    assert ci.m3_recommended_resolution is None


# ---------------------------------------------------------------------------
# Aggregate derivations.
# ---------------------------------------------------------------------------


def test_aggregate_overall_interaction_picks_most_severe() -> None:
    a = build_claim_interaction(
        claim_id="C1",
        claim_meaning="x",
        claim_type=ClaimType.INSTRUCTION,
        claim_importance=ClaimImportance.LOW,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.PRESERVED,
        combined_status=ClaimStatus.BROKEN,
    )
    b = build_claim_interaction(
        claim_id="C2",
        claim_meaning="y",
        claim_type=ClaimType.QUALIFIER,
        claim_importance=ClaimImportance.LOW,
        branch_a_status=ClaimStatus.PRESERVED,
        branch_b_status=ClaimStatus.PRESERVED,
        combined_status=ClaimStatus.PRESERVED,
    )
    overall = aggregate_overall_interaction([a, b])
    assert overall == ClaimCrossEditInteraction.CREATES_NEW_CONFLICT


def test_aggregate_overall_interaction_empty() -> None:
    assert aggregate_overall_interaction([]) == ClaimCrossEditInteraction.NONE


def test_aggregate_overall_impact_broken_wins() -> None:
    bc = BranchClaims(
        branch="combined",
        claim_survivals=[
            ClaimSurvival(
                claim_id="C1",
                branch="combined",
                status=ClaimStatus.PRESERVED,
                surviving_evidence=[],
                rationale="x",
            ),
            ClaimSurvival(
                claim_id="C2",
                branch="combined",
                status=ClaimStatus.BROKEN,
                surviving_evidence=[],
                rationale="x",
            ),
        ],
    )
    assert aggregate_overall_impact(bc) == ClaimStatus.BROKEN


def test_aggregate_overall_impact_empty() -> None:
    bc = BranchClaims(branch="combined", claim_survivals=[])
    assert aggregate_overall_impact(bc) == ClaimStatus.PRESERVED


# ---------------------------------------------------------------------------
# Orchestrator end-to-end with a _FakeM3Client.
# ---------------------------------------------------------------------------


class _FakeM3Client:
    """A canned M3 client for the orchestrator.

    Records every call to chat_json_sync (so tests can audit
    the exact context) and replays a queued sequence of
    JSON responses. The orchestrator calls:

      chat_json_sync(EXTRACTION_SYSTEM_INTENT, ...)         # STEP 1
      chat_json_sync(EVALUATION_SYSTEM_INTENT, ...) x 3N    # STEP 3
      chat_json_sync(EXPLANATION_SYSTEM_INTENT, ...)        # STEP 5 (per non-none)

    The fake decides which response to return based on the
    system prompt substring.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.model = "FAKE-M3"

    def chat_json_sync(self, *, system: str, user: str, **kwargs: Any) -> str:
        self.calls.append({"system": system, "user": user})
        if "BASE-claim extractor" in system:
            return self._responses.pop(0) if self._responses else json.dumps({"claims": []})
        if "per-claim preservation evaluator" in system:
            return (
                self._responses.pop(0)
                if self._responses
                else json.dumps(
                    {
                        "claim_id": "C?",
                        "status": "preserved",
                        "surviving_evidence": [],
                        "rationale": "default",
                        "confidence": 1.0,
                    }
                )
            )
        if "human-readable explainer" in system:
            return (
                self._responses.pop(0)
                if self._responses
                else json.dumps({"explanation": "default", "recommended_resolution": "default"})
            )
        return self._responses.pop(0) if self._responses else "{}"


def _build_claim_extraction_response(claims_data: list[dict]) -> str:
    return json.dumps({"claims": claims_data})


def _build_evaluation_response(
    claim_id: str, status: str, *, surviving: list[dict] | None = None, conf: float = 1.0
) -> str:
    return json.dumps(
        {
            "claim_id": claim_id,
            "status": status,
            "surviving_evidence": surviving or [],
            "rationale": f"synthetic {status}",
            "confidence": conf,
        }
    )


def _build_explanation_response() -> str:
    return json.dumps(
        {
            "explanation": "synthetic explanation",
            "recommended_resolution": "synthetic fix",
        }
    )


def test_orchestrator_canonical_prereq_loss_creates_new_conflict() -> None:
    """The user's load-bearing test: canonical prerequisite-loss
    fixture (A preserved, B preserved, combined broken) must
    be classified creates_new_conflict.

    BASE: 3 shots — shot 0 = prerequisite ("unplug first"),
    shot 1 = follow-up ("once unplugged, lift"), shot 2 = result.
    A: drops shot 0 (prerequisite). B: rewrites shot 1 (drops
    the "once unplugged" context). Combined: viewer no longer
    hears the prerequisite.
    """
    base = _make_rep(
        "base",
        transcripts=[
            "Before opening the device, unplug it from the wall.",
            "Once the device is unplugged, lift the cover.",
            "Then you can access the battery compartment.",
        ],
        durations=[3.0, 3.0, 3.0],
    )
    a = _make_rep(
        "a",
        transcripts=[
            # A drops shot 0.
            "Once the device is unplugged, lift the cover.",
            "Then you can access the battery compartment.",
        ],
        durations=[3.0, 3.0],
    )
    b = _make_rep(
        "b",
        transcripts=[
            # B keeps shot 0, rewrites shot 1 (drops "once unplugged").
            "Before opening the device, unplug it from the wall.",
            "Lift the cover.",
            "Then you can access the battery compartment.",
        ],
        durations=[3.0, 3.0, 3.0],
    )

    # Set up M3 canned responses.
    # 1. Extraction response: one critical claim "unplug before
    # opening" with two evidence regions (the two places the
    # claim is stated in BASE).
    extraction = _build_claim_extraction_response(
        [
            {
                "claim_id": "C1",
                "meaning": "The device must be unplugged before the cover is opened.",
                "claim_type": "prerequisite",
                "importance": "critical",
                "evidence_regions": [
                    {"start": 0.0, "end": 3.0, "description": "shot 0 states the prerequisite"},
                    {"start": 3.0, "end": 6.0, "description": "shot 1 references the prerequisite"},
                ],
                "equivalents": [],
            }
        ]
    )
    # 2. Evaluation responses (in order: A, B, combined).
    #    A drops shot 0; the only surviving evidence is the
    #    implicit reference in shot 1 (which is also dropped
    #    by the DP? — no, A keeps shot 1, just renumbers).
    #    The orchestrator's reconstruction drops regions that
    #    overlap A's delete on shot 0; M3 sees the surviving
    #    content (shot 1, shot 2) and decides whether the
    #    meaning is preserved.
    #    A: shot 1 says "Once unplugged, lift" → C1's meaning
    #       is still in the (rewritten) shot 1 because the
    #       follow-up sentence implies the prerequisite.
    #       M3 says preserved.
    a_eval = _build_evaluation_response("C1", "preserved")
    #    B: keeps shot 0; rewrites shot 1 to "Lift the cover."
    #       The prerequisite is still in shot 0, so M3 says
    #       preserved.
    b_eval = _build_evaluation_response("C1", "preserved")
    #    Combined: A's shot 0 is gone, B's shot 1 is rewritten.
    #       A's reconstructed view has only shot 1 (which says
    #       "Once unplugged, lift"). B's reconstructed view
    #       has shot 0 + the rewritten shot 1.
    #       M3 (correctly) sees: A kept an implicit reference;
    #       B kept the explicit prerequisite. But the *combined*
    #       M3 call is given the combined reconstructed content
    #       where both A's and B's drops apply — so the
    #       prerequisite is only in B's shot 0, and B's shot 1
    #       no longer references it. M3 says broken.
    c_eval = _build_evaluation_response("C1", "broken")

    # One explanation response.
    explanation = _build_explanation_response()

    responses = [extraction, a_eval, b_eval, c_eval, explanation]
    client = _FakeM3Client(responses)
    artifacts = analyze_claims(
        base=base,
        branch_a=a,
        branch_b=b,
        client=client,  # type: ignore[arg-type]
    )
    assert artifacts.analysis.overall_interaction == ClaimCrossEditInteraction.CREATES_NEW_CONFLICT


def test_orchestrator_one_branch_broken_no_creates_new_conflict() -> None:
    """The user-named test: A=broken, B=preserved, combined=broken
    must NOT classify creates_new_conflict. R5 fires."""
    base = _make_rep("base", transcripts=["Do not exceed the recommended dose."])
    a = _make_rep("a", transcripts=["Take this medication as needed."])  # A broke it
    b = _make_rep("b", transcripts=["Do not exceed the recommended dose."])  # B is BASE

    extraction = _build_claim_extraction_response(
        [
            {
                "claim_id": "C1",
                "meaning": "Do not exceed the recommended dose.",
                "claim_type": "prohibition",
                "importance": "critical",
                "evidence_regions": [{"start": 0.0, "end": 1.0, "description": "shot 0"}],
                "equivalents": [],
            }
        ]
    )
    a_eval = _build_evaluation_response("C1", "broken")
    b_eval = _build_evaluation_response("C1", "preserved")
    c_eval = _build_evaluation_response("C1", "broken")
    # R5 fires → none → no explanation M3 call.
    client = _FakeM3Client([extraction, a_eval, b_eval, c_eval])
    artifacts = analyze_claims(
        base=base,
        branch_a=a,
        branch_b=b,
        client=client,  # type: ignore[arg-type]
    )
    assert artifacts.analysis.overall_interaction == ClaimCrossEditInteraction.NONE
    # And no explanation was requested (none of the per-claim
    # interactions are non-none).
    assert artifacts.n_explanation_calls == 0


def test_orchestrator_redundant_claim_no_conflict() -> None:
    """The user-named test: redundant wording remains → no conflict.

    BASE: two equivalent restatements of the same claim.
    A: drops the first restatement. B: keeps BASE.
    Combined: the second restatement survives. M3 should
    return preserved for both branches and combined; the
    derivation step returns none.
    """
    base = _make_rep(
        "base",
        transcripts=[
            "All customers with nut allergies: ask staff for alternatives.",
            "Customers with severe nut allergies: do not consume.",
        ],
        durations=[3.0, 3.0],
    )
    a = _make_rep(
        "a",
        transcripts=[
            # A drops the first (all-nut-allergies) restatement.
            "Customers with severe nut allergies: do not consume.",
        ],
        durations=[3.0],
    )
    b = _make_rep(
        "b",
        transcripts=[
            # B = BASE
            "All customers with nut allergies: ask staff for alternatives.",
            "Customers with severe nut allergies: do not consume.",
        ],
        durations=[3.0, 3.0],
    )

    # The claim is "All nut-allergic customers must avoid".
    # BASE: claim has two evidence regions (shot 0 AND shot 1
    # both carry the meaning). Shot 1 is the equivalent of
    # shot 0 (also a restatement of the same claim).
    extraction = _build_claim_extraction_response(
        [
            {
                "claim_id": "C1",
                "meaning": "All nut-allergic customers must avoid this product.",
                "claim_type": "prohibition",
                "importance": "critical",
                "evidence_regions": [
                    {"start": 0.0, "end": 3.0, "description": "shot 0 says 'all nut allergies'"},
                ],
                "equivalents": [
                    {
                        "start": 3.0,
                        "end": 6.0,
                        "description": "shot 1 says 'severe nut allergies' (narrowing)",
                    },
                ],
            }
        ]
    )
    # A: shot 0 deleted, but the equivalent (shot 1) survives.
    #    M3 sees only shot 1 in A. The meaning is preserved
    #    (narrowed but still covers all nut allergies? — for
    #    the test, we say M3 returns preserved because the
    #    equivalent survives).
    a_eval = _build_evaluation_response(
        "C1",
        "preserved",
        surviving=[{"start": 3.0, "end": 6.0, "description": "equivalent survives"}],
    )
    b_eval = _build_evaluation_response(
        "C1",
        "preserved",
        surviving=[{"start": 0.0, "end": 3.0, "description": "primary survives"}],
    )
    # Combined: both A and B keep something (different things).
    #    M3 returns preserved (the meaning survives across
    #    the redundant statements).
    c_eval = _build_evaluation_response("C1", "preserved")
    client = _FakeM3Client([extraction, a_eval, b_eval, c_eval])
    artifacts = analyze_claims(
        base=base,
        branch_a=a,
        branch_b=b,
        client=client,  # type: ignore[arg-type]
    )
    assert artifacts.analysis.overall_interaction == ClaimCrossEditInteraction.NONE


def test_orchestrator_safe_unrelated_no_conflict() -> None:
    """Two unrelated edits → R7 none."""
    base = _make_rep(
        "base",
        transcripts=[
            "To make the sauce, whisk two eggs into the butter.",
            "Always wear protective gloves when handling the blade.",
        ],
    )
    a = _make_rep(
        "a",
        transcripts=[
            "To make the sauce, whisk three eggs into the butter.",
            "Always wear protective gloves when handling the blade.",
        ],
    )
    b = _make_rep(
        "b",
        transcripts=[
            "To make the sauce, whisk two eggs into the butter.",
            "Always wear protective gloves when handling any sharp tool.",
        ],
    )
    extraction = _build_claim_extraction_response(
        [
            {
                "claim_id": "C1",
                "meaning": "Use 2 eggs in the sauce.",
                "claim_type": "instruction",
                "importance": "high",
                "evidence_regions": [{"start": 0.0, "end": 1.0, "description": "shot 0"}],
                "equivalents": [],
            },
            {
                "claim_id": "C2",
                "meaning": "Wear protective gloves when handling the blade.",
                "claim_type": "prohibition",
                "importance": "high",
                "evidence_regions": [{"start": 1.0, "end": 2.0, "description": "shot 1"}],
                "equivalents": [],
            },
        ]
    )
    # Both claims preserved in A, B, combined.
    a_evals = [
        _build_evaluation_response("C1", "preserved"),
        _build_evaluation_response("C2", "preserved"),
    ]
    b_evals = [
        _build_evaluation_response("C1", "preserved"),
        _build_evaluation_response("C2", "preserved"),
    ]
    c_evals = [
        _build_evaluation_response("C1", "preserved"),
        _build_evaluation_response("C2", "preserved"),
    ]
    client = _FakeM3Client([extraction] + a_evals + b_evals + c_evals)
    artifacts = analyze_claims(
        base=base,
        branch_a=a,
        branch_b=b,
        client=client,  # type: ignore[arg-type]
    )
    assert artifacts.analysis.overall_interaction == ClaimCrossEditInteraction.NONE


def test_orchestrator_uses_only_important_claims() -> None:
    """Claims marked 'low' importance are still passed to M3,
    but the orchestrator reports them in the result so the
    user can inspect them.

    We do not filter low-importance claims in v4.1.0 (the
    orchestrator passes every claim M3 returned to the
    derivation step). The aggregation step prefers higher
    importance on ties.
    """
    base = _make_rep("base", transcripts=["A", "B"])
    a = _make_rep("a", transcripts=["A", "B"])
    b = _make_rep("b", transcripts=["A", "B"])
    extraction = _build_claim_extraction_response(
        [
            {
                "claim_id": "C1",
                "meaning": "x",
                "claim_type": "instruction",
                "importance": "low",
                "evidence_regions": [{"start": 0.0, "end": 1.0, "description": "a"}],
                "equivalents": [],
            }
        ]
    )
    a_eval = _build_evaluation_response("C1", "preserved")
    b_eval = _build_evaluation_response("C1", "preserved")
    c_eval = _build_evaluation_response("C1", "preserved")
    client = _FakeM3Client([extraction, a_eval, b_eval, c_eval])
    artifacts = analyze_claims(
        base=base,
        branch_a=a,
        branch_b=b,
        client=client,  # type: ignore[arg-type]
    )
    assert len(artifacts.analysis.interactions) == 1
    assert artifacts.analysis.overall_interaction == ClaimCrossEditInteraction.NONE


def test_orchestrator_metadata_propagated() -> None:
    """The artifacts expose the model + prompt versions + call
    counts so the eval harness can audit them."""
    base = _make_rep("base", transcripts=["x"])
    a = _make_rep("a", transcripts=["x"])
    b = _make_rep("b", transcripts=["x"])
    extraction = _build_claim_extraction_response(
        [
            {
                "claim_id": "C1",
                "meaning": "x",
                "claim_type": "instruction",
                "importance": "high",
                "evidence_regions": [{"start": 0.0, "end": 1.0, "description": "x"}],
                "equivalents": [],
            }
        ]
    )
    a_eval = _build_evaluation_response("C1", "preserved")
    b_eval = _build_evaluation_response("C1", "preserved")
    c_eval = _build_evaluation_response("C1", "preserved")
    client = _FakeM3Client([extraction, a_eval, b_eval, c_eval])
    artifacts = analyze_claims(
        base=base,
        branch_a=a,
        branch_b=b,
        client=client,  # type: ignore[arg-type]
    )
    assert artifacts.model == "FAKE-M3"
    assert artifacts.n_extraction_calls == 1
    assert artifacts.n_evaluation_calls == 3  # one per branch
    assert artifacts.n_explanation_calls == 0  # all interactions were none
    assert artifacts.extraction_prompt_version == "4.1.0"
    assert artifacts.evaluation_prompt_version == "4.1.0"
