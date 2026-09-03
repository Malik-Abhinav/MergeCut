"""Unit tests for `app.services.semantic.context`.

Covers the deterministic parts of the context packager:
- BASE shot enumeration
- alignment -> EditInfo conversion
- branch-content reconstruction (text + gap markers)
- all-pairs candidate generation
- text rendering for the M3 prompt

These tests use synthetic `VideoRepresentation`s (no real
videos) so the suite is fast and deterministic.
"""

from __future__ import annotations

from pathlib import Path

from app.models.alignment import (
    AlignmentMatch,
    AlignmentResult,
    ShotFingerprint,
    SimilarityComponents,
)
from app.models.media import Shot, VideoMetadata, VideoRepresentation
from app.services.alignment.edit_ops import OperationThresholds
from app.services.semantic.context import (
    build_semantic_context,
    render_context_for_prompt,
)

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _make_rep(video_id: str, *, transcripts: list[str]) -> VideoRepresentation:
    metadata = VideoMetadata(
        duration_seconds=float(len(transcripts)),
        width=320,
        height=240,
        fps=30.0,
        codec="h264",
        audio_present=bool(transcripts),
    )
    shots = [
        Shot(
            shot_id=f"shot_{i:04d}",
            start=float(i),
            end=float(i) + 1.0,
            keyframe_paths=[],
            transcript=text,
            transcript_segments=[],
        )
        for i, text in enumerate(transcripts)
    ]
    return VideoRepresentation.from_components(
        video_id=video_id,
        source_path=Path(f"/tmp/{video_id}.mp4"),
        normalized_path=Path(f"/tmp/{video_id}.working.mp4"),
        audio_path=None,
        metadata=metadata,
        normalization=__import__(
            "app.models.media", fromlist=["NormalizationInfo"]
        ).NormalizationInfo(normalized=False),
        shots=shots,
    )


def _sim(
    visual: float | None = 1.0,
    transcript: float | None = 1.0,
    duration: float = 1.0,
    order: float = 1.0,
) -> SimilarityComponents:
    return SimilarityComponents(
        visual_similarity=visual,
        transcript_similarity=transcript,
        duration_similarity=duration,
        order_prior=order,
        final_score=0.9,
        used_components=[
            "visual_similarity",
            "transcript_similarity",
            "duration_similarity",
            "order_prior",
        ],
    )


def _match(
    *,
    base_idx: int | None,
    branch_idx: int | None,
    op: str,
    confidence: float = 0.9,
) -> AlignmentMatch:
    base = (
        ShotFingerprint(
            shot_id=f"shot_{base_idx:04d}",
            start=float(base_idx),
            end=float(base_idx) + 1.0,
            duration=1.0,
            keyframe_paths=[],
            visual_fingerprint="abcd1234",
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
        confidence=confidence,
        evidence={"reason": f"synthetic {op}"},
    )


def _alignment(
    matches: list[AlignmentMatch], *, branch_video_id: str = "branch"
) -> AlignmentResult:
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


# ---------------------------------------------------------------------------
# BASE shot enumeration.
# ---------------------------------------------------------------------------


def test_base_shot_info_captures_transcripts() -> None:
    base = _make_rep("base", transcripts=["hello world", "second shot", ""])
    a_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="unchanged")], branch_video_id="a"
    )
    b_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="unchanged")], branch_video_id="b"
    )
    ctx = build_semantic_context(
        base=base, branch_a_alignment=a_alignment, branch_b_alignment=b_alignment
    )
    assert len(ctx.base_shots) == 3
    assert [s.transcript for s in ctx.base_shots] == ["hello world", "second shot", ""]
    assert [s.shot_id for s in ctx.base_shots] == ["shot_0000", "shot_0001", "shot_0002"]


# ---------------------------------------------------------------------------
# EditInfo conversion.
# ---------------------------------------------------------------------------


def test_edits_from_alignment_includes_all_matches() -> None:
    base = _make_rep("base", transcripts=["x", "y", "z"])
    a_alignment = _alignment(
        [
            _match(base_idx=0, branch_idx=0, op="unchanged"),
            _match(base_idx=1, branch_idx=None, op="delete"),
            _match(base_idx=2, branch_idx=1, op="replace"),
        ],
        branch_video_id="a",
    )
    b_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="unchanged")],
        branch_video_id="b",
    )
    ctx = build_semantic_context(
        base=base, branch_a_alignment=a_alignment, branch_b_alignment=b_alignment
    )
    ops = [e.operation for e in ctx.branch_a_edits]
    assert ops == ["unchanged", "delete", "replace"]


# ---------------------------------------------------------------------------
# Reconstructed branch content.
# ---------------------------------------------------------------------------


def test_reconstruction_marks_deleted_shots() -> None:
    base = _make_rep("base", transcripts=["first", "second", "third"])
    a_alignment = _alignment(
        [
            _match(base_idx=0, branch_idx=0, op="unchanged"),
            _match(base_idx=1, branch_idx=None, op="delete"),
            _match(base_idx=2, branch_idx=1, op="unchanged"),
        ],
        branch_video_id="a",
    )
    b_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="unchanged")],
        branch_video_id="b",
    )
    ctx = build_semantic_context(
        base=base, branch_a_alignment=a_alignment, branch_b_alignment=b_alignment
    )
    lines = ctx.branch_a_reconstructed.lines
    # Base shot 1 is deleted in branch_a.
    assert any("shot_0001" in line and "DELETED" in line for line in lines)
    # And shot 0 + 2 carry the BASE text.
    assert any("shot_0000" in line and "first" in line for line in lines)
    assert any("shot_0002" in line and "third" in line for line in lines)


def test_reconstruction_marks_replaced_shots() -> None:
    base = _make_rep("base", transcripts=["first"])
    a_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="replace")],
        branch_video_id="a",
    )
    b_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="unchanged")],
        branch_video_id="b",
    )
    ctx = build_semantic_context(
        base=base, branch_a_alignment=a_alignment, branch_b_alignment=b_alignment
    )
    lines = ctx.branch_a_reconstructed.lines
    assert any("REPLACED" in line for line in lines)


def test_reconstruction_marks_trimmed_shots() -> None:
    base = _make_rep("base", transcripts=["hello"])
    a_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="trim")],
        branch_video_id="a",
    )
    b_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="unchanged")],
        branch_video_id="b",
    )
    ctx = build_semantic_context(
        base=base, branch_a_alignment=a_alignment, branch_b_alignment=b_alignment
    )
    lines = ctx.branch_a_reconstructed.lines
    assert any("TRIMMED" in line and "hello" in line for line in lines)


# ---------------------------------------------------------------------------
# Candidate pairs.
# ---------------------------------------------------------------------------


def test_candidate_pairs_all_pairs_default() -> None:
    base = _make_rep("base", transcripts=["x", "y", "z"])
    a_alignment = _alignment(
        [
            _match(base_idx=0, branch_idx=0, op="delete"),
            _match(base_idx=1, branch_idx=0, op="replace"),
        ],
        branch_video_id="a",
    )
    b_alignment = _alignment(
        [
            _match(base_idx=0, branch_idx=0, op="replace"),
            _match(base_idx=2, branch_idx=1, op="delete"),
        ],
        branch_video_id="b",
    )
    ctx = build_semantic_context(
        base=base, branch_a_alignment=a_alignment, branch_b_alignment=b_alignment
    )
    # 2 A-edits × 2 B-edits = 4 pairs.
    assert len(ctx.candidate_pairs) == 4
    # All-pairs rationale is the default for the MVP.
    assert all(p.rationale.startswith("all-pairs") for p in ctx.candidate_pairs)


# ---------------------------------------------------------------------------
# Text rendering.
# ---------------------------------------------------------------------------


def test_render_includes_all_sections() -> None:
    base = _make_rep("base", transcripts=["hello", "world"])
    a_alignment = _alignment(
        [
            _match(base_idx=0, branch_idx=0, op="unchanged"),
            _match(base_idx=1, branch_idx=None, op="delete"),
        ],
        branch_video_id="a",
    )
    b_alignment = _alignment(
        [
            _match(base_idx=0, branch_idx=0, op="unchanged"),
            _match(base_idx=1, branch_idx=0, op="replace"),
        ],
        branch_video_id="b",
    )
    ctx = build_semantic_context(
        base=base, branch_a_alignment=a_alignment, branch_b_alignment=b_alignment
    )
    text = render_context_for_prompt(ctx)
    assert "BASE FULL CONTENT" in text
    assert "BRANCH A MECHANICAL EDITS" in text
    assert "BRANCH B MECHANICAL EDITS" in text
    assert "BRANCH_A FULL CONTENT" in text
    assert "BRANCH_B FULL CONTENT" in text
    assert "CANDIDATE PAIRS" in text
    # BASE shot text shows up in the BASE FULL CONTENT section.
    assert "hello" in text
    assert "world" in text
    # The deleted shot is marked.
    assert "DELETED" in text
    # The replaced shot is marked.
    assert "REPLACED" in text


def test_render_preserves_prompt_version() -> None:
    """The user-payload builder must include PROMPT_VERSION so
    we can identify which contract produced each analysis."""
    from app.services.semantic.prompts_v2 import PROMPT_VERSION, build_user_payload

    base = _make_rep("base", transcripts=["hi"])
    a_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="unchanged")], branch_video_id="a"
    )
    b_alignment = _alignment(
        [_match(base_idx=0, branch_idx=0, op="unchanged")], branch_video_id="b"
    )
    ctx = build_semantic_context(
        base=base, branch_a_alignment=a_alignment, branch_b_alignment=b_alignment
    )
    payload = build_user_payload(ctx)
    assert PROMPT_VERSION in payload
