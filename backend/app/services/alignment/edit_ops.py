"""Edit-operation inference.

Given an `AlignmentMatch` (one (base_shot, branch_shot) pair
with its SimilarityComponents), infer one of:

  - `unchanged` : the same shot preserved end-to-end.
  - `replace`   : the branch shot is a different shot (visuals
                  and/or transcript clearly different) at the
                  same position.
  - `trim`      : the branch shot looks like the same shot,
                  just shortened or extended.
  - `delete`    : base_shot present, branch_shot is None
                  (transition was DELETE).
  - `insert`    : branch_shot present, base_shot is None
                  (transition was INSERT — surfaced as
                  `uncertain` in this Phase; the user's brief
                  says INSERT is stretch-only).
  - `uncertain` : we don't have enough signal to pick one.

The thresholds live in `OPERATION_THRESHOLDS` and are the single
source of truth used by tests + the diagnostic dumps.

Decision rules (per the brief):

  1. base_shot is None                       → insert (uncertain).
  2. branch_shot is None                     → delete.
  3. visual_similarity ≥ UNCHANGED_MIN
     and transcript_similarity ≥ UNCHANGED_MIN   → unchanged.
     (transcript_similarity check skipped when either side
     lacks speech — see below.)
  4. visual_similarity ≥ REPLACE_MIN and
     transcript_similarity ≥ REPLACE_MIN        → replace.
     (transcript_similarity check skipped when either side
     lacks speech — see below.)
  5. relative_duration_diff < TRIM_MAX_REL_DIFF
     and visual_similarity ≥ TRIM_MIN_VISUAL    → trim.
  6. else → uncertain.

Missing-modality handling:

- If both shots lack speech, we drop the transcript check from
  steps 3–4 (the transcript component is None on both sides, so
  the similarity blend already re-normalized). The visual
  threshold alone decides between unchanged / replace / trim /
  uncertain.
- If exactly one shot lacks speech (genuine modality mismatch),
  we treat transcript_similarity as 0.0 when applying the
  rules above. A 0.0 transcript score combined with strong
  visual agreement can still mark unchanged; combined with
  weak visual agreement it falls through to uncertain.

Confidence is `final_score` for matches and a hardcoded 1.0 for
pure deletes / pure inserts (the DP committed to those, no
similarity involved).
"""

from __future__ import annotations

from typing import Any

from app.models.alignment import AlignmentMatch, EditOperationType
from app.services.alignment.similarity import (
    TRIM_MAX_REL_DIFF,
    relative_duration_diff,
)


class OperationThresholds:
    """Operating thresholds. Pinned for the Phase 3 acceptance gate."""

    UNCHANGED_MIN: float = 0.85  # visual AND transcript must be ≥ this for unchanged
    UNCHANGED_MAX_REL_DIFF: float = 0.10
    REPLACE_MIN: float = 0.50  # both visuals and transcript must exceed the "same" zone
    TRIM_MIN_VISUAL: float = 0.85  # trim requires strong visual agreement
    # The maximum tolerated relative-duration difference for trim
    # is `TRIM_MAX_REL_DIFF` (imported from `similarity`).
    # A weaker threshold is applied below — see trim rule.

    @classmethod
    def as_dict(cls) -> dict[str, float]:
        return {
            "UNCHANGED_MIN": cls.UNCHANGED_MIN,
            "UNCHANGED_MAX_REL_DIFF": cls.UNCHANGED_MAX_REL_DIFF,
            "REPLACE_MIN": cls.REPLACE_MIN,
            "TRIM_MIN_VISUAL": cls.TRIM_MIN_VISUAL,
            "TRIM_MAX_REL_DIFF": TRIM_MAX_REL_DIFF,
        }


# Convenience alias so callers can `from edit_ops import THRESHOLDS`.
THRESHOLDS = OperationThresholds.as_dict()


def _transcript_check(
    *,
    has_speech: bool | None,
    transcript_sim: float | None,
    threshold: float,
) -> bool:
    """Return True if the transcript signal agrees with the operation.

    - No speech on either side: pass (signal absent, drop the check).
    - Transcript similarity present: compare to threshold.
    """
    if has_speech is None:
        return True  # both sides lack speech — drop the check
    if transcript_sim is None:
        return False  # one side has speech, the other doesn't — fail the check
    return transcript_sim >= threshold


def infer_operation(match: AlignmentMatch) -> tuple[EditOperationType, dict[str, Any]]:
    """Return (operation, evidence) for one match.

    `evidence` is a plain-dict blob that the `AlignmentResult`
    keeps for diagnostics. Includes the per-component scores,
    thresholds, and the rule that fired.
    """
    base = match.base_shot
    branch = match.branch_shot
    sim = match.similarity

    thresholds = OperationThresholds.as_dict()
    visual = sim.visual_similarity
    transcript = sim.transcript_similarity
    rel_dur_diff = (
        relative_duration_diff(base, branch) if base is not None and branch is not None else 0.0
    )

    evidence: dict[str, Any] = {
        "visual_similarity": visual,
        "transcript_similarity": transcript,
        "duration_similarity": sim.duration_similarity,
        "final_score": sim.final_score,
        "used_components": sim.used_components,
        "relative_duration_diff": rel_dur_diff,
        "thresholds": thresholds,
    }

    # 1. INSERT: branch-only.
    if base is None and branch is not None:
        evidence["reason"] = "base_shot is None → insert (uncertain in Phase 3)"
        return ("insert", evidence)

    # 2. DELETE: base-only.
    if branch is None and base is not None:
        evidence["reason"] = "branch_shot is None → delete"
        return ("delete", evidence)

    # Defensive: no shots at all — degenerate.
    if base is None and branch is None:
        evidence["reason"] = "both shots None — degenerate match"
        return ("uncertain", evidence)

    # 3 / 4 / 5 / 6: both shots present.
    assert base is not None and branch is not None
    has_speech: bool | None
    if base.has_speech and branch.has_speech:
        has_speech = True
    elif not base.has_speech and not branch.has_speech:
        has_speech = None
    else:
        has_speech = False

    # Unchanged is decided before trim. Encoding and ASR boundaries
    # routinely move a retained shot by a few frames; a small duration
    # delta alone must not turn otherwise identical content into TRIM.
    if (
        rel_dur_diff <= thresholds["UNCHANGED_MAX_REL_DIFF"]
        and visual is not None
        and visual >= thresholds["UNCHANGED_MIN"]
    ):
        if _transcript_check(
            has_speech=has_speech,
            transcript_sim=transcript,
            threshold=thresholds["UNCHANGED_MIN"],
        ):
            evidence["reason"] = (
                f"visual_similarity={visual:.3f} >= "
                f"{thresholds['UNCHANGED_MIN']} AND transcript check passed → unchanged"
            )
            return ("unchanged", evidence)

    # A trim requires both a real duration change and evidence that
    # the surviving speech is still substantially the same content.
    if (
        rel_dur_diff > 0.0
        and rel_dur_diff < TRIM_MAX_REL_DIFF
        and visual is not None
        and visual >= thresholds["TRIM_MIN_VISUAL"]
        and _transcript_check(
            has_speech=has_speech,
            transcript_sim=transcript,
            threshold=thresholds["REPLACE_MIN"],
        )
    ):
        evidence["reason"] = (
            f"0 < relative_duration_diff={rel_dur_diff:.3f} < "
            f"{TRIM_MAX_REL_DIFF}, visual_similarity={visual:.3f} >= "
            f"{thresholds['TRIM_MIN_VISUAL']}, and transcript remains related → trim"
        )
        return ("trim", evidence)

    # Replacement is either a visually changed rendering of related
    # speech, or strongly BASE-anchored visuals carrying substantially
    # different speech. Requiring high transcript similarity here was
    # backwards: it classified real rewrites as uncertain/trim.
    if visual is not None and visual >= thresholds["REPLACE_MIN"]:
        transcript_diverged = transcript is not None and transcript < thresholds["REPLACE_MIN"]
        visual_changed = visual < thresholds["UNCHANGED_MIN"]
        if transcript_diverged or visual_changed:
            evidence["reason"] = (
                f"visual_similarity={visual:.3f} anchors the BASE unit and "
                f"transcript_similarity={transcript} or visual change indicates replacement"
            )
            return ("replace", evidence)

    # Step 6: uncertain.
    evidence["reason"] = (
        f"no rule matched: visual={visual}, transcript={transcript}, "
        f"rel_dur_diff={rel_dur_diff:.3f}"
    )
    return ("uncertain", evidence)


def infer_confidence(
    match: AlignmentMatch,
    operation: EditOperationType,
) -> float:
    """Confidence in the inferred operation.

    - Pure delete / pure insert: 1.0 (the DP committed, no
      similarity is involved).
    - Otherwise: the blend's `final_score`, optionally floored at
      0.5 for `replace` (we don't want low-confidence replaces to
      look like they passed).
    """
    if operation in ("delete", "insert"):
        return 1.0
    return match.similarity.final_score


__all__ = [
    "OperationThresholds",
    "THRESHOLDS",
    "infer_confidence",
    "infer_operation",
]
