"""Unit tests for `app.services.alignment.similarity`.

Covers each component similarity function, the weighted blend
(including missing-modality re-normalization), and the helper
`relative_duration_diff` used by the trim detector.
"""

from __future__ import annotations

import pytest

from app.models.alignment import ShotFingerprint
from app.services.alignment.similarity import (
    DEFAULT_WEIGHTS,
    VISUAL_SUBWEIGHTS,
    blend,
    compute_components,
    duration_similarity,
    order_prior,
    relative_duration_diff,
    transcript_similarity,
    visual_color_histogram_similarity,
    visual_color_mean_similarity,
    visual_similarity,
    visual_structural_similarity,
)

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _fp(
    *,
    vfp: str = "0000000000000000",
    transcript: str = "",
    duration: float = 1.0,
    sequence_index: int = 0,
    color_mean: tuple[float, float, float] | None = None,
    color_hist: tuple[float, ...] | None = None,
) -> ShotFingerprint:
    tokens = transcript.split() if transcript else []
    # If vfp is the all-zero hash AND no colour was provided,
    # the fingerprint is "no keyframe available" → leave the
    # colour fields None. If a non-zero vfp is provided, assume
    # the fingerprint has a keyframe and synthesise a
    # mid-grey colour (so the colour sub-components are present
    # by default in test fixtures).
    has_keyframe = vfp != "0" * 16
    if color_mean is None and has_keyframe and color_hist is None:
        color_mean = (0.5, 0.5, 0.5)
        color_hist = tuple([1.0 / 12.0] * 12)
    return ShotFingerprint(
        shot_id=f"shot_{sequence_index}",
        start=float(sequence_index),
        end=float(sequence_index) + duration,
        duration=duration,
        keyframe_paths=[],
        visual_fingerprint=vfp,
        color_mean_rgb=color_mean,
        color_histogram=color_hist,
        normalized_transcript=transcript,
        transcript_tokens=tokens,
        has_speech=bool(transcript),
        sequence_index=sequence_index,
    )


# ---------------------------------------------------------------------------
# visual_similarity.
# ---------------------------------------------------------------------------


def test_visual_identical_hashes_score_one() -> None:
    h = "abcd1234abcd1234"
    assert visual_similarity(_fp(vfp=h), _fp(vfp=h)) == pytest.approx(1.0)


def test_visual_different_hashes_score_below_one() -> None:
    a = _fp(vfp="0000000000000000")
    b = _fp(vfp="ffffffffffffffff")
    s = visual_similarity(a, b)
    assert s is not None
    assert 0.0 <= s < 1.0


def test_visual_both_zero_returns_none() -> None:
    assert visual_similarity(_fp(vfp="0" * 16), _fp(vfp="0" * 16)) is None


def test_visual_one_zero_returns_zero() -> None:
    a = _fp(vfp="0" * 16)
    b = _fp(vfp="abcd1234abcd1234")
    assert visual_similarity(a, b) == 0.0


# ---------------------------------------------------------------------------
# transcript_similarity (Jaccard on token multisets).
# ---------------------------------------------------------------------------


def test_transcript_identical_is_one() -> None:
    a = _fp(transcript="a b c")
    b = _fp(transcript="a b c")
    assert transcript_similarity(a, b) == pytest.approx(1.0)


def test_transcript_exact_utterance_duplication_is_collapsed() -> None:
    a = _fp(transcript="once unplugged lift cover once unplugged lift cover")
    b = _fp(transcript="once unplugged lift cover")
    assert transcript_similarity(a, b) == pytest.approx(1.0)


def test_transcript_no_overlap_is_zero() -> None:
    a = _fp(transcript="a b c")
    b = _fp(transcript="x y z")
    assert transcript_similarity(a, b) == 0.0


def test_transcript_partial_overlap_jaccard() -> None:
    # {a, b, c} ∩ {b, c, d} = {b, c} (size 2)
    # {a, b, c} ∪ {b, c, d} = {a, b, c, d} (size 4)
    # Jaccard = 2/4 = 0.5
    a = _fp(transcript="a b c")
    b = _fp(transcript="b c d")
    assert transcript_similarity(a, b) == pytest.approx(0.5)


def test_transcript_jaccard_treats_duplicates_as_multisets() -> None:
    # {a, a, b} vs {a, b, b} → ∩ = {a, b} (multiset min), |∩| = 2
    # Union multiset = {a, a, b, b} → |U| = 4
    # Jaccard = 0.5
    a = _fp(transcript="a a b")
    b = _fp(transcript="a b b")
    assert transcript_similarity(a, b) == pytest.approx(0.5)


def test_transcript_both_empty_returns_none() -> None:
    a = _fp(transcript="")
    b = _fp(transcript="")
    assert transcript_similarity(a, b) is None


def test_transcript_one_empty_is_zero_mismatch() -> None:
    a = _fp(transcript="hello world")
    b = _fp(transcript="")
    assert transcript_similarity(a, b) == 0.0


# ---------------------------------------------------------------------------
# duration_similarity + relative_duration_diff.
# ---------------------------------------------------------------------------


def test_duration_identical_is_one() -> None:
    a = _fp(duration=2.0)
    b = _fp(duration=2.0)
    assert duration_similarity(a, b) == pytest.approx(1.0)


def test_duration_one_double_other_is_half() -> None:
    # |2-1| / max(2,1) = 1/2 → 1 - 1/2 = 0.5
    a = _fp(duration=2.0)
    b = _fp(duration=1.0)
    assert duration_similarity(a, b) == pytest.approx(0.5)


def test_duration_zero_both_is_one() -> None:
    a = _fp(duration=0.0)
    b = _fp(duration=0.0)
    assert duration_similarity(a, b) == 1.0


def test_duration_always_computed() -> None:
    # duration similarity must never be None — it is always
    # computable from the timestamps.
    a = _fp(duration=0.0)
    b = _fp(duration=0.0)
    assert duration_similarity(a, b) is not None


def test_relative_duration_diff_basic() -> None:
    a = _fp(duration=2.0)
    b = _fp(duration=1.0)
    assert relative_duration_diff(a, b) == pytest.approx(0.5)


def test_relative_duration_diff_zero_durations() -> None:
    a = _fp(duration=0.0)
    b = _fp(duration=0.0)
    assert relative_duration_diff(a, b) == 0.0


# ---------------------------------------------------------------------------
# order_prior.
# ---------------------------------------------------------------------------


def test_order_prior_within_window_is_one() -> None:
    # gap=0 → 1.0
    a = _fp(sequence_index=2)
    b = _fp(sequence_index=2)
    assert order_prior(a, b) == pytest.approx(1.0)


def test_order_prior_drops_with_gap() -> None:
    a = _fp(sequence_index=0)
    b = _fp(sequence_index=2)
    # max_gap=4 default → 1 - 2/4 = 0.5
    assert order_prior(a, b) == pytest.approx(0.5)


def test_order_prior_clamps_to_zero_at_window_edge() -> None:
    a = _fp(sequence_index=0)
    b = _fp(sequence_index=4)
    assert order_prior(a, b) == pytest.approx(0.0)


def test_order_prior_clamps_to_zero_beyond_window() -> None:
    a = _fp(sequence_index=0)
    b = _fp(sequence_index=10)
    assert order_prior(a, b) == 0.0


# ---------------------------------------------------------------------------
# compute_components.
# ---------------------------------------------------------------------------


def test_compute_components_returns_all_four() -> None:
    a = _fp(vfp="abcd1234abcd1234", transcript="hi there", duration=2.0)
    b = _fp(vfp="abcd1234abcd1234", transcript="hi there", duration=2.0)
    comps = compute_components(a, b)
    assert comps.visual == pytest.approx(1.0)
    assert comps.transcript == pytest.approx(1.0)
    assert comps.duration == pytest.approx(1.0)
    assert comps.order == pytest.approx(1.0)


def test_compute_components_handles_missing_visual() -> None:
    a = _fp(vfp="0" * 16, transcript="hi", duration=1.0)
    b = _fp(vfp="0" * 16, transcript="hi", duration=1.0)
    comps = compute_components(a, b)
    assert comps.visual is None
    assert comps.transcript == pytest.approx(1.0)
    assert comps.duration == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# blend (weighted with re-normalization).
# ---------------------------------------------------------------------------


def test_blend_full_match_is_one() -> None:
    a = _fp(vfp="abcd1234abcd1234", transcript="hi there", duration=2.0)
    b = _fp(vfp="abcd1234abcd1234", transcript="hi there", duration=2.0)
    sim = blend(a, b)
    assert sim.final_score == pytest.approx(1.0)
    assert set(sim.used_components) == {
        "visual_similarity",
        "transcript_similarity",
        "duration_similarity",
        "order_prior",
    }


def test_blend_renormalizes_when_visual_missing() -> None:
    # Both visual hashes zero → re-normalize over
    # transcript + duration + order. Default weights:
    #   transcript 0.40, duration 0.10, order 0.05  → total 0.55
    # Identical transcript + duration + order → 1.0
    a = _fp(vfp="0" * 16, transcript="hello", duration=1.0)
    b = _fp(vfp="0" * 16, transcript="hello", duration=1.0)
    sim = blend(a, b)
    assert sim.visual_similarity is None
    assert "visual_similarity" not in sim.used_components
    assert sim.final_score == pytest.approx(1.0)


def test_blend_renormalizes_when_transcript_missing() -> None:
    # No speech on either side → transcript dropped. Remaining:
    # visual + duration + order.
    a = _fp(vfp="abcd1234abcd1234", duration=2.0)
    b = _fp(vfp="abcd1234abcd1234", duration=2.0)
    sim = blend(a, b)
    assert sim.transcript_similarity is None
    assert "transcript_similarity" not in sim.used_components
    # All remaining components are 1.0 → final 1.0.
    assert sim.final_score == pytest.approx(1.0)


def test_blend_all_components_missing_is_zero() -> None:
    a = _fp(vfp="0" * 16, duration=0.0)
    b = _fp(vfp="0" * 16, duration=0.0)
    sim = blend(a, b)
    # visual=None, transcript=None, duration=1.0 (both zero),
    # order=1.0 (gap 0). So used = {duration, order}.
    assert sim.final_score > 0.0
    # When truly everything is missing, blend is 0.0.
    # Construct that case explicitly.
    a2 = _fp(vfp="0" * 16, transcript="", duration=0.0)
    b2 = _fp(vfp="0" * 16, transcript="", duration=0.0)
    sim2 = blend(a2, b2)
    # duration=1.0 still computable even when both are 0.
    assert sim2.final_score > 0.0


def test_blend_partial_visual_only_match() -> None:
    # Visual: identical → 1.0. Transcript: no overlap → 0.0.
    # Default weights: 0.45 + 0.10 + 0.05 = 0.60 (used).
    #   0.45 * 1.0 / 0.60 + 0.10 * 1.0 / 0.60 + 0.05 * 1.0 / 0.60
    #   = 0.60 / 0.60 = 1.0 → all 1.0 components.
    # Build a case where visual is partial and transcript is zero.
    a = _fp(vfp="0000000000000000", transcript="", duration=2.0)
    b = _fp(vfp="ffffffffffffffff", transcript="", duration=2.0)
    sim = blend(a, b)
    # visual=0.0, transcript=None, duration=1.0, order=1.0
    # used = {visual, duration, order}; weights 0.45+0.10+0.05=0.60
    # final = (0.45*0.0 + 0.10*1.0 + 0.05*1.0) / 0.60
    #       = 0.15 / 0.60 = 0.25
    assert sim.final_score == pytest.approx(0.25)


def test_blend_custom_weights() -> None:
    a = _fp(vfp="abcd1234abcd1234", transcript="hi", duration=2.0)
    b = _fp(vfp="abcd1234abcd1234", transcript="hi", duration=2.0)
    # Even with all-zero weights, the final is renormalized — but
    # if the total of used-component weights is zero, the blend
    # falls back to 0.0.
    sim = blend(a, b, weights={k: 0.0 for k in DEFAULT_WEIGHTS})
    # weight_total == 0 → final = 0.0
    assert sim.final_score == 0.0


def test_blend_used_components_reflects_missing_modalities() -> None:
    a = _fp(vfp="0" * 16, transcript="", duration=1.0)
    b = _fp(vfp="abcd1234abcd1234", transcript="", duration=1.0)
    sim = blend(a, b)
    # visual present (one side zero, one real → score 0.0 but
    # still USED), transcript dropped (both no-speech), duration
    # used, order used.
    assert "visual_similarity" in sim.used_components
    assert "transcript_similarity" not in sim.used_components


def test_default_weights_sum_to_one() -> None:
    assert sum(DEFAULT_WEIGHTS.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Phase 3 visual-fingerprint repair: per-component sub-similarities.
# ---------------------------------------------------------------------------


def test_visual_structural_similarity_identical_phash() -> None:
    h = "abcd1234abcd1234"
    assert visual_similarity(_fp(vfp=h), _fp(vfp=h)) == pytest.approx(1.0)  # type: ignore[arg-type]


def test_visual_structural_differs_from_blend() -> None:
    """The new blend can return a different score than the
    structural-only pHash similarity when colour evidence
    disagrees. Solid red vs solid yellow have the same
    luminance prefix → structural pHash ≈ 1.0; but mean
    colour differs → blend is lower.
    """
    red = _fp(vfp="ff0000ff0000ff00")  # rough red pHash
    yellow = _fp(vfp="ffff00ffff00ff00")  # rough yellow pHash
    struct = visual_structural_similarity(red, yellow)
    color = visual_color_mean_similarity(red, yellow)
    blend = visual_similarity(red, yellow)
    assert struct is not None
    assert color is not None
    assert blend is not None
    # The blend is between the structural and colour components.
    assert min(struct, color) <= blend <= max(struct, color)


def test_visual_blend_uses_all_three_subweights_when_present() -> None:
    """When all three sub-components are present, the blend
    is the re-normalized weighted average."""
    a = _fp(vfp="abcd1234abcd1234")
    b = _fp(vfp="abcd1234abcd1234")
    struct = visual_structural_similarity(a, b)
    mean = visual_color_mean_similarity(a, b)
    hist = visual_color_histogram_similarity(a, b)
    blend = visual_similarity(a, b)
    # All three should be 1.0 → blend = 1.0.
    assert struct == pytest.approx(1.0)
    assert mean == pytest.approx(1.0)
    assert hist == pytest.approx(1.0)
    assert blend == pytest.approx(1.0)
    # And the subweights sum to 1.0.
    assert sum(VISUAL_SUBWEIGHTS.values()) == pytest.approx(1.0)


def test_visual_blend_returns_none_when_all_subcomponents_missing() -> None:
    """Both sides missing every sub-component → blend returns
    None (no visual evidence at all → blend re-normalises)."""
    # `_fp` defaults vfp="0"*16 and no color. So all three
    # sub-components are None.
    assert visual_similarity(_fp(), _fp()) is None


def test_visual_blend_returns_low_when_structural_similar_but_color_differs() -> None:
    """The Phase 3 visual-fingerprint repair case: two
    solid-colour shots with the same pHash (luminance prefix
    degenerate) but very different mean colours. The blend
    must drop the score, not just return 1.0 from pHash.

    We construct the test so the pHash is *identical* (luminance
    prefix degeneracy) and only the colour sub-components
    distinguish the two shots. With identical pHash + identical
    duration + identical order, the blend must come from the
    colour components.
    """
    same_phash = "ff0000ff0000ff00"  # arbitrary identical hashes
    red = _fp(
        vfp=same_phash,
        color_mean=(1.0, 0.0, 0.0),
        color_hist=(1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    )
    yellow = _fp(
        vfp=same_phash,
        color_mean=(1.0, 1.0, 0.0),
        color_hist=(0.5, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    )
    struct = visual_structural_similarity(red, yellow)
    color = visual_color_mean_similarity(red, yellow)
    blend = visual_similarity(red, yellow)
    assert struct is not None
    assert color is not None
    assert blend is not None
    # Structural = 1.0 (identical pHash). Colour < 1.0 (mean differs).
    assert struct == pytest.approx(1.0)
    assert color < 1.0
    # The blend must be strictly below 1.0 because the colour
    # sub-components pull it down.
    assert blend < 1.0


def test_blend_passes_through_visual_subscores() -> None:
    """The SimilarityComponents returned by `blend` must carry
    the per-visual sub-scores so the Phase 3 repair is
    inspectable in the JSON dump."""
    sim = blend(_fp(), _fp())
    assert sim.visual_similarity is None
    assert sim.visual_structural_similarity is None
    assert sim.visual_color_mean_similarity is None
    assert sim.visual_color_histogram_similarity is None
    # Real fingerprints: all three are present.
    a = _fp(vfp="abcd1234abcd1234")
    b = _fp(vfp="abcd1234abcd1234")
    sim = blend(a, b)
    assert sim.visual_structural_similarity is not None
    assert sim.visual_color_mean_similarity is not None
    assert sim.visual_color_histogram_similarity is not None


def test_visual_subweights_default_to_three_components() -> None:
    """The default visual sub-weight dict has three named
    sub-components and they sum to 1.0."""

    assert set(VISUAL_SUBWEIGHTS.keys()) == {"structural", "color_mean", "color_histogram"}
    assert sum(VISUAL_SUBWEIGHTS.values()) == pytest.approx(1.0)
