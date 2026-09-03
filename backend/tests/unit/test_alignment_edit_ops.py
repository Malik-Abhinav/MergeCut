"""Unit tests for `app.services.alignment.edit_ops`.

Covers the rule-based edit-operation inference (unchanged /
delete / replace / trim / insert / uncertain) and the
`OperationThresholds` source of truth.
"""

from __future__ import annotations

import pytest

from app.models.alignment import AlignmentMatch, ShotFingerprint, SimilarityComponents
from app.services.alignment.edit_ops import (
    THRESHOLDS,
    OperationThresholds,
    infer_confidence,
    infer_operation,
)

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _fp(
    *,
    idx: int = 0,
    duration: float = 1.0,
    vfp: str = "0000000000000000",
    transcript: str = "",
) -> ShotFingerprint:
    return ShotFingerprint(
        shot_id=f"shot_{idx:04d}",
        start=float(idx),
        end=float(idx) + duration,
        duration=duration,
        keyframe_paths=[],
        visual_fingerprint=vfp,
        normalized_transcript=transcript,
        transcript_tokens=transcript.split() if transcript else [],
        has_speech=bool(transcript),
        sequence_index=idx,
    )


def _sim(
    *,
    visual: float | None = 1.0,
    transcript: float | None = 1.0,
    duration: float = 1.0,
    order: float = 1.0,
    used: list[str] | None = None,
    final: float | None = None,
) -> SimilarityComponents:
    if used is None:
        used = [
            name
            for name, val in (
                ("visual_similarity", visual),
                ("transcript_similarity", transcript),
                ("duration_similarity", duration),
                ("order_prior", order),
            )
            if val is not None
        ]
    if final is None:
        # Compute a renormalized blend.
        weights = {
            "visual_similarity": 0.45,
            "transcript_similarity": 0.40,
            "duration_similarity": 0.10,
            "order_prior": 0.05,
        }
        raw = {
            "visual_similarity": visual,
            "transcript_similarity": transcript,
            "duration_similarity": duration,
            "order_prior": order,
        }
        wsum = sum(weights[n] for n in used)
        final = sum((raw[n] or 0.0) * (weights[n] / wsum) for n in used) if wsum else 0.0
    return SimilarityComponents(
        visual_similarity=visual,
        transcript_similarity=transcript,
        duration_similarity=duration,
        order_prior=order,
        final_score=final,
        used_components=used,
    )


def _match(
    *,
    base: ShotFingerprint | None,
    branch: ShotFingerprint | None,
    sim: SimilarityComponents,
) -> AlignmentMatch:
    return AlignmentMatch(
        base_shot=base,
        branch_shot=branch,
        similarity=sim,
        operation="uncertain",
        confidence=0.0,
    )


# ---------------------------------------------------------------------------
# Threshold sanity.
# ---------------------------------------------------------------------------


def test_thresholds_are_pinned() -> None:
    d = OperationThresholds.as_dict()
    assert d["UNCHANGED_MIN"] == 0.85
    assert d["UNCHANGED_MAX_REL_DIFF"] == 0.10
    assert d["REPLACE_MIN"] == 0.50
    assert d["TRIM_MIN_VISUAL"] == 0.85
    assert d["TRIM_MAX_REL_DIFF"] == pytest.approx(0.30)


def test_thresholds_dict_alias() -> None:
    assert THRESHOLDS == OperationThresholds.as_dict()


# ---------------------------------------------------------------------------
# Delete / insert (mechanical transitions).
# ---------------------------------------------------------------------------


def test_infer_operation_pure_delete() -> None:
    base = _fp(idx=0)
    branch = None
    sim = _sim(visual=None, transcript=None)
    op, evidence = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op == "delete"
    assert "branch_shot is None" in evidence["reason"]


def test_infer_operation_pure_insert() -> None:
    base = None
    branch = _fp(idx=0)
    sim = _sim()
    op, evidence = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op == "insert"
    assert "base_shot is None" in evidence["reason"]


# ---------------------------------------------------------------------------
# Unchanged.
# ---------------------------------------------------------------------------


def test_infer_operation_unchanged_high_visual_and_transcript() -> None:
    # Identical visuals + identical transcript + identical duration
    # → unchanged. A real "trim" requires a non-zero duration
    # delta; same-length high-visual matches are unchanged.
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    branch = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    sim = _sim(visual=0.95, transcript=0.95)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op == "unchanged"


def test_infer_operation_identical_match_is_unchanged_not_trim() -> None:
    # A perfectly-matching shot (visual ≥ TRIM_MIN_VISUAL,
    # rel_dur_diff=0) must NOT be classified as trim. Trim is a
    # duration change by definition.
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    branch = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    sim = _sim(visual=0.95, transcript=0.95)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op == "unchanged"


def test_infer_operation_unchanged_requires_both_signals() -> None:
    # High visual but very low transcript — must NOT be unchanged.
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    branch = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="different words here")
    sim = _sim(visual=0.95, transcript=0.10)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op != "unchanged"


def test_infer_operation_unchanged_both_no_speech() -> None:
    # No speech on either side → transcript check is dropped.
    # Visual is high, duration identical (no trim) → unchanged.
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="")
    branch = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="")
    sim = _sim(visual=0.95, transcript=None)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op == "unchanged"


# ---------------------------------------------------------------------------
# Trim.
# ---------------------------------------------------------------------------


def test_infer_operation_trim_short_duration_change() -> None:
    # Duration 2.0 → 1.6 = 20% shorter (under 30% threshold) and
    # identical visual → trim.
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    branch = _fp(idx=0, duration=1.6, vfp="abcd1234", transcript="hello world")
    sim = _sim(visual=0.95, transcript=0.95, duration=0.8)
    op, evidence = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op == "trim"
    assert "trim" in evidence["reason"]


def test_small_duration_delta_with_identical_content_is_unchanged() -> None:
    base = _fp(idx=0, duration=7.5, vfp="abcd1234", transcript="same words")
    branch = _fp(idx=0, duration=8.0, vfp="abcd1234", transcript="same words")
    sim = _sim(visual=1.0, transcript=1.0, duration=0.9375)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op == "unchanged"


def test_infer_operation_trim_requires_strong_visual() -> None:
    # 20% shorter but visually different → not trim; falls to
    # uncertain (visual below UNCHANGED_MIN and below
    # TRIM_MIN_VISUAL).
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    branch = _fp(idx=0, duration=1.6, vfp="0000ffff", transcript="hello world")
    sim = _sim(visual=0.70, transcript=0.95, duration=0.8)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    # Visual 0.70 is below UNCHANGED_MIN (0.85) and below
    # TRIM_MIN_VISUAL (0.85), but above REPLACE_MIN (0.50)
    # AND transcript is also above REPLACE_MIN → replace.
    assert op == "replace"


def test_infer_operation_trim_ignores_duration_above_threshold() -> None:
    # Duration 2.0 → 0.5 → relative diff 0.75 → above the
    # 0.30 threshold. Even with strong visual, this is NOT a
    # trim.
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    branch = _fp(idx=0, duration=0.5, vfp="abcd1234", transcript="hello world")
    sim = _sim(visual=0.95, transcript=0.95, duration=0.25)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op != "trim"


# ---------------------------------------------------------------------------
# Replace.
# ---------------------------------------------------------------------------


def test_infer_operation_replace_moderate_visual() -> None:
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    branch = _fp(idx=0, duration=2.0, vfp="0000ffff", transcript="hello world")
    sim = _sim(visual=0.60, transcript=0.95)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    # Visual 0.60 is above REPLACE_MIN (0.50) and below
    # UNCHANGED_MIN (0.85) and below TRIM_MIN_VISUAL (0.85),
    # and durations are equal so no trim → replace.
    assert op == "replace"


def test_infer_operation_replace_low_visual_but_high_transcript() -> None:
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    branch = _fp(idx=0, duration=2.0, vfp="ffff0000", transcript="completely different speech")
    sim = _sim(visual=0.20, transcript=0.55)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    # Visual 0.20 < REPLACE_MIN, transcript 0.55 ≥ REPLACE_MIN.
    # Both must clear REPLACE_MIN for replace, so this falls
    # through to uncertain.
    assert op == "uncertain"


def test_infer_operation_replace_requires_both_above_threshold() -> None:
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    branch = _fp(idx=0, duration=2.0, vfp="ffff0000", transcript="totally different words here")
    sim = _sim(visual=0.55, transcript=0.55)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op == "replace"


def test_strong_visual_anchor_with_divergent_speech_is_replace() -> None:
    base = _fp(idx=0, duration=7.5, vfp="abcd1234", transcript="once unplugged lift cover")
    branch = _fp(idx=0, duration=7.0, vfp="abcd1234", transcript="lift cover")
    sim = _sim(visual=1.0, transcript=0.4, duration=0.93)
    op, _ = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op == "replace"


# ---------------------------------------------------------------------------
# Uncertain (no rule fires).
# ---------------------------------------------------------------------------


def test_infer_operation_uncertain_low_signals() -> None:
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hello world")
    branch = _fp(idx=0, duration=2.0, vfp="0000ffff", transcript="different words here")
    sim = _sim(visual=0.20, transcript=0.20)
    op, evidence = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert op == "uncertain"
    assert "no rule matched" in evidence["reason"]


def test_infer_operation_degenerate_both_none() -> None:
    # Both shots None — pathological, should not crash.
    sim = _sim()
    op, _ = infer_operation(_match(base=None, branch=None, sim=sim))
    assert op == "uncertain"


# ---------------------------------------------------------------------------
# Confidence.
# ---------------------------------------------------------------------------


def test_infer_confidence_pure_delete_is_one() -> None:
    base = _fp(idx=0)
    branch = None
    sim = _sim(visual=None, transcript=None)
    m = _match(base=base, branch=branch, sim=sim)
    assert infer_confidence(m, "delete") == 1.0


def test_infer_confidence_pure_insert_is_one() -> None:
    base = None
    branch = _fp(idx=0)
    sim = _sim()
    m = _match(base=base, branch=branch, sim=sim)
    assert infer_confidence(m, "insert") == 1.0


def test_infer_confidence_match_uses_final_score() -> None:
    base = _fp(idx=0)
    branch = _fp(idx=0)
    sim = _sim(visual=0.90, transcript=0.90, duration=1.0, order=1.0, final=0.93)
    m = _match(base=base, branch=branch, sim=sim)
    assert infer_confidence(m, "unchanged") == 0.93
    assert infer_confidence(m, "replace") == 0.93
    assert infer_confidence(m, "trim") == 0.93


# ---------------------------------------------------------------------------
# Evidence shape.
# ---------------------------------------------------------------------------


def test_evidence_contains_components_and_thresholds() -> None:
    base = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hi")
    branch = _fp(idx=0, duration=2.0, vfp="abcd1234", transcript="hi")
    sim = _sim(visual=0.90, transcript=0.90, duration=1.0, order=1.0)
    _, evidence = infer_operation(_match(base=base, branch=branch, sim=sim))
    assert "visual_similarity" in evidence
    assert "transcript_similarity" in evidence
    assert "duration_similarity" in evidence
    assert "final_score" in evidence
    assert "used_components" in evidence
    assert "relative_duration_diff" in evidence
    assert "thresholds" in evidence
    assert "reason" in evidence
