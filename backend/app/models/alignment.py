"""Pydantic models for Phase 3 BASE ↔ branch alignment.

This is the structured output of `align_branch_to_base()` (see
`app.services.alignment.run`). The schema is deliberately verbose
and includes every component score so the user can inspect the
match, not just the final number.

Three layers:

1. `ShotFingerprint`       — one shot's representation (built from
                              a Phase 2 `VideoRepresentation`).
2. `SimilarityComponents`  — the four component similarities
                              between two fingerprints (visual,
                              transcript, duration, order_prior)
                              plus the weighted blend and which
                              modalities were actually used.
3. `AlignmentMatch`        — one (base_shot, branch_shot) pair,
                              the component scores, and the
                              inferred edit operation.

The top-level `AlignmentResult` aggregates the matches for one
branch against BASE.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Edit operation types. Per the user's brief: UNCHANGED / DELETE / REPLACE /
# TRIM are required; INSERT / MOVE are deliberately deferred (Phase 4+).
# ---------------------------------------------------------------------------

EditOperationType = Literal[
    "unchanged",
    "delete",
    "replace",
    "trim",
    "insert",
    "move",
    "uncertain",
]

# ---------------------------------------------------------------------------
# Shot fingerprint.
# ---------------------------------------------------------------------------


class ShotFingerprint(BaseModel):
    """A compact, deterministic representation of one shot.

    Built from a Phase 2 `Shot` (which carries metadata + keyframe
    paths + transcript text + transcript segments). Everything
    needed for sequence-aware alignment lives here:

    - `shot_id`            — stable id (e.g. 'shot_0003').
    - `start` / `end`      — timeline seconds.
    - `duration`           — `end - start`.
    - `keyframe_paths`     — absolute paths to one or more
                             representative frames. Phase 2 ships
                             one per shot; the schema accepts a
                             list so Phase 3 / 4 can pick more
                             without a schema change.
    - `visual_fingerprint` — perceptual hash of the (downsampled)
                             keyframe, encoded as 16 hex chars
                             (64-bit pHash). Deterministic, no
                             embeddings, no vector DB.
    - `normalized_transcript` — transcript text lower-cased,
                             whitespace-collapsed, punctuation
                             stripped.
    - `transcript_tokens`  — list of normalized tokens. Empty
                             when the shot has no useful speech
                             or no audio track.
    - `has_speech`         — True iff a non-empty normalized
                             transcript is available.
    - `sequence_index`     — original ordinal (0-based) in the
                             source video; used by the order_prior
                             component.
    """

    model_config = ConfigDict(extra="forbid")

    shot_id: str
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    duration: float = Field(ge=0.0)
    keyframe_paths: list[Path] = Field(default_factory=list)
    visual_fingerprint: str = Field(
        description=(
            "64-bit perceptual hash of the (downsampled) keyframe, "
            "encoded as 16 hex chars. '0' * 16 when no keyframe is "
            "available."
        )
    )
    # ------------------------------------------------------------------
    # Color-aware components (added in the Phase 3 visual-fingerprint
    # repair). pHash alone degenerates on visually uniform content
    # (solid-colour shots, near-monochrome frames), so the
    # fingerprint also carries the keyframe's mean colour and a
    # small normalized per-channel histogram. These are *additional*
    # evidence; the pHash stays as the structural component.
    # ------------------------------------------------------------------
    color_mean_rgb: tuple[float, float, float] | None = Field(
        default=None,
        description=(
            "Mean RGB of the keyframe, each channel in [0, 1]. None when no keyframe is available."
        ),
    )
    color_histogram: tuple[float, ...] | None = Field(
        default=None,
        description=(
            "Concatenated per-channel histogram (R bins, then G bins, "
            "then B bins), each value in [0, 1] and the whole vector "
            "normalized to sum to 1.0. None when no keyframe is "
            "available."
        ),
    )
    normalized_transcript: str = Field(
        default="",
        description=(
            "Lower-cased, whitespace-collapsed, punctuation-stripped "
            "transcript. Empty string when the shot has no speech."
        ),
    )
    transcript_tokens: list[str] = Field(
        default_factory=list,
        description="Tokenized normalized_transcript.",
    )
    has_speech: bool = Field(
        default=False,
        description=(
            "True iff a non-empty normalized_transcript is available. "
            "Used by similarity components to skip the transcript "
            "signal without penalizing the weighted blend."
        ),
    )
    sequence_index: int = Field(
        ge=0,
        description="Original ordinal (0-based) in the source video.",
    )


# ---------------------------------------------------------------------------
# Per-pair similarity.
# ---------------------------------------------------------------------------


class SimilarityComponents(BaseModel):
    """The four component similarities for one (base, branch) pair.

    Each component lives in [0, 1]. `None` means the component was
    not computed (typically because the modality is missing or
    not applicable).

    `final_score` is the weighted blend. When one or more
    components are `None`, the blend is re-normalized over the
    remaining components so a missing modality does NOT unfairly
    penalize the match (per the user's brief, item 4).

    `used_components` lists the names of the components that
    contributed to `final_score`. Diagnostics.
    """

    model_config = ConfigDict(extra="forbid")

    visual_similarity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Blend of structural (pHash) + color (mean + histogram) "
            "visual evidence. Kept as the single 'visual' field for "
            "downstream consumers; the per-component scores are "
            "exposed separately as `visual_structural_similarity`, "
            "`visual_color_mean_similarity`, and "
            "`visual_color_histogram_similarity` for inspection."
        ),
    )
    visual_structural_similarity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "1.0 - normalized Hamming distance between 64-bit pHashes. "
            "Captures STRUCTURAL similarity (luminance layout, "
            "edge orientation); degenerates on visually uniform "
            "content like solid-colour shots."
        ),
    )
    visual_color_mean_similarity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "1.0 - L1 distance between the two keyframes' mean RGB "
            "vectors, normalized to [0, 1] (a max L1 of 2.0 across "
            "RGB maps to 0 similarity). Strong on colour-only "
            "differences (red vs yellow)."
        ),
    )
    visual_color_histogram_similarity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Histogram intersection (a.k.a. Min kernel) between the "
            "two keyframes' per-channel normalized histograms. "
            "Captures the *distribution* of colours, not just the "
            "mean. Range [0, 1]."
        ),
    )
    transcript_similarity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Jaccard similarity over normalized token multisets.",
    )
    duration_similarity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "1.0 - relative duration difference, clipped to [0, 1]. "
            "Below TRIM_THRESHOLD we push it to 1.0 (we treat short "
            "duration deltas as the same shot for the trim detector)."
        ),
    )
    order_prior: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "1.0 when branch.sequence_index <= base.sequence_index "
            "in monotonic alignment; drops smoothly as the gap "
            "grows."
        ),
    )

    final_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Weighted blend, re-normalized over used components.",
    )
    used_components: list[str] = Field(
        default_factory=list,
        description="Names of components that contributed to final_score.",
    )


# ---------------------------------------------------------------------------
# Alignment match (one (base, branch) pair + inferred operation).
# ---------------------------------------------------------------------------


class AlignmentMatch(BaseModel):
    """One (base_shot, branch_shot) pair from the DP alignment.

    Either the base_shot or the branch_shot (or both) may be None:

    - branch_shot is None  → BASE shot was deleted in the branch.
    - base_shot is None    → branch shot is an insert (Phase 4+).
    - both present         → one of UNCHANGED / REPLACE / TRIM.

    `operation` is the inferred mechanical edit. `confidence` is in
    [0, 1]. `evidence` carries the component scores + the
    neighbouring matches (previous/next) so the inference is
    inspectable.
    """

    model_config = ConfigDict(extra="forbid")

    base_shot: ShotFingerprint | None = None
    branch_shot: ShotFingerprint | None = None
    similarity: SimilarityComponents
    operation: EditOperationType
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence in the inferred operation. Low-confidence "
            "matches have operation='uncertain' or operation='replace' "
            "with low visual+transcript agreement."
        ),
    )
    evidence: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "Structured evidence. Typical keys: 'previous_match', "
            "'next_match', 'reason', 'final_score', "
            "'operation_thresholds'."
        ),
    )


# ---------------------------------------------------------------------------
# Top-level alignment result.
# ---------------------------------------------------------------------------


class AlignmentResult(BaseModel):
    """Result of aligning one branch video against BASE.

    `matches` is ordered by base_shot.sequence_index (with inserts
    ordered by their branch_shot.sequence_index). Together the
    matches cover every shot in BASE plus any branch-only shots
    (inserts are surfaced as `uncertain` for now, per the user's
    brief).
    """

    model_config = ConfigDict(extra="forbid")

    branch_name: str
    base_video_id: str
    branch_video_id: str
    matches: list[AlignmentMatch] = Field(default_factory=list)
    weights: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Weights actually used for the blend (post missing-modality re-normalization)."
        ),
    )
    thresholds: dict[str, float] = Field(
        default_factory=dict,
        description="Operating thresholds (UNCHANGED_MIN, REPLACE_MIN, TRIM_MAX_REL_DIFF).",
    )


__all__ = [
    "ShotFingerprint",
    "SimilarityComponents",
    "AlignmentMatch",
    "AlignmentResult",
    "EditOperationType",
]
