"""Phase 4 representation-correctness pass — actual-content reconstruction.

These tests pin the Phase 4 representation-correctness contract:
the verbatim content the viewer hears in each branch / combined
view MUST be the actual ASR text the viewer hears. BASE wording
MUST NOT leak into deleted / replaced positions; replacement ASR
MUST replace the BASE wording (not be appended alongside it);
edit-marker prose MUST NOT appear in the M3-facing candidate
content.

The eight user-named invariants covered here:

  1. delete absent             — A deleted the BASE shot, the
                                 branch's actual content does NOT
                                 contain the BASE wording (and the
                                 line is empty in the actual-content
                                 reconstruction).
  2. replacement old absent    — A replaced the BASE shot, the
                                 BASE wording does NOT appear in A's
                                 actual content (the replacement
                                 ASR is the only text on the line).
  3. replacement new present   — the replacement ASR IS in the
                                 branch's actual content.
  4. combined both edits       — when A and B each delete/replace
                                 a BASE shot, the combined view
                                 applies both (A-first, B-fallback)
                                 and the resulting lines carry the
                                 winning branch's text.
  5. unchanged remains         — BASE shots neither branch touched
                                 keep their BASE text in every view.
  6. BASE ref never concat     — no BASE wording appears on a line
                                 that the alignment marked
                                 delete/replace/trim.
  7. canonical A exactly one   — for the canonical fixture
                                 ``01_canonical_prereq_loss``, branch
                                 A's actual content contains the
                                 phrase "before opening the device"
                                 / "unplug it" exactly once (no
                                 duplicated BASE text).
  8. fixture-specific          — fixture 06 (one-branch-broken):
                                 A does NOT carry the max-dose
                                 warning wording in its actual
                                 content (A's replacement
                                 paraphrases it); fixture 08
                                 (replacements): both branches
                                 carry their respective replacement
                                 wording and the BASE wording is
                                 absent from each branch.

All tests are deterministic — no M3 calls, no real video files.
They use synthetic ``VideoRepresentation``s.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.models.alignment import (  # noqa: E402
    AlignmentMatch,
    AlignmentResult,
    ShotFingerprint,
    SimilarityComponents,
)
from app.models.media import (  # noqa: E402
    NormalizationInfo,
    Shot,
    VideoMetadata,
    VideoRepresentation,
)
from app.services.semantic.claims.represent import (  # noqa: E402
    ReconstructedActualContent,
    write_representation_diagnostics,
)
from app.services.semantic.claims.represent import (  # noqa: E402
    reconstruct_base_actual_content as _reconstruct_base,
)
from app.services.semantic.claims.represent import (  # noqa: E402
    reconstruct_branch_actual_content as _reconstruct_branch,
)
from app.services.semantic.claims.represent import (  # noqa: E402
    reconstruct_combined_actual_content as _reconstruct_combined,
)

# ---------------------------------------------------------------------------
# Helpers (shared with other tests; local copies to keep this file
# independent of the test helpers in test_claims_deterministic).
# ---------------------------------------------------------------------------


def _make_shot(
    *,
    sequence_index: int,
    transcript: str,
    start: float = 0.0,
    end: float = 3.0,
    shot_id: str | None = None,
) -> ShotFingerprint:
    return ShotFingerprint(
        shot_id=shot_id or f"shot_{sequence_index:04d}",
        sequence_index=sequence_index,
        start=start,
        end=end,
        duration=end - start,
        keyframe_paths=[],
        visual_fingerprint="0" * 16,
        normalized_transcript=transcript,
        transcript_tokens=transcript.split(),
        has_speech=bool(transcript),
    )


def _make_rep(*, video_id: str, lines: list[str]) -> VideoRepresentation:
    shots = [
        Shot(
            shot_id=f"shot_{i:04d}",
            start=float(i) * 3.0,
            end=float(i + 1) * 3.0,
            transcript=t,
            transcript_segments=[],
            keyframe_paths=[],
        )
        for i, t in enumerate(lines)
    ]
    metadata = VideoMetadata(
        duration_seconds=3.0 * len(lines),
        width=320,
        height=240,
        fps=30.0,
        codec="h264",
        audio_present=True,
    )
    normalization = NormalizationInfo(normalized=False, reason=None)
    return VideoRepresentation.from_components(
        video_id=video_id,
        source_path=Path(f"/tmp/{video_id}.mp4"),
        normalized_path=Path(f"/tmp/{video_id}_norm.mp4"),
        audio_path=None,
        metadata=metadata,
        normalization=normalization,
        shots=shots,
    )


def _make_match(
    *,
    base_seq: int,
    branch_seq: int | None,
    branch_text: str = "",
    operation: str = "replace",
    branch_name: str = "branch_a",
) -> AlignmentMatch:
    return AlignmentMatch(
        operation=operation,  # type: ignore[arg-type]
        confidence=0.9,
        similarity=SimilarityComponents(
            visual_similarity=0.6,
            transcript_similarity=0.4,
            duration_similarity=0.9,
            final_score=0.7,
            used_components=["visual", "transcript", "duration"],
        ),
        base_shot=_make_shot(
            sequence_index=base_seq,
            transcript=f"base shot {base_seq}",
            start=base_seq * 3.0,
            end=(base_seq + 1) * 3.0,
        ),
        branch_shot=(
            _make_shot(
                sequence_index=branch_seq or 0,
                transcript=branch_text,
                start=(branch_seq or 0) * 3.0,
                end=((branch_seq or 0) + 1) * 3.0,
            )
            if branch_seq is not None
            else None
        ),
    )


def _make_alignment(*, branch_name: str, matches: list[AlignmentMatch]) -> AlignmentResult:
    return AlignmentResult(
        branch_name=branch_name,
        base_video_id="base",
        branch_video_id=branch_name,
        matches=matches,
        weights={},
        thresholds={},
    )


def _branch(
    *,
    base: VideoRepresentation,
    branch_name: str,
    alignment: AlignmentResult,
    lines: list[str],
) -> ReconstructedActualContent:  # noqa: F821
    branch = _make_rep(video_id=branch_name, lines=lines)
    return _reconstruct_branch(
        branch_name=branch_name,
        base=base,
        branch_alignment=alignment,
        branch_video=branch,
    )


def _combined(
    *,
    base: VideoRepresentation,
    branch_a: VideoRepresentation,
    branch_b: VideoRepresentation,
    a_alignment: AlignmentResult,
    b_alignment: AlignmentResult,
):
    return _reconstruct_combined(
        a_alignment=a_alignment,
        b_alignment=b_alignment,
        branch_a=branch_a,
        branch_b=branch_b,
        base=base,
    )


# ---------------------------------------------------------------------------
# Invariant 1 — delete is absent from the branch's actual content.
# Invariant 2 — replacement BASE wording is absent; replacement is present.
# Invariant 5 — unchanged shots keep BASE wording.
# ---------------------------------------------------------------------------


def test_delete_absent_branch_actual_content() -> None:
    """Invariant 1: a deleted BASE shot's wording MUST NOT appear
    in the branch's actual content. The actual-content line at
    that BASE position is empty (no leak via a marker either)."""
    base = _make_rep(video_id="base", lines=["Unplug first.", "Then lift."])
    alignment = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(base_seq=0, branch_seq=None, operation="delete"),
        ],
    )
    content = _branch(base=base, branch_name="branch_a", alignment=alignment, lines=["Then lift."])
    # The deleted BASE wording must not appear anywhere.
    assert "Unplug first." not in content.text_lines(), content.text_lines()
    # The line is empty (no marker prose either).
    assert content.lines[0].deleted is True
    assert content.lines[0].text == ""
    assert content.lines[0].operation == "delete"
    # The unchanged BASE shot still appears.
    assert "Then lift." in content.text_lines()


def test_unchanged_candidate_uses_aligned_branch_transcript_not_base_reference() -> None:
    """Invariant 5: candidate and BASE reference stay separate.

    An unchanged match retains the transcript actually observed in
    the branch, even when its ASR wording differs slightly from BASE.
    """
    base = _make_rep(video_id="base", lines=["Unplug the device first."])
    alignment = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(
                base_seq=0,
                branch_seq=0,
                operation="unchanged",
                branch_text="Unplug device first.",
            ),
        ],
    )
    content = _branch(
        base=base,
        branch_name="branch_a",
        alignment=alignment,
        lines=["Unplug device first."],
    )

    assert content.text_lines() == ["Unplug device first."]
    assert content.lines[0].base_text == "Unplug the device first."


def test_replacement_old_absent_new_present_branch() -> None:
    """Invariant 2 + 3: a replaced BASE shot's wording MUST NOT
    appear in the branch's actual content; the replacement ASR
    MUST replace it. The line carries ONLY the replacement text."""
    base = _make_rep(video_id="base", lines=["Lift the cover.", "Access the battery."])
    alignment = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(
                base_seq=0, branch_seq=0, operation="replace", branch_text="Remove the cover."
            ),
        ],
    )
    content = _branch(
        base=base,
        branch_name="branch_a",
        alignment=alignment,
        lines=["Remove the cover.", "Access the battery."],
    )
    text_lines = content.text_lines()
    # Old wording absent.
    assert "Lift the cover." not in text_lines, text_lines
    # New wording present, exactly once.
    assert text_lines.count("Remove the cover.") == 1
    # No edit-marker prose leaked in.
    for line in text_lines:
        assert "REPLACED" not in line
        assert "DELETED" not in line
        assert "TRIMMED" not in line
        assert "UNCERTAIN" not in line
        assert "[->" not in line
        assert "->'" not in line


def test_unchanged_remains_in_branch() -> None:
    """Invariant 5: BASE shots the alignment did not touch keep
    their BASE wording in the branch's actual content."""
    base = _make_rep(video_id="base", lines=["Stay safe.", "Wear gloves."])
    alignment = _make_alignment(branch_name="branch_a", matches=[])
    content = _branch(
        base=base,
        branch_name="branch_a",
        alignment=alignment,
        lines=["Stay safe.", "Wear gloves."],
    )
    assert content.text_lines() == ["Stay safe.", "Wear gloves."]


def test_no_marker_prose_in_branch_actual_content() -> None:
    """Invariant 6: the branch's actual content MUST NOT contain
    edit-marker prose (no [DELETED], [REPLACED], [TRIMMED], etc.)."""
    base = _make_rep(
        video_id="base",
        lines=["Lift the cover.", "Access the battery.", "Replace the fuse."],
    )
    alignment = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(base_seq=0, branch_seq=None, operation="delete"),
            _make_match(
                base_seq=1,
                branch_seq=0,
                operation="replace",
                branch_text="Open the battery compartment.",
            ),
            _make_match(base_seq=2, branch_seq=1, operation="trim", branch_text="Fuse swap."),
        ],
    )
    content = _branch(
        base=base,
        branch_name="branch_a",
        alignment=alignment,
        lines=["Open the battery compartment.", "Fuse swap."],
    )
    for line in content.text_lines():
        for marker in ("DELETED", "REPLACED", "TRIMMED", "UNCERTAIN", "see edit list", "[->"):
            assert marker not in line, line


# ---------------------------------------------------------------------------
# Invariant 4 — combined applies both alignments over BASE positions.
# Invariant 6 — BASE wording is never concatenated on a line that
#               delete/replace/trim touched.
# ---------------------------------------------------------------------------


def test_combined_both_edits_apply_a_first() -> None:
    """Invariant 4: when A and B each delete a different BASE
    shot, the combined view drops both. When A replaces one shot
    and B replaces another, the combined view carries A's text
    on A's shot and B's text on B's shot."""
    base = _make_rep(
        video_id="base",
        lines=["Unplug first.", "Once unplugged, lift.", "Access the battery."],
    )
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[_make_match(base_seq=0, branch_seq=None, operation="delete")],
    )
    b_align = _make_alignment(
        branch_name="branch_b",
        matches=[_make_match(base_seq=2, branch_seq=None, operation="delete")],
    )
    branch_a = _make_rep(
        video_id="branch_a", lines=["Once unplugged, lift.", "Access the battery."]
    )
    branch_b = _make_rep(video_id="branch_b", lines=["Unplug first.", "Once unplugged, lift."])
    combined = _combined(
        base=base,
        branch_a=branch_a,
        branch_b=branch_b,
        a_alignment=a_align,
        b_alignment=b_align,
    )
    text_lines = combined.text_lines()
    # Both deletes applied: both BASE wordings absent.
    assert "Unplug first." not in text_lines
    assert "Access the battery." not in text_lines
    # Unchanged shot present.
    assert "Once unplugged, lift." in text_lines
    # No edit-marker prose.
    for line in text_lines:
        assert "DELETED" not in line
        assert "REPLACED" not in line


def test_combined_a_replace_b_replace_different_text_unresolved() -> None:
    """Invariant 4 + 6: when both branches replace the same BASE
    shot with DIFFERENT wording, the composer returns
    UNRESOLVED (per the brief: no invented winner for
    differing replaces). The candidate text is empty; the
    explicit mechanical-conflict data lives on the
    ``combined_timeline``.
    """
    base = _make_rep(video_id="base", lines=["Use the cover."])
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(
                base_seq=0, branch_seq=0, operation="replace", branch_text="Open the cover."
            )
        ],
    )
    b_align = _make_alignment(
        branch_name="branch_b",
        matches=[
            _make_match(
                base_seq=0, branch_seq=0, operation="replace", branch_text="Lift the cover."
            )
        ],
    )
    branch_a = _make_rep(video_id="branch_a", lines=["Open the cover."])
    branch_b = _make_rep(video_id="branch_b", lines=["Lift the cover."])
    combined = _combined(
        base=base,
        branch_a=branch_a,
        branch_b=branch_b,
        a_alignment=a_align,
        b_alignment=b_align,
    )
    text_lines = combined.text_lines()
    # BASE absent; both branches' wording absent (unresolved,
    # not A-wins).
    assert text_lines == []
    assert "Use the cover." not in text_lines
    assert "Open the cover." not in text_lines
    assert "Lift the cover." not in text_lines
    # The combined_timeline surfaces the explicit mechanical
    # conflict.
    assert combined.combined_timeline is not None
    slice_ = combined.combined_timeline.slices[0]
    assert slice_.verdict == "unresolved"
    assert slice_.combined_text == ""
    assert slice_.unit_a is not None
    assert slice_.unit_b is not None


def test_combined_a_replace_b_replace_identical_text_resolves() -> None:
    """Compatible same-base replace+replace with identical text
    // resolves (the shared wording lands; no conflict)."""
    base = _make_rep(video_id="base", lines=["Use the cover."])
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(
                base_seq=0, branch_seq=0, operation="replace", branch_text="Open the cover."
            )
        ],
    )
    b_align = _make_alignment(
        branch_name="branch_b",
        matches=[
            _make_match(
                base_seq=0, branch_seq=0, operation="replace", branch_text="Open the cover."
            )
        ],
    )
    branch_a = _make_rep(video_id="branch_a", lines=["Open the cover."])
    branch_b = _make_rep(video_id="branch_b", lines=["Open the cover."])
    combined = _combined(
        base=base,
        branch_a=branch_a,
        branch_b=branch_b,
        a_alignment=a_align,
        b_alignment=b_align,
    )
    text_lines = combined.text_lines()
    assert text_lines == ["Open the cover."]
    assert combined.lines[0].operation == "replace"


def test_combined_unrelated_edits_compose() -> None:
    """Invariant 4: A replaces shot 0, B replaces shot 1. The
    combined view carries A's text on shot 0 and B's text on
    shot 1; BASE wording is absent from both slots."""
    base = _make_rep(video_id="base", lines=["Use the cover.", "Access the cell."])
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(
                base_seq=0, branch_seq=0, operation="replace", branch_text="Open the cover."
            )
        ],
    )
    b_align = _make_alignment(
        branch_name="branch_b",
        matches=[
            _make_match(
                base_seq=1, branch_seq=0, operation="replace", branch_text="Reach the cell."
            )
        ],
    )
    branch_a = _make_rep(video_id="branch_a", lines=["Open the cover.", "Access the cell."])
    branch_b = _make_rep(video_id="branch_b", lines=["Use the cover.", "Reach the cell."])
    combined = _combined(
        base=base,
        branch_a=branch_a,
        branch_b=branch_b,
        a_alignment=a_align,
        b_alignment=b_align,
    )
    text_lines = combined.text_lines()
    assert text_lines == ["Open the cover.", "Reach the cell."]
    assert "Use the cover." not in text_lines
    assert "Access the cell." not in text_lines


def test_combined_replace_then_delete_unresolved() -> None:
    """A replace + B delete on the same BASE position →
    UNRESOLVED. The brief forbids choosing a winner for any
    incompatible dual modification of the same BASE unit
    (delete+replace / replace+delete / delete+trim /
    trim+delete / replace+trim / trim+replace and differing
    trims / replaces all return explicit unresolved; no
    invented winner). The candidate text is empty; the
    explicit mechanical-conflict data lives on the
    ``combined_timeline``.
    """
    base = _make_rep(video_id="base", lines=["Use the cover."])
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(
                base_seq=0, branch_seq=0, operation="replace", branch_text="Open the cover."
            )
        ],
    )
    b_align = _make_alignment(
        branch_name="branch_b",
        matches=[_make_match(base_seq=0, branch_seq=None, operation="delete")],
    )
    branch_a = _make_rep(video_id="branch_a", lines=["Open the cover."])
    branch_b = _make_rep(video_id="branch_b", lines=[])
    combined = _combined(
        base=base,
        branch_a=branch_a,
        branch_b=branch_b,
        a_alignment=a_align,
        b_alignment=b_align,
    )
    # No candidate text — the position is unresolved, not
    # deleted. The combined text_lines helper omits empty /
    # unresolved slices.
    text_lines = combined.text_lines()
    assert text_lines == []
    # The combined_timeline surfaces the explicit mechanical
    # conflict with both units preserved for forensic
    # inspection.
    assert combined.combined_timeline is not None
    slice_ = combined.combined_timeline.slices[0]
    assert slice_.verdict == "unresolved"
    assert slice_.combined_text == ""
    assert slice_.unit_a is not None
    assert slice_.unit_b is not None
    assert slice_.unit_a.operation.value == "replace"
    assert slice_.unit_b.operation.value == "delete"
    # No BASE wording or A's wording leaks into candidate text.
    assert "Use the cover." not in text_lines
    assert "Open the cover." not in text_lines


def test_combined_delete_then_replace_unresolved() -> None:
    """A delete + B replace on the same BASE position →
    UNRESOLVED. Symmetric to replace+delete; the brief
    forbids choosing a winner. The position surfaces as an
    explicit mechanical conflict; the candidate text is
    empty.
    """
    base = _make_rep(video_id="base", lines=["Use the cover."])
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[_make_match(base_seq=0, branch_seq=None, operation="delete")],
    )
    b_align = _make_alignment(
        branch_name="branch_b",
        matches=[
            _make_match(
                base_seq=0, branch_seq=0, operation="replace", branch_text="Lift the cover."
            )
        ],
    )
    branch_a = _make_rep(video_id="branch_a", lines=[])
    branch_b = _make_rep(video_id="branch_b", lines=["Lift the cover."])
    combined = _combined(
        base=base,
        branch_a=branch_a,
        branch_b=branch_b,
        a_alignment=a_align,
        b_alignment=b_align,
    )
    text_lines = combined.text_lines()
    assert text_lines == []
    assert combined.combined_timeline is not None
    slice_ = combined.combined_timeline.slices[0]
    assert slice_.verdict == "unresolved"
    assert slice_.combined_text == ""
    assert slice_.unit_a is not None
    assert slice_.unit_b is not None
    assert slice_.unit_a.operation.value == "delete"
    assert slice_.unit_b.operation.value == "replace"
    assert "Use the cover." not in text_lines
    assert "Lift the cover." not in text_lines


def test_combined_trim_uses_branch_shot_text() -> None:
    """A trims a BASE shot, B leaves it. The combined view carries
    A's trimmed branch-shot text (BASE wording absent)."""
    base = _make_rep(video_id="base", lines=["Lift the cover."])
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[_make_match(base_seq=0, branch_seq=0, operation="trim", branch_text="Lift.")],
    )
    b_align = _make_alignment(branch_name="branch_b", matches=[])
    branch_a = _make_rep(video_id="branch_a", lines=["Lift."])
    branch_b = _make_rep(video_id="branch_b", lines=["Lift the cover."])
    combined = _combined(
        base=base,
        branch_a=branch_a,
        branch_b=branch_b,
        a_alignment=a_align,
        b_alignment=b_align,
    )
    assert combined.text_lines() == ["Lift."]
    assert combined.lines[0].operation == "trim"
    assert "Lift the cover." not in combined.text_lines()


# ---------------------------------------------------------------------------
# Invariant 7 — canonical fixture 01: A's actual content has the
#               "before opening the device" prerequisite expression
#               exactly once; B has exactly one expression of the
#               same meaning; combined has zero.
# ---------------------------------------------------------------------------


def test_canonical_fixture_01_a_one_b_one_combined_zero_prerequisite() -> None:
    """The canonical MergeCut case: the prerequisite
    "unplug before opening the device" lives in BASE twice.
    A drops the first copy; B rewrites the second copy (so it
    no longer mentions unplugging); combined has no mention of
    the prerequisite anywhere.

    The user-facing invariant: A's actual content contains
    exactly one expression of the prerequisite, B's contains
    exactly one (the rewritten follow-up), and combined has
    ZERO. The "prerequisite expression" is the substring
    "unplug" (a stable substring that catches both the explicit
    statement in BASE shot 0 and the follow-up reference in
    shot 1).
    """
    base = _make_rep(
        video_id="base",
        lines=[
            "Before opening the device, unplug it from the wall.",
            "Once the device is unplugged, lift the cover.",
            "Then you can access the battery compartment.",
        ],
    )
    # A drops shot 0 (the explicit prerequisite).
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(base_seq=0, branch_seq=None, operation="delete"),
        ],
    )
    # B rewrites shot 1 to drop the "once unplugged" reference.
    b_align = _make_alignment(
        branch_name="branch_b",
        matches=[
            _make_match(
                base_seq=1,
                branch_seq=1,
                operation="replace",
                branch_text="Lift the cover.",
            )
        ],
    )
    branch_a = _make_rep(
        video_id="branch_a",
        lines=[
            "Once the device is unplugged, lift the cover.",
            "Then you can access the battery compartment.",
        ],
    )
    branch_b = _make_rep(
        video_id="branch_b",
        lines=[
            "Before opening the device, unplug it from the wall.",
            "Lift the cover.",
            "Then you can access the battery compartment.",
        ],
    )

    a_content = _branch(
        base=base,
        branch_name="branch_a",
        alignment=a_align,
        lines=[
            "Once the device is unplugged, lift the cover.",
            "Then you can access the battery compartment.",
        ],
    )
    b_content = _branch(
        base=base,
        branch_name="branch_b",
        alignment=b_align,
        lines=[
            "Before opening the device, unplug it from the wall.",
            "Lift the cover.",
            "Then you can access the battery compartment.",
        ],
    )
    combined = _combined(
        base=base,
        branch_a=branch_a,
        branch_b=branch_b,
        a_alignment=a_align,
        b_alignment=b_align,
    )

    def count_unplug(lines: list[str]) -> int:
        return sum(1 for ln in lines if "unplug" in ln.lower())

    a_lines = a_content.text_lines()
    b_lines = b_content.text_lines()
    c_lines = combined.text_lines()
    assert count_unplug(a_lines) == 1, a_lines
    assert count_unplug(b_lines) == 1, b_lines
    assert count_unplug(c_lines) == 0, c_lines


# ---------------------------------------------------------------------------
# Invariant 8 — fixture 06 (one-branch-broken): A's replacement
#               paraphrases the prohibition, so A's actual content
#               does NOT carry the "Do not exceed the recommended
#               dose" wording (max-dose warning is gone in A);
#               fixture 08 (replacements): both branches carry
#               their respective replacement wording; BASE wording
#               is absent from both.
# ---------------------------------------------------------------------------


def test_fixture06_branch_a_does_not_carry_max_dose_warning() -> None:
    """Fixture 06 (one-branch-broken): the BASE sentence is
    "Do not exceed the recommended dose of this medication."
    A replaces it with "Take this medication as needed."

    The max-dose warning wording MUST NOT appear in A's actual
    content (no marker-prose; A's replacement carries only the
    "as needed" wording). B is a no-op (B = BASE), so B's
    actual content still has the warning.
    """
    base = _make_rep(
        video_id="base",
        lines=["Do not exceed the recommended dose of this medication."],
    )
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(
                base_seq=0,
                branch_seq=0,
                operation="replace",
                branch_text="Take this medication as needed.",
            )
        ],
    )
    b_align = _make_alignment(branch_name="branch_b", matches=[])
    a_content = _branch(
        base=base,
        branch_name="branch_a",
        alignment=a_align,
        lines=["Take this medication as needed."],
    )
    b_content = _branch(
        base=base,
        branch_name="branch_b",
        alignment=b_align,
        lines=["Do not exceed the recommended dose of this medication."],
    )

    # A: max-dose warning wording absent; new wording present.
    a_text = a_content.text_lines()
    assert all("Do not exceed the recommended dose" not in ln for ln in a_text)
    assert any("Take this medication as needed." in ln for ln in a_text)
    # B (= BASE): warning still present.
    assert any("Do not exceed the recommended dose" in ln for ln in b_content.text_lines())
    # No edit-marker prose anywhere.
    for line in a_text:
        for marker in ("REPLACED", "DELETED", "TRIMMED", "UNCERTAIN", "see edit list"):
            assert marker not in line, line


def test_fixture08_replacements_present_original_absent() -> None:
    """Fixture 08 (hard-negative-related): BASE says
    "Before installing the driver, disable secure boot. Then
    run the installer and restart the computer."

    A rewrites shot 0 → "Before installing the driver, turn off
    secure boot in the BIOS." (same meaning, different reason).
    B rewrites shot 1 → "Then run the installer as administrator
    and restart." (same meaning, different install command).

    The user-named invariants:

      - A's actual content carries A's replacement wording on
        shot 0 and BASE wording on shot 1.
      - B's actual content carries BASE wording on shot 0 and
        B's replacement wording on shot 1.
      - Neither branch's actual content carries the OTHER
        branch's replacement wording.
    """
    base = _make_rep(
        video_id="base",
        lines=[
            "Before installing the driver, disable secure boot.",
            "Then run the installer and restart the computer.",
        ],
    )
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(
                base_seq=0,
                branch_seq=0,
                operation="replace",
                branch_text="Before installing the driver, turn off secure boot in the BIOS.",
            )
        ],
    )
    b_align = _make_alignment(
        branch_name="branch_b",
        matches=[
            _make_match(
                base_seq=1,
                branch_seq=1,
                operation="replace",
                branch_text="Then run the installer as administrator and restart.",
            )
        ],
    )
    a_content = _branch(
        base=base,
        branch_name="branch_a",
        alignment=a_align,
        lines=[
            "Before installing the driver, turn off secure boot in the BIOS.",
            "Then run the installer and restart the computer.",
        ],
    )
    b_content = _branch(
        base=base,
        branch_name="branch_b",
        alignment=b_align,
        lines=[
            "Before installing the driver, disable secure boot.",
            "Then run the installer as administrator and restart.",
        ],
    )

    a_lines = a_content.text_lines()
    b_lines = b_content.text_lines()
    # A: replacement wording on shot 0; BASE wording on shot 1.
    assert any("turn off secure boot in the BIOS" in ln for ln in a_lines)
    assert all("Before installing the driver, disable secure boot." not in ln for ln in a_lines)
    assert any("Then run the installer and restart the computer." in ln for ln in a_lines)
    # A does NOT carry B's replacement wording.
    assert all("as administrator" not in ln for ln in a_lines)
    # B: BASE wording on shot 0; B's replacement wording on shot 1.
    assert any("Before installing the driver, disable secure boot." in ln for ln in b_lines)
    assert any("Then run the installer as administrator and restart." in ln for ln in b_lines)
    assert all("Then run the installer and restart the computer." not in ln for ln in b_lines)
    # B does NOT carry A's replacement wording.
    assert all("in the BIOS" not in ln for ln in b_lines)


# ---------------------------------------------------------------------------
# BASE actual-content snapshot (the BASE itself, no edits).
# ---------------------------------------------------------------------------


def test_base_actual_content_unchanged() -> None:
    """The BASE actual-content snapshot carries every BASE
    shot's transcript verbatim in BASE order. No edits."""
    base = _make_rep(video_id="base", lines=["alpha.", "beta.", "gamma."])
    content = _reconstruct_base(base)
    assert content.branch == "base"
    assert content.text_lines() == ["alpha.", "beta.", "gamma."]
    assert content.edit_metadata.entries == []


# ---------------------------------------------------------------------------
# Diagnostic JSON writer.
# ---------------------------------------------------------------------------


def test_write_representation_diagnostics_emits_all_four() -> None:
    """The diagnostic writer produces one JSON file with the
    four actual-content snapshots (BASE, branch_a, branch_b,
    combined). Each snapshot has lines, text_lines, edit_metadata."""
    base = _make_rep(video_id="base", lines=["First.", "Second.", "Third."])
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[_make_match(base_seq=1, branch_seq=None, operation="delete")],
    )
    b_align = _make_alignment(
        branch_name="branch_b",
        matches=[
            _make_match(
                base_seq=2,
                branch_seq=0,
                operation="replace",
                branch_text="Third (rewritten).",
            )
        ],
    )
    branch_a = _make_rep(video_id="branch_a", lines=["First.", "Third."])
    branch_b = _make_rep(video_id="branch_b", lines=["First.", "Second.", "Third (rewritten)."])
    out = Path("/tmp/_phase4_representation_test.json")
    if out.exists():
        out.unlink()
    path = write_representation_diagnostics(
        base=base,
        branch_a=branch_a,
        branch_b=branch_b,
        a_alignment=a_align,
        b_alignment=b_align,
        out_path=out,
    )
    assert path == out
    payload = json.loads(out.read_text())
    assert set(payload.keys()) == {"BASE", "branch_a", "branch_b", "combined"}
    # BASE: all three lines verbatim.
    assert payload["BASE"]["text_lines"] == ["First.", "Second.", "Third."]
    # branch_a: shot 1 deleted.
    assert payload["branch_a"]["text_lines"] == ["First.", "Third."]
    assert payload["branch_a"]["edit_metadata"][0]["operation"] == "delete"
    # branch_b: shot 2 replaced.
    assert payload["branch_b"]["text_lines"] == [
        "First.",
        "Second.",
        "Third (rewritten).",
    ]
    assert payload["branch_b"]["lines"][2]["operation"] == "replace"
    assert "Third." not in payload["branch_b"]["text_lines"]
    # combined: shot 1 deleted (from A) + shot 2 replaced (from B).
    assert payload["combined"]["text_lines"] == ["First.", "Third (rewritten)."]
    # Edit metadata is separate from the candidate content.
    for key in ("BASE", "branch_a", "branch_b", "combined"):
        assert "edit_metadata" in payload[key]
    out.unlink()


# ---------------------------------------------------------------------------
# Edit metadata is separate from candidate content.
# ---------------------------------------------------------------------------


def test_edit_metadata_separate_from_candidate_content() -> None:
    """The orchestrator returns ``ReconstructedActualContent``
    with ``lines`` (the actual verbatim text) and a separate
    ``edit_metadata`` audit. M3 NEVER sees the metadata; the
    orchestrator passes only ``text_lines()`` to M3."""
    base = _make_rep(video_id="base", lines=["AAA.", "BBB."])
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[_make_match(base_seq=1, branch_seq=None, operation="delete")],
    )
    content = _branch(base=base, branch_name="branch_a", alignment=a_align, lines=["AAA."])
    text_lines = content.text_lines()
    # Deleted slots are removed from the candidate content entirely.
    assert text_lines == ["AAA."]
    # Edit metadata records the delete with confidence.
    assert len(content.edit_metadata.entries) == 1
    assert content.edit_metadata.entries[0].operation == "delete"
    assert content.edit_metadata.entries[0].base_sequence_index == 1
    # The metadata is NOT in text_lines (no marker prose).
    for ln in text_lines:
        assert "DELETED" not in ln
        assert "delete" not in ln


def test_text_lines_are_safe_to_pass_to_m3() -> None:
    """The ``text_lines()`` view is the exact text M3 sees in
    STEP 3. It is the verbatim viewer-hears text only; no
    markers, no BASE leakage."""
    base = _make_rep(video_id="base", lines=["Lift the cover.", "Access the battery."])
    a_align = _make_alignment(
        branch_name="branch_a",
        matches=[
            _make_match(
                base_seq=0,
                branch_seq=0,
                operation="replace",
                branch_text="Remove the cover.",
            )
        ],
    )
    content = _branch(
        base=base,
        branch_name="branch_a",
        alignment=a_align,
        lines=["Remove the cover.", "Access the battery."],
    )
    lines = content.text_lines()
    assert lines == ["Remove the cover.", "Access the battery."]
    assert "Lift the cover." not in lines
    for line in lines:
        for marker in ("DELETED", "REPLACED", "TRIMMED", "UNCERTAIN", "see edit list"):
            assert marker not in line
