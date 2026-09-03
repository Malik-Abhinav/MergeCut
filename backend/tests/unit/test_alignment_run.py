"""Unit tests for `app.services.alignment.run` (orchestrator).

The acceptance gate for Phase 3 is in PROJECT_PLAN §29:

> On controlled fixtures correctly identify:
> - one deletion
> - one replacement
> - unchanged segments

We exercise this with synthetic `VideoRepresentation`s (no real
videos) so the tests are fast and deterministic. Real-video
integration tests against the controlled fixtures in
`tests/fixtures/alignment_fixtures.py` live in
`test_alignment_integration.py` and are marked so they only run
when the fixture builder has been invoked.
"""

from __future__ import annotations

from pathlib import Path

from app.models.media import (
    NormalizationInfo,
    Shot,
    VideoMetadata,
    VideoRepresentation,
)
from app.services.alignment.run import align_branch_to_base

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _shot(
    *,
    idx: int,
    start: float,
    end: float,
    transcript: str = "",
    colour_hint: str = "red",
) -> Shot:
    """Build a `Shot` whose keyframe path encodes the colour in its
    filename. The alignment layer reads the first keyframe and
    derives a pHash from it, so two different colours → different
    hashes → low visual similarity.

    For "unchanged" tests we point base and branch at the *same*
    keyframe file (so the hash is identical) — we just reuse
    `tmp_path` and write the same PNG twice.
    """
    return Shot(
        shot_id=f"shot_{idx:04d}",
        start=start,
        end=end,
        keyframe_paths=[],
        transcript=transcript,
        transcript_segments=[],
    )


def _make_keyframe(tmp_path: Path, name: str, rgb: tuple[int, int, int]) -> Path:
    from PIL import Image

    p = tmp_path / f"{name}.png"
    Image.new("RGB", (32, 32), rgb).save(p)
    return p


def _make_solid(tmp_path: Path, name: str, rgb: tuple[int, int, int]) -> Path:
    return _make_keyframe(tmp_path, name, rgb)


def _make_checker(
    tmp_path: Path, name: str, a: tuple[int, int, int], b: tuple[int, int, int]
) -> Path:
    from PIL import Image

    p = tmp_path / f"{name}.png"
    im = Image.new("RGB", (32, 32), a)
    px = im.load()
    for y in range(32):
        for x in range(32):
            if (x // 4 + y // 4) % 2 == 0:
                px[x, y] = a
            else:
                px[x, y] = b
    im.save(p)
    return p


def _make_stripes(
    tmp_path: Path, name: str, a: tuple[int, int, int], b: tuple[int, int, int]
) -> Path:
    from PIL import Image

    p = tmp_path / f"{name}.png"
    im = Image.new("RGB", (32, 32), a)
    px = im.load()
    for y in range(32):
        for x in range(32):
            if (x // 4) % 2 == 0:
                px[x, y] = a
            else:
                px[x, y] = b
    im.save(p)
    return p


def _build_rep_from_paths(
    tmp_path: Path,
    *,
    video_id: str,
    keyframe_paths: list[Path],
    transcripts: list[str] | None = None,
) -> VideoRepresentation:
    """Build a `VideoRepresentation` from pre-existing keyframe files."""
    transcripts = transcripts or ["" for _ in keyframe_paths]
    shots: list[Shot] = []
    for i, (kf, text) in enumerate(zip(keyframe_paths, transcripts, strict=True)):
        shot = Shot(
            shot_id=f"shot_{i:04d}",
            start=float(i),
            end=float(i) + 1.0,
            keyframe_paths=[kf],
            transcript=text,
            transcript_segments=[],
        )
        shots.append(shot)
    metadata = VideoMetadata(
        duration_seconds=float(len(keyframe_paths)),
        width=32,
        height=32,
        fps=30.0,
        codec="h264",
        audio_present=False,
    )
    return VideoRepresentation.from_components(
        video_id=video_id,
        source_path=tmp_path / f"{video_id}.mp4",
        normalized_path=tmp_path / f"{video_id}.working.mp4",
        audio_path=None,
        metadata=metadata,
        normalization=NormalizationInfo(normalized=False),
        shots=shots,
    )


def _build_rep(
    tmp_path: Path,
    *,
    video_id: str,
    keyframes: list[tuple[int, tuple[int, int, int]]],
    transcripts: list[str] | None = None,
) -> VideoRepresentation:
    """Build a `VideoRepresentation` with one shot per keyframe.

    `keyframes` is a list of (idx, RGB) — `idx` is the shot
    sequence index and RGB determines the pHash.
    `transcripts` (optional) provides the transcript text per shot.
    """
    shots: list[Shot] = []
    transcripts = transcripts or ["" for _ in keyframes]
    for (idx, rgb), text in zip(keyframes, transcripts, strict=True):
        kf = _make_keyframe(tmp_path, f"{video_id}_shot{idx}", rgb)
        shot = Shot(
            shot_id=f"shot_{idx:04d}",
            start=float(idx),
            end=float(idx) + 1.0,
            keyframe_paths=[kf],
            transcript=text,
            transcript_segments=[],
        )
        shots.append(shot)
    metadata = VideoMetadata(
        duration_seconds=float(len(keyframes)),
        width=32,
        height=32,
        fps=30.0,
        codec="h264",
        audio_present=False,
    )
    return VideoRepresentation.from_components(
        video_id=video_id,
        source_path=tmp_path / f"{video_id}.mp4",
        normalized_path=tmp_path / f"{video_id}.working.mp4",
        audio_path=None,
        metadata=metadata,
        normalization=NormalizationInfo(normalized=False),
        shots=shots,
    )


def _ops(result) -> list[str]:
    return [m.operation for m in result.matches]


# ---------------------------------------------------------------------------
# Phase 3 acceptance: one deletion.
# ---------------------------------------------------------------------------


def test_acceptance_one_deletion(tmp_path: Path) -> None:
    """Branch drops shot 1 (the white/second shot)."""
    red = _make_solid(tmp_path, "red", (255, 0, 0))
    white = _make_solid(tmp_path, "white", (255, 255, 255))
    green = _make_solid(tmp_path, "green", (0, 255, 0))
    base = _build_rep_from_paths(tmp_path, video_id="base", keyframe_paths=[red, white, green])
    branch = _build_rep_from_paths(tmp_path, video_id="branch_del", keyframe_paths=[red, green])
    result = align_branch_to_base(base=base, branch=branch, branch_name="A")

    # 3 transitions in base: shot 0 (unchanged), shot 1 (delete), shot 2 (unchanged)
    assert _ops(result) == ["unchanged", "delete", "unchanged"]
    delete_match = result.matches[1]
    assert delete_match.base_shot is not None
    assert delete_match.branch_shot is None
    assert delete_match.operation == "delete"
    assert delete_match.confidence == 1.0


# ---------------------------------------------------------------------------
# Phase 3 acceptance: one replacement.
# ---------------------------------------------------------------------------


def test_acceptance_one_replacement(tmp_path: Path) -> None:
    """Branch keeps all 3 shots but shot 1 is replaced.

    We construct the test so the middle shot differs in BOTH
    the visual fingerprint (different colour envelope) AND the
    transcript (different speech). With the new colour-aware
    visual blend, two "same mean colour" shots can still
    score visually similar; the transcript signal must
    therefore carry the discrimination.
    """
    red_kf = _make_solid(tmp_path, "red", (255, 0, 0))
    yellow_kf = _make_solid(tmp_path, "yellow", (255, 255, 0))
    green_kf = _make_solid(tmp_path, "green", (0, 255, 0))

    base = _build_rep_from_paths(
        tmp_path,
        video_id="base",
        keyframe_paths=[red_kf, red_kf, green_kf],
        transcripts=[
            "step one open the device",
            "step two disconnect the battery first",
            "step three remove the back panel",
        ],
    )
    # Branch: middle shot is yellow (clearly different colour)
    # and has different speech. This is a *real* replacement.
    branch = _build_rep_from_paths(
        tmp_path,
        video_id="branch_rep",
        keyframe_paths=[red_kf, yellow_kf, green_kf],
        transcripts=[
            "step one open the device",
            "step two remove the back panel instead",  # different
            "step three remove the back panel",
        ],
    )
    result = align_branch_to_base(base=base, branch=branch, branch_name="B")

    # Flanking shots should be unchanged (same keyframe + same
    # transcript → exactly the same shot).
    assert result.matches[0].operation == "unchanged"
    assert result.matches[2].operation == "unchanged"
    # Middle shot: different colour + different transcript
    # → must be replace or uncertain.
    middle = result.matches[1]
    assert middle.operation in {"replace", "uncertain"}, (
        f"expected middle shot to be replace/uncertain, got {middle.operation} "
        f"(visual={middle.similarity.visual_similarity}, "
        f"transcript={middle.similarity.transcript_similarity})"
    )


def test_acceptance_replacement_with_different_transcript(tmp_path: Path) -> None:
    """Same visuals but different speech — transcript must disambiguate."""
    base = _build_rep(
        tmp_path,
        video_id="base",
        keyframes=[
            (0, (255, 0, 0)),
            (1, (255, 255, 255)),
            (2, (0, 255, 0)),
        ],
        transcripts=[
            "first shot spoken words",
            "second shot spoken words",
            "third shot spoken words",
        ],
    )
    # We want shot 1 to have IDENTICAL visual but DIFFERENT speech.
    # To do that, point the branch's keyframe at the SAME file
    # the base used for shot 1 (so the pHash matches exactly)
    # but with a different transcript.
    base_kf_path = base.shots[1].keyframe_paths[0]
    branch = VideoRepresentation.from_components(
        video_id="branch_tb",
        source_path=tmp_path / "branch_tb.mp4",
        normalized_path=tmp_path / "branch_tb.working.mp4",
        audio_path=None,
        metadata=VideoMetadata(
            duration_seconds=3.0,
            width=32,
            height=32,
            fps=30.0,
            codec="h264",
            audio_present=False,
        ),
        normalization=NormalizationInfo(normalized=False),
        shots=[
            Shot(
                shot_id="shot_0000",
                start=0.0,
                end=1.0,
                keyframe_paths=[_make_keyframe(tmp_path, "branch_tb_shot0", (255, 0, 0))],
                transcript="first shot spoken words",
                transcript_segments=[],
            ),
            Shot(
                shot_id="shot_0001",
                start=1.0,
                end=2.0,
                keyframe_paths=[base_kf_path],  # SAME visual as base shot 1
                transcript="completely different transcript here",
                transcript_segments=[],
            ),
            Shot(
                shot_id="shot_0002",
                start=2.0,
                end=3.0,
                keyframe_paths=[_make_keyframe(tmp_path, "branch_tb_shot2", (0, 255, 0))],
                transcript="third shot spoken words",
                transcript_segments=[],
            ),
        ],
    )
    result = align_branch_to_base(base=base, branch=branch, branch_name="TB")
    # Shot 1: identical visuals anchor it to the same BASE unit,
    # while completely different speech identifies a replacement.
    _ops(result)
    middle = result.matches[1]
    assert middle.operation == "replace"
    # Transcript signal must be reflected in the similarity.
    assert middle.similarity.transcript_similarity is not None
    assert middle.similarity.transcript_similarity < 0.5


# ---------------------------------------------------------------------------
# Phase 3 acceptance: unchanged segments.
# ---------------------------------------------------------------------------


def test_acceptance_unchanged_branch(tmp_path: Path) -> None:
    """Branch is byte-equivalent to BASE — no edits."""
    base = _build_rep(
        tmp_path,
        video_id="base",
        keyframes=[
            (0, (255, 0, 0)),
            (1, (255, 255, 255)),
            (2, (0, 255, 0)),
        ],
    )
    # Branch reuses the exact same keyframe files as base.
    branch_kfs = [list(s.keyframe_paths) for s in base.shots]
    branch = VideoRepresentation.from_components(
        video_id="branch_unchanged",
        source_path=tmp_path / "branch_unchanged.mp4",
        normalized_path=tmp_path / "branch_unchanged.working.mp4",
        audio_path=None,
        metadata=VideoMetadata(
            duration_seconds=3.0,
            width=32,
            height=32,
            fps=30.0,
            codec="h264",
            audio_present=False,
        ),
        normalization=NormalizationInfo(normalized=False),
        shots=[
            Shot(
                shot_id=f"shot_{i:04d}",
                start=float(i),
                end=float(i) + 1.0,
                keyframe_paths=kfs,
                transcript="",
                transcript_segments=[],
            )
            for i, kfs in enumerate(branch_kfs)
        ],
    )
    result = align_branch_to_base(base=base, branch=branch, branch_name="U")
    # All three shots should be unchanged (identical keyframe +
    # same duration → not trim, not delete, not insert, not
    # replace).
    for m in result.matches:
        assert m.operation == "unchanged", f"unexpected op {m.operation} for unchanged branch"


# ---------------------------------------------------------------------------
# Output shape.
# ---------------------------------------------------------------------------


def test_align_branch_to_base_returns_alignment_result(tmp_path: Path) -> None:
    base = _build_rep(tmp_path, video_id="base", keyframes=[(0, (255, 0, 0))])
    branch = _build_rep(tmp_path, video_id="branch", keyframes=[(0, (255, 0, 0))])
    result = align_branch_to_base(base=base, branch=branch, branch_name="A")
    # Result carries branch_name, video IDs, and weights.
    assert result.branch_name == "A"
    assert result.base_video_id == "base"
    assert result.branch_video_id == "branch"
    assert "visual_similarity" in result.weights
    assert "UNCHANGED_MIN" in result.thresholds


def test_align_branch_to_base_serializable(tmp_path: Path) -> None:
    """The result must round-trip through JSON (Phase 3 dump + Phase 4 consumers)."""
    import json

    base = _build_rep(
        tmp_path,
        video_id="base",
        keyframes=[(0, (255, 0, 0)), (1, (0, 255, 0))],
    )
    branch = _build_rep(
        tmp_path,
        video_id="branch",
        keyframes=[(0, (255, 0, 0))],  # one deletion
    )
    result = align_branch_to_base(base=base, branch=branch, branch_name="A")
    # Dump via Pydantic and reload.
    d = result.model_dump(mode="json")
    j = json.dumps(d)
    assert json.loads(j) == d


def test_align_branch_to_base_evidence_carries_neighbours(tmp_path: Path) -> None:
    base = _build_rep(
        tmp_path,
        video_id="base",
        keyframes=[(0, (255, 0, 0)), (1, (255, 255, 255)), (2, (0, 255, 0))],
    )
    branch = _build_rep(
        tmp_path,
        video_id="branch",
        keyframes=[(0, (255, 0, 0)), (1, (255, 255, 255)), (2, (0, 255, 0))],
    )
    result = align_branch_to_base(base=base, branch=branch, branch_name="A")
    # First match's previous_match is "start", last match's
    # next_match is "end". Middle match has both populated.
    assert result.matches[0].evidence["previous_match"] == "start"
    assert result.matches[-1].evidence["next_match"] == "end"
    if len(result.matches) > 1:
        mid = result.matches[len(result.matches) // 2]
        assert mid.evidence["previous_match"] != "start"
        assert mid.evidence["next_match"] != "end"


def test_align_branch_to_base_custom_weights(tmp_path: Path) -> None:
    base = _build_rep(
        tmp_path,
        video_id="base",
        keyframes=[(0, (255, 0, 0)), (1, (0, 255, 0))],
    )
    branch = _build_rep(
        tmp_path,
        video_id="branch",
        keyframes=[(0, (255, 0, 0)), (1, (0, 255, 0))],
    )
    custom = {
        "visual_similarity": 0.0,
        "transcript_similarity": 0.0,
        "duration_similarity": 0.0,
        "order_prior": 1.0,
    }
    result = align_branch_to_base(base=base, branch=branch, branch_name="A", weights=custom)
    # Custom weights are recorded on the result.
    assert result.weights == custom


# ---------------------------------------------------------------------------
# Combination: deletion + replacement in one branch.
# ---------------------------------------------------------------------------


def test_combined_deletion_and_replacement(tmp_path: Path) -> None:
    """Branch drops shot 1 AND replaces shot 3 (two edits in one)."""
    red = _make_solid(tmp_path, "red", (255, 0, 0))
    white = _make_solid(tmp_path, "white", (255, 255, 255))
    green = _make_solid(tmp_path, "green", (0, 255, 0))
    blue = _make_solid(tmp_path, "blue", (0, 0, 255))
    checker = _make_checker(tmp_path, "checker", (0, 0, 0), (255, 255, 255))

    base = _build_rep_from_paths(
        tmp_path, video_id="base", keyframe_paths=[red, white, green, blue]
    )
    branch = _build_rep_from_paths(
        tmp_path,
        video_id="branch_mix",
        # drop white, replace blue with checker
        keyframe_paths=[red, green, checker],
    )
    result = align_branch_to_base(base=base, branch=branch, branch_name="M")
    ops = _ops(result)
    assert "delete" in ops
    # Find the base shot 3 (blue) operation. It got "replaced" by
    # the checker — visual diff → replace or uncertain.
    base_shot_3 = next(m for m in result.matches if m.base_shot and m.base_shot.sequence_index == 3)
    assert base_shot_3.operation in {"replace", "uncertain"}


def test_independent_edits_two_branches(tmp_path: Path) -> None:
    """Phase 3 case 4: A deletes shot 1, B replaces shot 3."""
    red = _make_solid(tmp_path, "red", (255, 0, 0))
    white = _make_solid(tmp_path, "white", (255, 255, 255))
    green = _make_solid(tmp_path, "green", (0, 255, 0))
    blue = _make_solid(tmp_path, "blue", (0, 0, 255))
    checker = _make_checker(tmp_path, "checker", (0, 0, 0), (255, 255, 255))

    base = _build_rep_from_paths(
        tmp_path, video_id="base", keyframe_paths=[red, white, green, blue]
    )
    a = _build_rep_from_paths(
        tmp_path,
        video_id="branch_a",
        keyframe_paths=[red, green, blue],  # white dropped
    )
    b = _build_rep_from_paths(
        tmp_path,
        video_id="branch_b",
        keyframe_paths=[red, white, green, checker],  # blue replaced
    )
    res_a = align_branch_to_base(base=base, branch=a, branch_name="A")
    res_b = align_branch_to_base(base=base, branch=b, branch_name="B")
    # A's base shot 1 (white) is deleted.
    assert "delete" in _ops(res_a)
    # B's base shot 3 (blue) is replaced by checker.
    b_last = next(m for m in res_b.matches if m.base_shot and m.base_shot.sequence_index == 3)
    assert b_last.operation in {"replace", "uncertain"}
