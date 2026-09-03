"""Context packaging for Phase 4 semantic analysis.

Given:

- BASE: a `VideoRepresentation`
- A vs BASE: an `AlignmentResult` (from Phase 3)
- B vs BASE: an `AlignmentResult` (from Phase 3)

produce a structured `SemanticContext` block that:

1. Enumerates the BASE shot timeline (sequence_index, start/end,
   transcript, keyframe).
2. Enumerates the A-side edits inferred by the alignment
   (delete, replace, trim, uncertain) with timestamps and the
   corresponding BASE shot.
3. Enumerates the B-side edits.
4. Reconstructs Branch A's full content (BASE with A's edit
   applied: deleted shots → "[DELETED]", replaced shots →
   "[REPLACED — see edit op]").
5. Reconstructs Branch B's full content (same idea).
6. Surfaces candidate cross-edit pairs (all-pairs for the MVP).

The user explicitly said:

> The alignment layer determines WHAT changed.
> M3 determines WHAT THOSE CHANGES MEAN.
> Keep those responsibilities separate.

So this module does not interpret semantics. It serializes the
mechanical facts and lets M3 do the meaning work.

The output is a Pydantic model (`SemanticContext`) so it can be
JSON-serialized for diagnostics and round-tripped through the
M3 prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.models.alignment import AlignmentResult
from app.models.media import VideoRepresentation

# ---------------------------------------------------------------------------
# Context data structures.
# ---------------------------------------------------------------------------


class BaseShotInfo(BaseModel):
    """One BASE shot, with its transcript and keyframe path."""

    model_config = ConfigDict(extra="forbid")

    shot_id: str
    sequence_index: int = Field(ge=0)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    duration: float = Field(ge=0.0)
    transcript: str
    keyframe_path: str | None = None


class EditInfo(BaseModel):
    """One inferred mechanical edit (one `AlignmentMatch` from Phase 3).

    For deletes / inserts the corresponding shot is the
    `affected_shot` on the side that has the shot.
    """

    model_config = ConfigDict(extra="forbid")

    branch: str
    operation: str
    base_shot_id: str | None = None
    base_shot_sequence_index: int | None = None
    branch_shot_id: str | None = None
    branch_shot_sequence_index: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    visual_similarity: float | None = None
    transcript_similarity: float | None = None
    duration_similarity: float | None = None


class ReconstructedBranchContent(BaseModel):
    """The full reconstructed content of a branch (BASE + that branch's edit)."""

    model_config = ConfigDict(extra="forbid")

    branch: str
    lines: list[str] = Field(
        description=(
            "Ordered lines. Each line is either a shot-segment "
            "('shot_0000 [00:00.000–00:03.000] text...') or a "
            "gap marker ('shot_0001 [00:03.000–00:06.000] "
            "[DELETED in branch_a]')."
        )
    )


class CandidatePair(BaseModel):
    """One (A-edit, B-edit) candidate pair for cross-edit analysis.

    For the MVP we use all-pairs: every A-side edit is paired
    with every B-side edit. The user explicitly approved this
    for the MVP because "videos are short in the MVP, an
    all-pairs comparison is acceptable initially if the number
    of edits is small and easier to validate."
    """

    model_config = ConfigDict(extra="forbid")

    branch_a_edit: EditInfo
    branch_b_edit: EditInfo
    rationale: str = Field(default="all-pairs (MVP): every A edit is paired with every B edit.")


class SemanticContext(BaseModel):
    """The full context block sent to M3 for one BASE / A / B triple."""

    model_config = ConfigDict(extra="forbid")

    base_video_id: str
    branch_a_video_id: str
    branch_b_video_id: str
    base_shots: list[BaseShotInfo]
    branch_a_edits: list[EditInfo]
    branch_b_edits: list[EditInfo]
    branch_a_reconstructed: ReconstructedBranchContent
    branch_b_reconstructed: ReconstructedBranchContent
    candidate_pairs: list[CandidatePair]
    notes: str | None = None


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _seconds_to_ts(seconds: float) -> str:
    """Format seconds as [mm:ss.mmm]."""
    if seconds < 0:
        seconds = 0.0
    m, s = divmod(seconds, 60)
    return f"{int(m):02d}:{s:06.3f}"


def _normalize_ws(s: str) -> str:
    """Collapse whitespace and trim."""
    return re.sub(r"\s+", " ", s or "").strip()


def _shot_info_for_base(rep: VideoRepresentation) -> list[BaseShotInfo]:
    out: list[BaseShotInfo] = []
    for idx, shot in enumerate(rep.shots):
        kf = shot.keyframe_paths[0] if shot.keyframe_paths else None
        out.append(
            BaseShotInfo(
                shot_id=shot.shot_id,
                sequence_index=idx,
                start=shot.start,
                end=shot.end,
                duration=max(0.0, shot.end - shot.start),
                transcript=_normalize_ws(shot.transcript),
                keyframe_path=str(kf) if kf is not None else None,
            )
        )
    return out


def _edits_from_alignment(
    branch_name: str,
    result: AlignmentResult,
) -> list[EditInfo]:
    out: list[EditInfo] = []
    for m in result.matches:
        out.append(
            EditInfo(
                branch=branch_name,  # type: ignore[arg-type]
                operation=m.operation,
                base_shot_id=m.base_shot.shot_id if m.base_shot else None,
                base_shot_sequence_index=m.base_shot.sequence_index if m.base_shot else None,
                branch_shot_id=m.branch_shot.shot_id if m.branch_shot else None,
                branch_shot_sequence_index=m.branch_shot.sequence_index if m.branch_shot else None,
                confidence=m.confidence,
                visual_similarity=m.similarity.visual_similarity,
                transcript_similarity=m.similarity.transcript_similarity,
                duration_similarity=m.similarity.duration_similarity,
            )
        )
    return out


def _reconstruct_branch_content(
    base: VideoRepresentation,
    edits: list[EditInfo],
    *,
    branch: str,
) -> ReconstructedBranchContent:
    """Render the full reconstructed branch content.

    For every BASE shot, produce a line that either:
    - quotes the BASE transcript (when the edit operation is
      `unchanged`, `trim`, `uncertain`, or absent), or
    - marks the shot `[DELETED in branch_X]`, or
    - marks it `[REPLACED in branch_X — see edit list]`.

    The output is intentionally text-shaped so M3 can apply
    the same per-branch safety reasoning that the v1 contract
    used, but anchored on the Phase 3 alignment result.
    """
    by_seq: dict[int, EditInfo] = {}
    for e in edits:
        if e.base_shot_sequence_index is not None:
            by_seq[e.base_shot_sequence_index] = e

    lines: list[str] = []
    for idx, shot in enumerate(base.shots):
        ts = f"{_seconds_to_ts(shot.start)}–{_seconds_to_ts(shot.end)}"
        text = _normalize_ws(shot.transcript)
        edit = by_seq.get(idx)
        if edit is None:
            op = "unchanged"
        else:
            op = edit.operation
        if op in {"delete", "insert"} or (branch == "branch_a" and edit and op == "delete"):
            lines.append(f"{shot.shot_id} [{ts}] [DELETED in {branch}]")
        elif op == "replace":
            lines.append(f"{shot.shot_id} [{ts}] [REPLACED in {branch} — see edit list]")
        elif op == "trim":
            lines.append(f"{shot.shot_id} [{ts}] [TRIMMED in {branch}] {text}")
        elif op == "uncertain":
            lines.append(f"{shot.shot_id} [{ts}] [UNCERTAIN in {branch}] {text}")
        else:  # unchanged
            lines.append(f"{shot.shot_id} [{ts}] {text}")
    return ReconstructedBranchContent(branch=branch, lines=lines)  # type: ignore[arg-type]


def _candidate_pairs(
    a_edits: list[EditInfo],
    b_edits: list[EditInfo],
) -> list[CandidatePair]:
    """All-pairs cross product of A and B edits (MVP)."""
    pairs: list[CandidatePair] = []
    for a in a_edits:
        for b in b_edits:
            pairs.append(CandidatePair(branch_a_edit=a, branch_b_edit=b))
    return pairs


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def build_semantic_context(
    *,
    base: VideoRepresentation,
    branch_a_alignment: AlignmentResult,
    branch_b_alignment: AlignmentResult,
) -> SemanticContext:
    """Build the full context block from the Phase 3 alignment results.

    The block is everything M3 needs to evaluate the two-axis
    taxonomy. The orchestrator (`app.services.semantic.run`)
    renders it as the M3 user payload.
    """
    base_shots = _shot_info_for_base(base)
    a_edits = _edits_from_alignment("branch_a", branch_a_alignment)
    b_edits = _edits_from_alignment("branch_b", branch_b_alignment)
    a_recon = _reconstruct_branch_content(base, a_edits, branch="branch_a")
    b_recon = _reconstruct_branch_content(base, b_edits, branch="branch_b")
    pairs = _candidate_pairs(a_edits, b_edits)
    return SemanticContext(
        base_video_id=base.video_id,
        branch_a_video_id=branch_a_alignment.branch_video_id,
        branch_b_video_id=branch_b_alignment.branch_video_id,
        base_shots=base_shots,
        branch_a_edits=a_edits,
        branch_b_edits=b_edits,
        branch_a_reconstructed=a_recon,
        branch_b_reconstructed=b_recon,
        candidate_pairs=pairs,
    )


# ---------------------------------------------------------------------------
# Text rendering for the M3 user payload.
# ---------------------------------------------------------------------------


@dataclass
class _Section:
    title: str
    body: str


def render_context_for_prompt(ctx: SemanticContext) -> str:
    """Render the SemanticContext as a single text block for the M3 prompt.

    The text is the user-payload portion of the M3 call. The
    system intent (in `prompts_v2.py`) tells M3 what to do
    with it; this function decides *what* M3 sees.

    Sections, in order:
    1. BASE FULL CONTENT — the BASE shot timeline + transcripts.
    2. BRANCH A MECHANICAL EDITS — every A-side AlignmentMatch.
    3. BRANCH B MECHANICAL EDITS — every B-side AlignmentMatch.
    4. BRANCH A FULL CONTENT — reconstructed (BASE + A's edit).
    5. BRANCH B FULL CONTENT — reconstructed (BASE + B's edit).
    6. CANDIDATE PAIRS — for the MVP, the full A×B cross
       product. The orchestrator can trim to "interesting
       pairs" in a future phase.
    """
    out: list[str] = []
    out.append("=" * 60)
    out.append("BASE FULL CONTENT (the original video)")
    out.append("=" * 60)
    for s in ctx.base_shots:
        ts = f"{_seconds_to_ts(s.start)}–{_seconds_to_ts(s.end)}"
        out.append(f"  {s.shot_id} [{ts}]  {s.transcript or '(no transcript)'}")

    for branch_name, edits in (
        ("BRANCH A", ctx.branch_a_edits),
        ("BRANCH B", ctx.branch_b_edits),
    ):
        out.append("")
        out.append("=" * 60)
        out.append(f"{branch_name} MECHANICAL EDITS (from Phase 3 alignment)")
        out.append("=" * 60)
        for e in edits:
            v = (
                f"visual={e.visual_similarity:.2f}"
                if e.visual_similarity is not None
                else "visual=None"
            )
            t = (
                f"transcript={e.transcript_similarity:.2f}"
                if e.transcript_similarity is not None
                else "transcript=None"
            )
            base_id = e.base_shot_id or "—"
            branch_id = e.branch_shot_id or "—"
            out.append(
                f"  {branch_name} {e.operation:9s}  base={base_id:9s}  "
                f"branch={branch_id:9s}  conf={e.confidence:.2f}  {v}  {t}"
            )

    for recon in (ctx.branch_a_reconstructed, ctx.branch_b_reconstructed):
        out.append("")
        out.append("=" * 60)
        out.append(
            f"{recon.branch.upper()} FULL CONTENT (BASE after applying ONLY that branch's edit)"
        )
        out.append("=" * 60)
        for line in recon.lines:
            out.append(f"  {line}")

    out.append("")
    out.append("=" * 60)
    out.append("CANDIDATE PAIRS (cross-edit interactions to consider)")
    out.append("=" * 60)
    for i, p in enumerate(ctx.candidate_pairs, 1):
        out.append(
            f"  Pair {i:02d}: A.{p.branch_a_edit.operation}@"
            f"{p.branch_a_edit.base_shot_id or '?'}"
            f"  ×  B.{p.branch_b_edit.operation}@"
            f"{p.branch_b_edit.base_shot_id or '?'}"
        )

    if ctx.notes:
        out.append("")
        out.append(f"NOTE: {ctx.notes}")
    return "\n".join(out)


__all__ = [
    "BaseShotInfo",
    "EditInfo",
    "ReconstructedBranchContent",
    "CandidatePair",
    "SemanticContext",
    "build_semantic_context",
    "render_context_for_prompt",
]
