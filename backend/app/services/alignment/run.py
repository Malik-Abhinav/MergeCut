"""Top-level Phase 3 orchestrator: `align_branch_to_base()`.

Takes two `VideoRepresentation`s (BASE, branch) and returns an
`AlignmentResult` containing one `AlignmentMatch` per (base,
branch) pair from the DP alignment, with edit-operation labels,
component scores, confidence, and evidence.

This is the only entry point Phase 4 / 5 / 6 need to call from
the backend. It is intentionally thin — all the heavy lifting
lives in `fingerprints`, `similarity`, `align`, and `edit_ops`.
"""

from __future__ import annotations

from app.models.alignment import (
    AlignmentMatch,
    AlignmentResult,
    SimilarityComponents,
)
from app.models.media import VideoRepresentation
from app.services.alignment.align import Transition, align_sequences
from app.services.alignment.edit_ops import (
    OperationThresholds,
    infer_confidence,
    infer_operation,
)
from app.services.alignment.fingerprints import build_fingerprints
from app.services.alignment.similarity import DEFAULT_WEIGHTS, blend


def align_branch_to_base(
    *,
    base: VideoRepresentation,
    branch: VideoRepresentation,
    branch_name: str = "branch",
    weights: dict[str, float] | None = None,
) -> AlignmentResult:
    """Align `branch` against `base` and return an `AlignmentResult`.

    Steps:
    1. Build `ShotFingerprint`s for both videos.
    2. DP-align the two fingerprint sequences (monotonic).
    3. For each transition, attach the `SimilarityComponents`
       (matches only) and the inferred `EditOperationType`.
    4. Attach confidence and structured evidence.

    The result is fully serializable (Pydantic, `extra="forbid"`)
    so the Phase 3 diagnostic dump and the eventual Phase 4 / 5
    consumers can both ingest it without further normalization.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    base_fps = build_fingerprints(base)
    branch_fps = build_fingerprints(branch)

    raw = align_sequences(base_fps, branch_fps, weights=weights)

    matches: list[AlignmentMatch] = []
    for trans, base_shot, branch_shot in raw:
        if trans == Transition.MATCH:
            assert base_shot is not None and branch_shot is not None
            sim = blend(base_shot, branch_shot, weights=weights)
            match = AlignmentMatch(
                base_shot=base_shot,
                branch_shot=branch_shot,
                similarity=sim,
                operation="uncertain",  # overwritten below
                confidence=0.0,
                evidence={},
            )
            operation, evidence = infer_operation(match)
            confidence = infer_confidence(match, operation)
            # Carry the operation_thresholds + neighbouring
            # matches so the diagnostic is self-contained.
            evidence["previous_match"] = matches[-1].operation if matches else "start"
            evidence["next_match"] = None  # filled in by post-pass below
            match = match.model_copy(
                update={
                    "operation": operation,
                    "confidence": confidence,
                    "evidence": evidence,
                }
            )
            matches.append(match)
        elif trans == Transition.DELETE:
            assert base_shot is not None
            # Pure delete: similarity is empty / placeholder.
            sim = SimilarityComponents(
                final_score=1.0,
                used_components=[],
            )
            match = AlignmentMatch(
                base_shot=base_shot,
                branch_shot=None,
                similarity=sim,
                operation="delete",
                confidence=1.0,
                evidence={
                    "reason": "branch_shot is None → delete",
                    "thresholds": OperationThresholds.as_dict(),
                    "previous_match": (matches[-1].operation if matches else "start"),
                    "next_match": None,
                },
            )
            matches.append(match)
        else:  # INSERT
            assert branch_shot is not None
            sim = SimilarityComponents(
                final_score=1.0,
                used_components=[],
            )
            match = AlignmentMatch(
                base_shot=None,
                branch_shot=branch_shot,
                similarity=sim,
                operation="insert",
                confidence=1.0,
                evidence={
                    "reason": ("base_shot is None → insert (uncertain in Phase 3)"),
                    "thresholds": OperationThresholds.as_dict(),
                    "previous_match": (matches[-1].operation if matches else "start"),
                    "next_match": None,
                },
            )
            matches.append(match)

    # Post-pass: fill in `next_match` for every entry so each
    # match's evidence carries both neighbours.
    for i, m in enumerate(matches):
        next_op = matches[i + 1].operation if i + 1 < len(matches) else "end"
        # model_copy preserves extra="forbid" so we cannot add
        # arbitrary keys; the dict update merges into the
        # existing evidence dict.
        new_evidence = {**m.evidence, "next_match": next_op}
        matches[i] = m.model_copy(update={"evidence": new_evidence})

    return AlignmentResult(
        branch_name=branch_name,
        base_video_id=base.video_id,
        branch_video_id=branch.video_id,
        matches=matches,
        weights=dict(weights),
        thresholds=OperationThresholds.as_dict(),
    )


__all__ = ["align_branch_to_base"]
