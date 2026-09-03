"""Component similarities + weighted blend.

Each component returns either a score in [0, 1] or `None`
(modality missing). The weighted blend re-normalizes over the
*used* components so a missing modality does not unfairly
penalize the match — per the user's brief, item 4.

Defaults follow the user's brief:

- visual_similarity  = 0.45
  (composed of: structural pHash 0.18, color mean 0.12,
   color histogram 0.15 — see VISUAL_SUBWEIGHTS)
- transcript_similarity = 0.40
- duration_similarity = 0.10
- order_prior = 0.05

These are starting values only; they live in `DEFAULT_WEIGHTS`
and can be overridden per-call (and will be swept in Phase 5).

Phase 3 visual-fingerprint repair: the single `visual_similarity`
is now a blend of three sub-components (pHash + colour mean +
colour histogram). The sub-scores are exposed in
`SimilarityComponents.visual_structural_similarity`,
`visual_color_mean_similarity`, and
`visual_color_histogram_similarity` so the repair is inspectable
and the per-sub-component defaults can be tuned independently.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.alignment import ShotFingerprint, SimilarityComponents
from app.services.alignment.fingerprints import (
    hamming_hex,
    histogram_intersection,
    mean_rgb_similarity,
)

# Default weights from the user's brief.
# Default weights from the user's brief.
DEFAULT_WEIGHTS: dict[str, float] = {
    "visual_similarity": 0.45,
    "transcript_similarity": 0.40,
    "duration_similarity": 0.10,
    "order_prior": 0.05,
}

# Trim detector: if |delta_dur|/max_dur < TRIM_MAX_REL_DIFF we
# treat it as the same shot (just shortened/extended). Beyond
# this threshold we treat it as a different shot.
TRIM_MAX_REL_DIFF = 0.30

# How many top-3-bits-of-each-channel dominate the visual hash (we
# put 9 bits of luminance prefix at the top of the 64-bit hash to
# avoid monochrome degeneracy). Matching those bits gets a free
# ride; the remaining 55 bits are the classical pHash median test.
_VFP_PREFIX_BITS = 9
_VFP_TOTAL_BITS = 64

# Sub-weights for the visual blend. These sum to 1.0 and
# default to: 0.40 pHash (structural), 0.30 mean colour, 0.30
# histogram. They are independent of `DEFAULT_WEIGHTS` so the
# top-level `visual_similarity` can still be tuned in
# isolation.
VISUAL_SUBWEIGHTS: dict[str, float] = {
    "structural": 0.40,
    "color_mean": 0.30,
    "color_histogram": 0.30,
}


# ---------------------------------------------------------------------------
# Per-component visual sub-similarities.
# ---------------------------------------------------------------------------


def visual_structural_similarity(a: ShotFingerprint, b: ShotFingerprint) -> float | None:
    """1.0 - normalised Hamming distance between 64-bit pHashes.

    Returns None only when *both* fingerprints are the all-zero
    placeholder. If one side has a real hash and the other is
    zero, we return 0.0 (real mismatch).
    """
    zero_hash = "0" * 16
    if a.visual_fingerprint == zero_hash and b.visual_fingerprint == zero_hash:
        return None
    if a.visual_fingerprint == zero_hash or b.visual_fingerprint == zero_hash:
        return 0.0
    h = hamming_hex(a.visual_fingerprint, b.visual_fingerprint)
    return 1.0 - (h / float(_VFP_TOTAL_BITS))


def visual_color_mean_similarity(a: ShotFingerprint, b: ShotFingerprint) -> float | None:
    """Wrapper around `mean_rgb_similarity` for fingerprint inputs."""
    return mean_rgb_similarity(a.color_mean_rgb, b.color_mean_rgb)


def visual_color_histogram_similarity(a: ShotFingerprint, b: ShotFingerprint) -> float | None:
    """Wrapper around `histogram_intersection` for fingerprint inputs."""
    return histogram_intersection(a.color_histogram, b.color_histogram)


def visual_similarity(
    a: ShotFingerprint,
    b: ShotFingerprint,
    *,
    subweights: dict[str, float] | None = None,
) -> float | None:
    """Blend the three visual sub-components (pHash + colour mean +
    colour histogram) into one [0, 1] score.

    Re-normalises over the *used* sub-components when one or
    more sub-components are missing. Returns None only when
    every sub-component is missing on both sides (no keyframe
    on either side).
    """
    if subweights is None:
        subweights = VISUAL_SUBWEIGHTS
    struct = visual_structural_similarity(a, b)
    mean = visual_color_mean_similarity(a, b)
    hist = visual_color_histogram_similarity(a, b)
    raw = {
        "structural": struct,
        "color_mean": mean,
        "color_histogram": hist,
    }
    used_total = 0.0
    for name, val in raw.items():
        if val is not None:
            used_total += subweights.get(name, 0.0)
    if used_total <= 0.0:
        return None
    final = sum((raw[name] or 0.0) * (subweights.get(name, 0.0) / used_total) for name in raw)
    return final


def transcript_similarity(a: ShotFingerprint, b: ShotFingerprint) -> float | None:
    """Jaccard similarity over normalized token multisets.

    Returns None when either side has no transcript (no useful
    speech). Returns 0.0 only when one side has speech and the
    other side has speech but the tokens do not overlap at all.

    Jaccard = |A ∩ B| / |A ∪ B|, computed on multisets (so
    repeated words count). Empty intersection over a non-empty
    union = 0.0.
    """
    if not a.has_speech and not b.has_speech:
        return None
    if not a.has_speech or not b.has_speech:
        # One side has speech, the other doesn't: this is a real
        # mismatch, not a missing modality. Return 0.0 so the
        # blend down-weights it.
        return 0.0
    if not a.transcript_tokens and not b.transcript_tokens:
        return None

    # ASR occasionally emits the exact same utterance twice for one
    # shot. Treat an exact repeated token block as one utterance so a
    # duplicated BASE transcript still matches its unchanged branch
    # descendant. Ordinary repeated words are preserved.
    def _collapse_exact_repetitions(tokens: list[str]) -> list[str]:
        for block_size in range(2, (len(tokens) // 2) + 1):
            if len(tokens) % block_size:
                continue
            block = tokens[:block_size]
            if block * (len(tokens) // block_size) == tokens:
                return block
        return tokens

    a_tokens = _collapse_exact_repetitions(a.transcript_tokens)
    b_tokens = _collapse_exact_repetitions(b.transcript_tokens)
    a_multiset: dict[str, int] = {}
    for tok in a_tokens:
        a_multiset[tok] = a_multiset.get(tok, 0) + 1
    b_multiset: dict[str, int] = {}
    for tok in b_tokens:
        b_multiset[tok] = b_multiset.get(tok, 0) + 1
    inter = sum(min(a_multiset.get(t, 0), b_multiset.get(t, 0)) for t in a_multiset)
    union = sum(
        max(a_multiset.get(t, 0), b_multiset.get(t, 0)) for t in set(a_multiset) | set(b_multiset)
    )
    if union == 0:
        return None
    return inter / union


def duration_similarity(a: ShotFingerprint, b: ShotFingerprint) -> float | None:
    """1 - |Δdur| / max(dur_a, dur_b), clipped to [0, 1].

    Always computed (durations are always available). Used both
    as a match signal and to drive the trim detector in
    `edit_ops.infer_operation`.
    """
    max_dur = max(a.duration, b.duration)
    if max_dur <= 0.0:
        return 1.0
    diff = abs(a.duration - b.duration) / max_dur
    return max(0.0, 1.0 - diff)


def relative_duration_diff(a: ShotFingerprint, b: ShotFingerprint) -> float:
    """|Δdur| / max(dur_a, dur_b) — raw relative difference.

    Used by the trim detector (`edit_ops`). Returns 0.0 when both
    durations are zero.
    """
    max_dur = max(a.duration, b.duration)
    if max_dur <= 0.0:
        return 0.0
    return abs(a.duration - b.duration) / max_dur


def order_prior(
    a: ShotFingerprint,
    b: ShotFingerprint,
    *,
    max_gap: int = 4,
) -> float | None:
    """Sequence prior — penalise out-of-order matches.

    Returns 1.0 when branch.sequence_index <= base.sequence_index
    (or the gap is small), dropping smoothly as the gap grows.
    Always computed (no missing-modality case for sequence
    indices).

    The drop is linear: at gap == max_gap the score is 0.0; above
    max_gap it stays 0.0. The DP alignment enforces monotonicity
    anyway, so this prior is mostly a tie-breaker between
    equally-good visual+transcript matches.

    Returns None only when indices are missing (they shouldn't
    be — they are required by the schema — but we are defensive).
    """
    if a.sequence_index < 0 or b.sequence_index < 0:
        return None
    gap = max(0, b.sequence_index - a.sequence_index)
    return max(0.0, 1.0 - (gap / float(max_gap)))


# ---------------------------------------------------------------------------
# Weighted blend with missing-modality re-normalization.
# ---------------------------------------------------------------------------


@dataclass
class ComponentResult:
    """Raw per-component output. Used by tests and the runner.

    `visual_*` fields are the three visual sub-components
    (structural pHash + colour mean + colour histogram) and
    `visual` is their re-normalized blend.
    """

    visual: float | None
    visual_structural: float | None
    visual_color_mean: float | None
    visual_color_histogram: float | None
    transcript: float | None
    duration: float | None
    order: float | None


def compute_components(
    a: ShotFingerprint,
    b: ShotFingerprint,
) -> ComponentResult:
    """Run all components (incl. the three visual sub-components) for one (base, branch) pair."""
    return ComponentResult(
        visual=visual_similarity(a, b),
        visual_structural=visual_structural_similarity(a, b),
        visual_color_mean=visual_color_mean_similarity(a, b),
        visual_color_histogram=visual_color_histogram_similarity(a, b),
        transcript=transcript_similarity(a, b),
        duration=duration_similarity(a, b),
        order=order_prior(a, b),
    )


def blend(
    a: ShotFingerprint,
    b: ShotFingerprint,
    *,
    weights: dict[str, float] | None = None,
) -> SimilarityComponents:
    """Compute components and return the blended SimilarityComponents.

    Missing components (returned None) drop out of the blend
    entirely; the remaining weights are re-normalized so they
    sum to 1.0 over the used components. If NO component is
    available, `final_score` is 0.0 and `used_components` is [].

    The per-visual-component sub-scores are passed through to
    the returned model so the Phase 3 visual-repair is
    inspectable in the diagnostics dump.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    comps = compute_components(a, b)
    raw = {
        "visual_similarity": comps.visual,
        "transcript_similarity": comps.transcript,
        "duration_similarity": comps.duration,
        "order_prior": comps.order,
    }

    used: list[str] = []
    weight_total = 0.0
    for name, score in raw.items():
        if score is not None:
            used.append(name)
            weight_total += weights.get(name, 0.0)

    if weight_total > 0.0:
        # Type-narrowing: `used` only contains names whose raw
        # value is not None.
        final = sum((raw[name] or 0.0) * (weights.get(name, 0.0) / weight_total) for name in used)
    else:
        final = 0.0

    return SimilarityComponents(
        visual_similarity=comps.visual,
        visual_structural_similarity=comps.visual_structural,
        visual_color_mean_similarity=comps.visual_color_mean,
        visual_color_histogram_similarity=comps.visual_color_histogram,
        transcript_similarity=comps.transcript,
        duration_similarity=comps.duration,
        order_prior=comps.order,
        final_score=final,
        used_components=used,
    )


__all__ = [
    "DEFAULT_WEIGHTS",
    "TRIM_MAX_REL_DIFF",
    "VISUAL_SUBWEIGHTS",
    "ComponentResult",
    "blend",
    "compute_components",
    "duration_similarity",
    "order_prior",
    "relative_duration_diff",
    "transcript_similarity",
    "visual_color_histogram_similarity",
    "visual_color_mean_similarity",
    "visual_similarity",
    "visual_structural_similarity",
]
