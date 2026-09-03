"""Phase 3.5 — provenance-aware BASE-anchored cross-branch composition.

These tests are **strictly deterministic**. They do NOT call M3
and they do NOT build any video — they construct synthetic
``AlignmentResult`` objects in memory and exercise the merge-side
``provenance`` module against the BASE-anchored composition rules
from the user's brief.

The fixture *scripts* / *edit definitions* / *expected labels* in
``tests.fixtures.semantic_*`` and ``tests.fixtures.alignment_*``
are NOT modified. The synthetic ``AlignmentResult``s here describe
the mechanical edits each branch applied (the same edits the
semantic fixtures encode) and the composition module is asked to
build the combined timeline from them.

The tests cover:

  1. The canonical MergeCut prerequisite-loss case (the load-bearing
     example from the user's brief). The combined text must
     contain "Lift the cover" and battery content, and must NOT
     contain "Before opening" / "unplug" / "Once unplugged".

  2. Ten additional cases the user named:

     a. Deletion shifts indices — a delete in an earlier BASE
        position does NOT change how later branch positions are
        keyed; the composer iterates BASE and resolves by
        ``base_index`` (never by branch current position).

     b. Independent edits to different BASE positions compose.

     c. Delete-before-replace at the same BASE position (A
        deletes, B replaces) — the replace wins (B's content
        survives because B re-introduces content at the BASE
        position).

     d. Replace-before-delete at the same BASE position (A
        replaces, B deletes) — the delete wins (A's replacement
        is gone; B's content is gone).

     e. Multiple earlier deletions — deleting BASE positions
        0 and 1 in A does NOT change which BASE position A's
        shot 2 (which is the surviving BASE 2) is keyed to.

     f. Trim + unrelated replace — A trims BASE[0], B replaces
        BASE[2] — both edits compose; positions are independent.

     g. Unchanged branch — A and B both equal BASE; combined
        equals BASE.

     h. Same-base incompatible dual edits — A and B both
        replace BASE[0] with DIFFERENT text; combined verdict
        is ``unresolved`` and ``combined_text`` is empty
        (no invented winner).

     i. Provenance survives — the combined slices carry the
        immutable BASE identity (base_shot_id, base_index,
        base_range) and the per-branch provenance
        (branch_shot_id, branch_sequence_position). The
        composer never silently drops provenance.

     j. Current sequence index never determines base identity —
        when the branch's current position is shifted by an
        earlier delete, the composer STILL keys by BASE
        identity, so the composition is correct even when the
        branch's current position would have pointed at a
        different BASE shot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from app.models.alignment import (
    AlignmentMatch,
    AlignmentResult,
    EditOperationType,
    ShotFingerprint,
    SimilarityComponents,
)
from app.models.media import (
    NormalizationInfo,
    Shot,
    VideoMetadata,
    VideoRepresentation,
)
from app.services.merge.provenance import (
    BaseShotRecord,
    BranchShotProvenance,
    EditKind,
    ShotRange,
    build_edit_set,
    compose_combined,
)

# ---------------------------------------------------------------------------
# Synthetic helpers.
# ---------------------------------------------------------------------------


def _base_shot(
    *,
    index: int,
    shot_id: str | None = None,
    start: float = 0.0,
    end: float = 3.0,
    transcript: str = "",
) -> ShotFingerprint:
    """Build a synthetic BASE ``ShotFingerprint``."""
    return ShotFingerprint(
        shot_id=shot_id or f"base_{index:04d}",
        start=start,
        end=end,
        duration=end - start,
        visual_fingerprint="0" * 16,
        normalized_transcript=transcript.lower().strip(),
        transcript_tokens=transcript.lower().split(),
        has_speech=bool(transcript.strip()),
        sequence_index=index,
    )


def _branch_shot(
    *,
    index: int,
    shot_id: str | None = None,
    start: float = 0.0,
    end: float = 3.0,
    transcript: str = "",
) -> ShotFingerprint:
    """Build a synthetic branch ``ShotFingerprint``.

    ``index`` here is the branch's *current* sequence position
    (NOT BASE). The branch sequence position is distinct from
    BASE identity — that's the entire point of the Phase 3.5
    provenance work.
    """
    return ShotFingerprint(
        shot_id=shot_id or f"branch_{index:04d}",
        start=start,
        end=end,
        duration=end - start,
        visual_fingerprint="0" * 16,
        normalized_transcript=transcript.lower().strip(),
        transcript_tokens=transcript.lower().split(),
        has_speech=bool(transcript.strip()),
        sequence_index=index,
    )


def _match(
    *,
    operation: EditOperationType,
    base: ShotFingerprint | None,
    branch: ShotFingerprint | None,
    confidence: float = 1.0,
) -> AlignmentMatch:
    """Build a synthetic ``AlignmentMatch``."""
    sim = SimilarityComponents(
        final_score=confidence,
        used_components=[],
    )
    return AlignmentMatch(
        base_shot=base,
        branch_shot=branch,
        similarity=sim,
        operation=operation,
        confidence=confidence,
        evidence={},
    )


def _alignment(
    *,
    branch_name: str,
    base_video_id: str,
    branch_video_id: str,
    matches: list[AlignmentMatch],
) -> AlignmentResult:
    return AlignmentResult(
        branch_name=branch_name,
        base_video_id=base_video_id,
        branch_video_id=branch_video_id,
        matches=matches,
        weights={},
        thresholds={},
    )


def _base_record(*, index: int, text: str, shot_id: str | None = None) -> BaseShotRecord:
    return BaseShotRecord(
        base_index=index,
        base_shot_id=shot_id or f"base_{index:04d}",
        base_range=ShotRange(start=float(index) * 3.0, end=float(index) * 3.0 + 3.0),
        base_text=text,
    )


# Canonical MergeCut script — the prerequisite-loss case.
CANONICAL_BASE: Final[list[BaseShotRecord]] = [
    _base_record(
        index=0,
        text="Before opening the device, unplug it from the wall.",
    ),
    _base_record(
        index=1,
        text="Once the device is unplugged, lift the cover.",
    ),
    _base_record(
        index=2,
        text="Then you can access the battery compartment.",
    ),
]


# ---------------------------------------------------------------------------
# 1. Canonical MergeCut prerequisite-loss case.
# ---------------------------------------------------------------------------


def test_canonical_prereq_loss_combined_contains_lift_and_battery() -> None:
    """The canonical MergeCut case from the user's brief.

    A deletes the prerequisite (BASE[0]). B replaces the
    follow-up sentence (BASE[1]) with the no-prerequisite
    wording "Lift the cover.". The combined video must
    contain "Lift the cover" and the battery content; the
    prerequisite and the "once unplugged" framing must be
    gone (the prerequisite was deleted; the follow-up no
    longer references it).
    """
    base_shot0 = _base_shot(index=0, transcript=CANONICAL_BASE[0].base_text)
    base_shot1 = _base_shot(index=1, transcript=CANONICAL_BASE[1].base_text)
    base_shot2 = _base_shot(index=2, transcript=CANONICAL_BASE[2].base_text)

    # Branch A: deletes BASE[0] (the prerequisite). No branch
    # shot for that position. Surviving branch shots start at
    # current sequence position 0.
    a_branch0 = _branch_shot(index=0, transcript=CANONICAL_BASE[1].base_text)
    a_branch1 = _branch_shot(index=1, transcript=CANONICAL_BASE[2].base_text)
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="delete", base=base_shot0, branch=None),
            _match(operation="unchanged", base=base_shot1, branch=a_branch0),
            _match(operation="unchanged", base=base_shot2, branch=a_branch1),
        ],
    )

    # Branch B: replaces BASE[1] with "Lift the cover.". The
    # prerequisite and the battery line are unchanged.
    b_branch0 = _branch_shot(index=0, transcript=CANONICAL_BASE[0].base_text)
    b_branch1 = _branch_shot(index=1, transcript="Lift the cover.")
    b_branch2 = _branch_shot(index=2, transcript=CANONICAL_BASE[2].base_text)
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="unchanged", base=base_shot0, branch=b_branch0),
            _match(operation="replace", base=base_shot1, branch=b_branch1),
            _match(operation="unchanged", base=base_shot2, branch=b_branch2),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(base_units=CANONICAL_BASE, set_a=set_a, set_b=set_b)

    lines = combined.text_lines()
    joined = " ".join(lines)

    # The combined video must NOT contain the prerequisite
    # (BASE[0] was deleted by A) and must NOT contain the
    # "once unplugged" framing (BASE[1] was replaced by B with
    # "Lift the cover."). It MUST contain the new wording and
    # the battery content. (Case-insensitive: the
    # ShotFingerprint normalizes text to lowercase.)
    joined_lower = joined.lower()
    assert "lift the cover" in joined_lower
    assert "battery" in joined_lower
    assert "before opening" not in joined_lower
    assert "unplug it from the wall" not in joined_lower
    assert "once the device is unplugged" not in joined_lower

    # Slice-level verdict for each BASE position.
    verdicts = {s.base_index: s.verdict for s in combined.slices}
    assert verdicts[0] == "deleted"  # A's delete
    assert verdicts[1] == "replaced"  # B's replace
    assert verdicts[2] == "preserved"  # both branches left unchanged


# ---------------------------------------------------------------------------
# 2a. Deletion shifts indices — composer still keys by BASE identity.
# ---------------------------------------------------------------------------


def test_deletion_shifts_indices_composer_keys_by_base_identity() -> None:
    """A deletes BASE[0]. A's surviving branch shot for BASE[1]
    has current branch sequence position 0. The composer must
    still pair it with BASE[1] (not BASE[0]).
    """
    base0 = _base_shot(index=0, transcript="alpha")
    base1 = _base_shot(index=1, transcript="bravo")
    base2 = _base_shot(index=2, transcript="charlie")

    # Branch A's current sequence position 0 is BASE[1] (because
    # BASE[0] was deleted). The Phase 3.5 composer MUST NOT use
    # current position 0 as BASE[0]; it must use the alignment's
    # provenance (base_shot.sequence_index == 1).
    a_branch0 = _branch_shot(index=0, transcript="bravo")
    a_branch1 = _branch_shot(index=1, transcript="charlie")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="delete", base=base0, branch=None),
            _match(operation="unchanged", base=base1, branch=a_branch0),
            _match(operation="unchanged", base=base2, branch=a_branch1),
        ],
    )

    # B is unchanged.
    b_branch0 = _branch_shot(index=0, transcript="alpha")
    b_branch1 = _branch_shot(index=1, transcript="bravo")
    b_branch2 = _branch_shot(index=2, transcript="charlie")
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="unchanged", base=base0, branch=b_branch0),
            _match(operation="unchanged", base=base1, branch=b_branch1),
            _match(operation="unchanged", base=base2, branch=b_branch2),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="alpha"),
        _base_record(index=1, text="bravo"),
        _base_record(index=2, text="charlie"),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)

    verdicts = {s.base_index: s for s in combined.slices}
    # BASE[0] is deleted (A wins).
    assert verdicts[0].verdict == "deleted"
    assert verdicts[0].combined_text == ""
    # BASE[1] is preserved (A's branch position 0 is keyed to
    # BASE[1] by provenance, not by current sequence position).
    assert verdicts[1].verdict == "preserved"
    assert verdicts[1].combined_text == "bravo"
    assert verdicts[1].unit_a is not None
    assert verdicts[1].unit_a.provenance.branch_sequence_position == 0
    # BASE[2] is preserved.
    assert verdicts[2].verdict == "preserved"
    assert verdicts[2].combined_text == "charlie"


# ---------------------------------------------------------------------------
# 2b. Independent edits to different BASE positions compose.
# ---------------------------------------------------------------------------


def test_independent_edits_compose() -> None:
    """A and B edit different BASE positions; both edits land
    in the combined timeline at the correct BASE position.
    """
    base0 = _base_shot(index=0, transcript="prerequisite.")
    base1 = _base_shot(index=1, transcript="middle.")
    base2 = _base_shot(index=2, transcript="follow-up.")

    # A replaces BASE[0] with "lift the cover.".
    a_branch0 = _branch_shot(index=0, transcript="lift the cover.")
    a_branch1 = _branch_shot(index=1, transcript="middle.")
    a_branch2 = _branch_shot(index=2, transcript="follow-up.")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="replace", base=base0, branch=a_branch0),
            _match(operation="unchanged", base=base1, branch=a_branch1),
            _match(operation="unchanged", base=base2, branch=a_branch2),
        ],
    )

    # B replaces BASE[2] with "the battery is replaced.".
    b_branch0 = _branch_shot(index=0, transcript="prerequisite.")
    b_branch1 = _branch_shot(index=1, transcript="middle.")
    b_branch2 = _branch_shot(index=2, transcript="the battery is replaced.")
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="unchanged", base=base0, branch=b_branch0),
            _match(operation="unchanged", base=base1, branch=b_branch1),
            _match(operation="replace", base=base2, branch=b_branch2),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="prerequisite."),
        _base_record(index=1, text="middle."),
        _base_record(index=2, text="follow-up."),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)

    verdicts = {s.base_index: s for s in combined.slices}
    assert verdicts[0].verdict == "replaced"
    assert verdicts[0].combined_text == "lift the cover."
    assert verdicts[1].verdict == "preserved"
    assert verdicts[1].combined_text == "middle."
    assert verdicts[2].verdict == "replaced"
    assert verdicts[2].combined_text == "the battery is replaced."


# ---------------------------------------------------------------------------
# 2c. Delete-before-replace on DIFFERENT BASE units (ordering / index
#     independence). These prove that iterating BASE and keying by
#     base_index produces the right combined output even when the
#     edit operations are presented in either branch order.
# ---------------------------------------------------------------------------


def test_delete_before_replace_on_different_base_units_compose() -> None:
    """A deletes BASE[0]; B replaces BASE[1] (a DIFFERENT BASE
    position). This is the ordering / index-independence test:
    both edits land at their respective BASE positions, keyed
    by BASE identity. If the composer ever keyed by current
    branch position, A's current position 0 (containing the
    BASE[1] content) would be paired with BASE[0] and the
    replacement would be mis-attributed.
    """
    base0 = _base_shot(index=0, transcript="alpha")
    base1 = _base_shot(index=1, transcript="bravo")
    base2 = _base_shot(index=2, transcript="charlie")

    a_branch0 = _branch_shot(index=0, transcript="bravo")
    a_branch1 = _branch_shot(index=1, transcript="charlie")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="delete", base=base0, branch=None),
            _match(operation="unchanged", base=base1, branch=a_branch0),
            _match(operation="unchanged", base=base2, branch=a_branch1),
        ],
    )

    b_branch0 = _branch_shot(index=0, transcript="alpha")
    b_branch1 = _branch_shot(index=1, transcript="bravo rewritten by b.")
    b_branch2 = _branch_shot(index=2, transcript="charlie")
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="unchanged", base=base0, branch=b_branch0),
            _match(operation="replace", base=base1, branch=b_branch1),
            _match(operation="unchanged", base=base2, branch=b_branch2),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="alpha"),
        _base_record(index=1, text="bravo"),
        _base_record(index=2, text="charlie"),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)
    verdicts = {s.base_index: s for s in combined.slices}
    # BASE[0] is deleted (A).
    assert verdicts[0].verdict == "deleted"
    assert verdicts[0].combined_text == ""
    # BASE[1] is replaced (B), keyed by base_index=1 — NOT by
    # A's current branch position 0 (which also contains the
    # BASE[1] wording).
    assert verdicts[1].verdict == "replaced"
    assert verdicts[1].combined_text == "bravo rewritten by b."
    assert verdicts[1].unit_b is not None
    assert verdicts[1].unit_b.base_index == 1
    # BASE[2] is preserved.
    assert verdicts[2].verdict == "preserved"
    assert verdicts[2].combined_text == "charlie"


def test_replace_before_delete_on_different_base_units_compose() -> None:
    """Symmetric to the previous test: A replaces BASE[2]; B
    deletes BASE[0]. Both edits land at their respective BASE
    positions; the composer keys by BASE identity regardless
    of the order A and B were applied.
    """
    base0 = _base_shot(index=0, transcript="alpha")
    base1 = _base_shot(index=1, transcript="bravo")
    base2 = _base_shot(index=2, transcript="charlie")

    a_branch0 = _branch_shot(index=0, transcript="alpha")
    a_branch1 = _branch_shot(index=1, transcript="bravo")
    a_branch2 = _branch_shot(index=2, transcript="charlie rewritten by a.")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="unchanged", base=base0, branch=a_branch0),
            _match(operation="unchanged", base=base1, branch=a_branch1),
            _match(operation="replace", base=base2, branch=a_branch2),
        ],
    )

    b_branch0 = _branch_shot(index=0, transcript="bravo")
    b_branch1 = _branch_shot(index=1, transcript="charlie")
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="delete", base=base0, branch=None),
            _match(operation="unchanged", base=base1, branch=b_branch0),
            _match(operation="unchanged", base=base2, branch=b_branch1),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="alpha"),
        _base_record(index=1, text="bravo"),
        _base_record(index=2, text="charlie"),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)
    verdicts = {s.base_index: s for s in combined.slices}
    assert verdicts[0].verdict == "deleted"
    assert verdicts[0].combined_text == ""
    assert verdicts[1].verdict == "preserved"
    assert verdicts[1].combined_text == "bravo"
    assert verdicts[2].verdict == "replaced"
    assert verdicts[2].combined_text == "charlie rewritten by a."
    assert verdicts[2].unit_a is not None
    assert verdicts[2].unit_a.base_index == 2


# ---------------------------------------------------------------------------
# 2d. Same-base INCOMPATIBLE dual edits are unresolved.
#     Per the brief: delete+replace, replace+delete, delete+trim,
#     trim+delete, replace+trim, trim+replace, and differing trims /
#     replaces are all unresolved. No invented winner.
# ---------------------------------------------------------------------------


def test_same_base_delete_and_replace_unresolved() -> None:
    """A deletes BASE[0]; B replaces BASE[0]. Per the brief,
    delete+replace on the same BASE unit is incompatible and
    MUST be unresolved. No invented winner.
    """
    base0 = _base_shot(index=0, transcript="original.")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[_match(operation="delete", base=base0, branch=None)],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(
                operation="replace",
                base=base0,
                branch=_branch_shot(index=0, transcript="b replacement."),
            ),
        ],
    )
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(
        base_units=[_base_record(index=0, text="original.")],
        set_a=set_a,
        set_b=set_b,
    )
    assert combined.slices[0].verdict == "unresolved"
    assert combined.slices[0].combined_text == ""
    assert "delete" in combined.slices[0].reason
    assert "replace" in combined.slices[0].reason


def test_same_base_replace_and_delete_unresolved() -> None:
    """Symmetric: A replaces BASE[0]; B deletes BASE[0].
    Incompatible; unresolved; no invented winner.
    """
    base0 = _base_shot(index=0, transcript="original.")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(
                operation="replace",
                base=base0,
                branch=_branch_shot(index=0, transcript="a replacement."),
            ),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[_match(operation="delete", base=base0, branch=None)],
    )
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(
        base_units=[_base_record(index=0, text="original.")],
        set_a=set_a,
        set_b=set_b,
    )
    assert combined.slices[0].verdict == "unresolved"
    assert combined.slices[0].combined_text == ""


def test_same_base_delete_and_trim_unresolved() -> None:
    """A deletes BASE[0]; B trims BASE[0]. Incompatible;
    unresolved.
    """
    base0 = _base_shot(index=0, transcript="original.", end=5.0)
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[_match(operation="delete", base=base0, branch=None)],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(
                operation="trim",
                base=base0,
                branch=_branch_shot(index=0, transcript="trimmed.", start=0.0, end=2.5),
            ),
        ],
    )
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(
        base_units=[
            BaseShotRecord(
                base_index=0,
                base_shot_id="base_0000",
                base_range=ShotRange(start=0.0, end=5.0),
                base_text="original.",
            ),
        ],
        set_a=set_a,
        set_b=set_b,
    )
    assert combined.slices[0].verdict == "unresolved"
    assert combined.slices[0].combined_text == ""


def test_same_base_trim_and_delete_unresolved() -> None:
    """Symmetric: A trims BASE[0]; B deletes BASE[0].
    Incompatible; unresolved.
    """
    base0 = _base_shot(index=0, transcript="original.", end=5.0)
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(
                operation="trim",
                base=base0,
                branch=_branch_shot(index=0, transcript="trimmed.", start=0.0, end=2.5),
            ),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[_match(operation="delete", base=base0, branch=None)],
    )
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(
        base_units=[
            BaseShotRecord(
                base_index=0,
                base_shot_id="base_0000",
                base_range=ShotRange(start=0.0, end=5.0),
                base_text="original.",
            ),
        ],
        set_a=set_a,
        set_b=set_b,
    )
    assert combined.slices[0].verdict == "unresolved"
    assert combined.slices[0].combined_text == ""


def test_same_base_replace_and_trim_unresolved() -> None:
    """A replaces BASE[0]; B trims BASE[0]. Incompatible;
    unresolved.
    """
    base0 = _base_shot(index=0, transcript="original.")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(
                operation="replace",
                base=base0,
                branch=_branch_shot(index=0, transcript="a replacement."),
            ),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(
                operation="trim",
                base=base0,
                branch=_branch_shot(index=0, transcript="trimmed wording."),
            ),
        ],
    )
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(
        base_units=[_base_record(index=0, text="original.")],
        set_a=set_a,
        set_b=set_b,
    )
    assert combined.slices[0].verdict == "unresolved"
    assert combined.slices[0].combined_text == ""


def test_same_base_trim_and_replace_unresolved() -> None:
    """Symmetric: A trims BASE[0]; B replaces BASE[0].
    Incompatible; unresolved.
    """
    base0 = _base_shot(index=0, transcript="original.")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(
                operation="trim",
                base=base0,
                branch=_branch_shot(index=0, transcript="trimmed wording."),
            ),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(
                operation="replace",
                base=base0,
                branch=_branch_shot(index=0, transcript="b replacement."),
            ),
        ],
    )
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(
        base_units=[_base_record(index=0, text="original.")],
        set_a=set_a,
        set_b=set_b,
    )
    assert combined.slices[0].verdict == "unresolved"
    assert combined.slices[0].combined_text == ""


def test_same_base_differing_trims_unresolved() -> None:
    """A and B both trim BASE[0] with DIFFERENT text. Per the
    brief, differing trims are incompatible and MUST NOT
    choose a winner.
    """
    base0 = _base_shot(index=0, transcript="original.")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(
                operation="trim",
                base=base0,
                branch=_branch_shot(index=0, transcript="a trimmed wording."),
            ),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(
                operation="trim",
                base=base0,
                branch=_branch_shot(index=0, transcript="b trimmed wording."),
            ),
        ],
    )
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(
        base_units=[_base_record(index=0, text="original.")],
        set_a=set_a,
        set_b=set_b,
    )
    assert combined.slices[0].verdict == "unresolved"
    assert combined.slices[0].combined_text == ""


def test_same_base_identical_trims_resolve() -> None:
    """A and B both trim BASE[0] with the SAME text — compatible;
    resolve to the shared wording.
    """
    base0 = _base_shot(index=0, transcript="original.")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(
                operation="trim",
                base=base0,
                branch=_branch_shot(index=0, transcript="identical trim."),
            ),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(
                operation="trim",
                base=base0,
                branch=_branch_shot(index=0, transcript="identical trim."),
            ),
        ],
    )
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(
        base_units=[_base_record(index=0, text="original.")],
        set_a=set_a,
        set_b=set_b,
    )
    assert combined.slices[0].verdict == "trimmed"
    assert combined.slices[0].combined_text == "identical trim."


# ---------------------------------------------------------------------------
# 2e. Multiple earlier deletions do not corrupt later keying.
# ---------------------------------------------------------------------------


def test_multiple_earlier_deletions_keying_intact() -> None:
    """A deletes BASE[0] and BASE[1]. A's surviving branch shot
    for BASE[2] is at current branch sequence position 0. The
    composer must still pair that shot with BASE[2] (not
    BASE[0]).
    """
    base0 = _base_shot(index=0, transcript="first.")
    base1 = _base_shot(index=1, transcript="second.")
    base2 = _base_shot(index=2, transcript="third.")

    a_branch0 = _branch_shot(index=0, transcript="third.")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="delete", base=base0, branch=None),
            _match(operation="delete", base=base1, branch=None),
            _match(operation="unchanged", base=base2, branch=a_branch0),
        ],
    )

    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="unchanged", base=base0, branch=_branch_shot(index=0)),
            _match(operation="unchanged", base=base1, branch=_branch_shot(index=1)),
            _match(operation="unchanged", base=base2, branch=_branch_shot(index=2)),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="first."),
        _base_record(index=1, text="second."),
        _base_record(index=2, text="third."),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)
    verdicts = {s.base_index: s for s in combined.slices}
    assert verdicts[0].verdict == "deleted"
    assert verdicts[1].verdict == "deleted"
    assert verdicts[2].verdict == "preserved"
    assert verdicts[2].combined_text == "third."
    assert verdicts[2].unit_a is not None
    # A's branch shot for BASE[2] is at current sequence
    # position 0 (after two deletes), but the provenance
    # records base_index=2 correctly.
    assert verdicts[2].unit_a.base_index == 2
    assert verdicts[2].unit_a.provenance.branch_sequence_position == 0


# ---------------------------------------------------------------------------
# 2f. Trim + unrelated replace compose.
# ---------------------------------------------------------------------------


def test_trim_and_unrelated_replace_compose() -> None:
    """A trims BASE[0] (shorter, same wording). B replaces
    BASE[2] with new content. Both edits land at the correct
    BASE position.
    """
    base0 = _base_shot(index=0, transcript="trimmed text.", start=0.0, end=2.0)
    base1 = _base_shot(index=1, transcript="middle.", start=2.0, end=5.0)
    base2 = _base_shot(index=2, transcript="replaced.", start=5.0, end=8.0)

    a_branch0 = _branch_shot(index=0, transcript="trimmed text.", start=0.0, end=1.6)
    a_branch1 = _branch_shot(index=1, transcript="middle.", start=1.6, end=4.6)
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="trim", base=base0, branch=a_branch0),
            _match(operation="unchanged", base=base1, branch=a_branch1),
            _match(operation="unchanged", base=base2, branch=_branch_shot(index=2)),
        ],
    )

    b_branch0 = _branch_shot(index=0, transcript="trimmed text.")
    b_branch1 = _branch_shot(index=1, transcript="middle.")
    b_branch2 = _branch_shot(index=2, transcript="the new replacement.")
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="unchanged", base=base0, branch=b_branch0),
            _match(operation="unchanged", base=base1, branch=b_branch1),
            _match(operation="replace", base=base2, branch=b_branch2),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="trimmed text."),
        _base_record(index=1, text="middle."),
        _base_record(index=2, text="replaced."),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)
    verdicts = {s.base_index: s for s in combined.slices}
    assert verdicts[0].verdict == "trimmed"
    assert verdicts[0].combined_text == "trimmed text."
    assert verdicts[1].verdict == "preserved"
    assert verdicts[1].combined_text == "middle."
    assert verdicts[2].verdict == "replaced"
    assert verdicts[2].combined_text == "the new replacement."


# ---------------------------------------------------------------------------
# 2g. Unchanged branch: combined equals BASE.
# ---------------------------------------------------------------------------


def test_unchanged_branch_combined_equals_base() -> None:
    """A and B both equal BASE. The combined timeline is
    position-by-position the BASE text, with verdict
    'preserved' at every position.
    """
    base0 = _base_shot(index=0, transcript="line one.")
    base1 = _base_shot(index=1, transcript="line two.")
    base2 = _base_shot(index=2, transcript="line three.")

    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="unchanged", base=base0, branch=_branch_shot(index=0)),
            _match(operation="unchanged", base=base1, branch=_branch_shot(index=1)),
            _match(operation="unchanged", base=base2, branch=_branch_shot(index=2)),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="unchanged", base=base0, branch=_branch_shot(index=0)),
            _match(operation="unchanged", base=base1, branch=_branch_shot(index=1)),
            _match(operation="unchanged", base=base2, branch=_branch_shot(index=2)),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="line one."),
        _base_record(index=1, text="line two."),
        _base_record(index=2, text="line three."),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)

    assert [s.verdict for s in combined.slices] == ["preserved", "preserved", "preserved"]
    assert combined.text_lines() == ["line one.", "line two.", "line three."]


# ---------------------------------------------------------------------------
# 2h. Same-base incompatible dual edits → explicit unresolved.
# ---------------------------------------------------------------------------


def test_same_base_incompatible_dual_edits_unresolved() -> None:
    """A and B both replace BASE[0] with DIFFERENT text. The
    composer must return ``unresolved`` and empty
    ``combined_text``. No invented winner.
    """
    base0 = _base_shot(index=0, transcript="original line.")
    base1 = _base_shot(index=1, transcript="next line.")

    a_branch0 = _branch_shot(index=0, transcript="branch a rewrite.")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="replace", base=base0, branch=a_branch0),
            _match(operation="unchanged", base=base1, branch=_branch_shot(index=1)),
        ],
    )

    b_branch0 = _branch_shot(index=0, transcript="branch b different rewrite.")
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="replace", base=base0, branch=b_branch0),
            _match(operation="unchanged", base=base1, branch=_branch_shot(index=1)),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="original line."),
        _base_record(index=1, text="next line."),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)
    verdicts = {s.base_index: s for s in combined.slices}
    assert verdicts[0].verdict == "unresolved"
    assert verdicts[0].combined_text == ""
    # The conflict reason names the conflicting operations
    # (both replace) and the DIFFerent text so downstream
    # resolvers can surface the mechanical conflict. Both
    # units are also carried on the slice for forensic
    # inspection.
    assert "DIFFERENT" in verdicts[0].reason
    assert verdicts[0].unit_a is not None
    assert verdicts[0].unit_a.provenance.branch == "branch_a"
    assert verdicts[0].unit_a.replacement_text == "branch a rewrite."
    assert verdicts[0].unit_b is not None
    assert verdicts[0].unit_b.provenance.branch == "branch_b"
    assert verdicts[0].unit_b.replacement_text == "branch b different rewrite."

    # The non-conflicting position composes normally.
    assert verdicts[1].verdict == "preserved"
    assert verdicts[1].combined_text == "next line."


def test_same_base_compatible_dual_replaces_resolve() -> None:
    """A and B both replace BASE[0] with the SAME text. The
    composer returns the shared text (the 'no conflict'
    counterpart of the incompatible case above).
    """
    base0 = _base_shot(index=0, transcript="original.")

    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(
                operation="replace",
                base=base0,
                branch=_branch_shot(index=0, transcript="identical rewrite."),
            ),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(
                operation="replace",
                base=base0,
                branch=_branch_shot(index=0, transcript="identical rewrite."),
            ),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(
        base_units=[_base_record(index=0, text="original.")],
        set_a=set_a,
        set_b=set_b,
    )
    assert combined.slices[0].verdict == "replaced"
    assert combined.slices[0].combined_text == "identical rewrite."


# ---------------------------------------------------------------------------
# 2i. Provenance survives.
# ---------------------------------------------------------------------------


def test_provenance_survives_in_combined_slices() -> None:
    """Every combined slice carries the immutable BASE
    identity (base_shot_id, base_index, base_range) and the
    per-branch provenance (branch_shot_id,
    branch_sequence_position). The composer never silently
    drops provenance.
    """
    base0 = _base_shot(index=0, shot_id="base_alpha", transcript="alpha")
    base1 = _base_shot(index=1, shot_id="base_bravo", transcript="bravo")

    a_branch0 = _branch_shot(index=0, shot_id="branch_a_bravo", transcript="bravo")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="delete", base=base0, branch=None),
            _match(operation="unchanged", base=base1, branch=a_branch0),
        ],
    )

    b_branch0 = _branch_shot(index=0, shot_id="branch_b_alpha", transcript="alpha")
    b_branch1 = _branch_shot(index=1, shot_id="branch_b_bravo", transcript="bravo rephrased")
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="unchanged", base=base0, branch=b_branch0),
            _match(operation="replace", base=base1, branch=b_branch1),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="alpha", shot_id="base_alpha"),
        _base_record(index=1, text="bravo", shot_id="base_bravo"),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)

    # BASE identity survives in every slice.
    assert combined.slices[0].base_index == 0
    assert combined.slices[0].base_shot_id == "base_alpha"
    assert combined.slices[0].base_range.start == 0.0
    assert combined.slices[0].base_range.end == 3.0
    assert combined.slices[1].base_index == 1
    assert combined.slices[1].base_shot_id == "base_bravo"

    # Branch provenance survives.
    # BASE[0]: A deleted (unit_a present, unit_b present).
    assert combined.slices[0].unit_a is not None
    assert combined.slices[0].unit_a.base_shot_id == "base_alpha"
    assert combined.slices[0].unit_a.operation == EditKind.DELETE
    assert combined.slices[0].unit_b is not None
    assert combined.slices[0].unit_b.base_shot_id == "base_alpha"
    assert combined.slices[0].unit_b.operation == EditKind.UNCHANGED
    assert combined.slices[0].unit_b.provenance.branch_shot_id == "branch_b_alpha"

    # BASE[1]: A's branch shot (now at current position 0 after
    # the delete) still carries base_shot_id='base_bravo'.
    assert combined.slices[1].unit_a is not None
    assert combined.slices[1].unit_a.base_shot_id == "base_bravo"
    assert combined.slices[1].unit_a.provenance.branch_shot_id == "branch_a_bravo"
    assert combined.slices[1].unit_a.provenance.branch_sequence_position == 0
    assert combined.slices[1].unit_b is not None
    assert combined.slices[1].unit_b.base_shot_id == "base_bravo"
    assert combined.slices[1].unit_b.provenance.branch_shot_id == "branch_b_bravo"


def test_provenance_survives_in_editset() -> None:
    """The EditSet carries every required provenance field
    per unit (requirement 1 of the brief): base_shot_id /
    base_index, branch_shot_id, branch, operation, base
    start / end, branch start / end, retained / replacement
    content, confidence, and the branch's current sequence
    position distinct from BASE identity.
    """
    base0 = _base_shot(
        index=0,
        shot_id="b0",
        start=0.0,
        end=3.0,
        transcript="original.",
    )
    a_branch0 = _branch_shot(
        index=0,
        shot_id="a0",
        start=0.0,
        end=3.0,
        transcript="replacement.",
    )
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[_match(operation="replace", base=base0, branch=a_branch0, confidence=0.87)],
    )

    edit_set = build_edit_set(branch="branch_a", alignment=a_alignment)
    assert edit_set.branch == "branch_a"
    assert len(edit_set.units) == 1
    unit = edit_set.units[0]
    p: BranchShotProvenance = unit.provenance
    assert p.base_shot_id == "b0"
    assert p.base_index == 0
    assert p.base_range.start == 0.0
    assert p.base_range.end == 3.0
    assert p.branch == "branch_a"
    assert p.branch_shot_id == "a0"
    assert p.branch_sequence_position == 0
    assert p.branch_range is not None
    assert p.branch_range.start == 0.0
    assert p.branch_range.end == 3.0
    assert p.operation == EditKind.REPLACE
    assert p.retained_text == ""
    assert p.replacement_text == "replacement."
    assert p.confidence == pytest.approx(0.87)


# ---------------------------------------------------------------------------
# 2j. Current sequence index never determines base identity.
# ---------------------------------------------------------------------------


def test_current_sequence_index_never_determines_base_identity() -> None:
    """A branch's current sequence position is recorded as
    ``branch_sequence_position`` but MUST NOT be the lookup
    key. To prove this, construct a deliberately
    mis-aligned-looking scenario: branch's current position
    N contains a different BASE line's content, and the
    composer must still key the slice by ``base_index``.

    Concretely: BASE[0] is "alpha", BASE[1] is "bravo".
    A's current position 0 holds the wording "bravo" (the
    BASE[1] line) because A deleted BASE[0]. The
    alignment's provenance correctly tags the match with
    base_shot.sequence_index=1. The composer must use
    base_index=1 (NOT 0) for the slice that contains the
    "bravo" wording. If the composer ever keyed by current
    branch position, the slice would land at base_index=0
    and the combined text would be wrong.
    """
    base0 = _base_shot(index=0, transcript="alpha")
    base1 = _base_shot(index=1, transcript="bravo")

    a_branch0 = _branch_shot(index=0, transcript="bravo")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="delete", base=base0, branch=None),
            _match(operation="unchanged", base=base1, branch=a_branch0),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="unchanged", base=base0, branch=_branch_shot(index=0)),
            _match(operation="unchanged", base=base1, branch=_branch_shot(index=1)),
        ],
    )

    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="alpha"),
        _base_record(index=1, text="bravo"),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)

    # The slice keyed by base_index=1 must carry the "bravo"
    # wording — NOT a slice at base_index=0 carrying "bravo".
    by_idx = {s.base_index: s for s in combined.slices}
    assert by_idx[0].verdict == "deleted"
    assert by_idx[0].combined_text == ""
    assert by_idx[1].verdict == "preserved"
    assert by_idx[1].combined_text == "bravo"
    # The unit for BASE[1] in A is at current branch sequence
    # position 0; the slice's provenance captures this.
    assert by_idx[1].unit_a is not None
    assert by_idx[1].unit_a.base_index == 1
    assert by_idx[1].unit_a.provenance.branch_sequence_position == 0
    # The combined text never contains "alpha" (BASE[0] was
    # deleted).
    assert "alpha" not in combined.text_lines()


def test_no_branch_unit_lookup_uses_branch_sequence_position() -> None:
    """Explicit defensive check: the
    ``EditSet.by_base_index()`` method must reject duplicate
    ``base_index`` entries (which would be the symptom of
    looking up by current branch position). The constructor
    itself surfaces duplicates too, but the lookup table
    must be the only supported key.
    """
    base0 = _base_shot(index=0, transcript="x")
    a_branch0 = _branch_shot(index=0, transcript="x")
    a_branch1 = _branch_shot(index=1, transcript="x")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="unchanged", base=base0, branch=a_branch0),
            _match(operation="unchanged", base=base0, branch=a_branch1),
        ],
    )
    with pytest.raises(ValueError, match="duplicate base_index"):
        build_edit_set(branch="branch_a", alignment=a_alignment)


# ---------------------------------------------------------------------------
# Phase 3.5 fixture rendering repair — visual provenance.
# ---------------------------------------------------------------------------


def test_branch_base_indices_preserves_base_index_after_delete() -> None:
    """The Phase 3.5 fixture rendering repair: the colour of
    each surviving branch block must come from the BASE
    index, not the branch's current sequence position.

    ``branch_base_indices`` maps each surviving branch line
    to the BASE line index it is the (possibly edited) version
    of. The colour palette is indexed by BASE line index so a
    delete in an earlier line does not shift the colour of a
    later surviving line.
    """
    from tests.fixtures.semantic_fixtures import (
        Script,
        ScriptLine,
        branch_base_indices,
    )

    script = Script(
        name="phase35_visual_provenance_repair",
        lines=[
            ScriptLine("narrator", "first."),
            ScriptLine("narrator", "second."),
            ScriptLine("narrator", "third."),
            ScriptLine("narrator", "fourth."),
        ],
        edits_a={0: "DELETE", 2: "DELETE"},
        edits_b={},
    )

    # Branch A's surviving lines: BASE[1], BASE[3]. The
    # mapping must report [1, 3] (the BASE indices of those
    # surviving lines) — NOT [0, 1] (the current branch
    # positions).
    a_idx = branch_base_indices(script, "branch_a")
    assert a_idx == [1, 3]

    # Branch B is unchanged: every surviving line is keyed
    # 1:1 to its BASE index.
    b_idx = branch_base_indices(script, "branch_b")
    assert b_idx == [0, 1, 2, 3]


def test_branch_base_indices_replace_keeps_base_index() -> None:
    """A REPLACE keeps the BASE line index (the replacement
    is the same BASE line, just with different wording).
    """
    from tests.fixtures.semantic_fixtures import (
        Script,
        ScriptLine,
        branch_base_indices,
    )

    script = Script(
        name="phase35_replace_keeps_base_index",
        lines=[
            ScriptLine("narrator", "first."),
            ScriptLine("narrator", "second."),
        ],
        edits_a={1: "REPLACE"},
        edits_b={},
    )
    assert branch_base_indices(script, "branch_a") == [0, 1]
    assert branch_base_indices(script, "branch_b") == [0, 1]


# ---------------------------------------------------------------------------
# Insert semantics: insert with no stable BASE anchor → unresolved.
# ---------------------------------------------------------------------------


def test_insert_is_preserved_on_editset_unanchored_inserts() -> None:
    """An insert (``base_shot is None``) is preserved on
    ``EditSet.unanchored_inserts`` (NOT silently dropped). The
    composer attaches the insert to the nearest preceding
    BASE position and surfaces an explicit ``unresolved``
    mechanical-conflict verdict carrying the insert
    provenance. The combined candidate text remains
    uncontaminated — empty ``combined_text`` on the
    ``unresolved`` slice.
    """
    base0 = _base_shot(index=0, transcript="original.", start=0.0, end=3.0)
    base1 = _base_shot(index=1, transcript="second.", start=3.0, end=6.0)
    insert_shot = _branch_shot(index=1, transcript="inserted line.", start=1.0, end=2.0)
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(operation="unchanged", base=base0, branch=_branch_shot(index=0)),
            _match(operation="insert", base=None, branch=insert_shot),
            _match(operation="unchanged", base=base1, branch=_branch_shot(index=2)),
        ],
    )
    edit_set = build_edit_set(branch="branch_a", alignment=a_alignment)
    # The two BASE-anchored units survive.
    assert len(edit_set.units) == 2
    assert sorted(u.base_index for u in edit_set.units) == [0, 1]
    # The insert is preserved with full provenance on
    # unanchored_inserts — NOT dropped.
    assert len(edit_set.unanchored_inserts) == 1
    ins = edit_set.unanchored_inserts[0]
    assert ins.provenance.base_index == -1
    assert ins.provenance.branch == "branch_a"
    assert ins.provenance.branch_sequence_position == 1
    assert ins.provenance.retained_text == "inserted line."

    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(operation="unchanged", base=base0, branch=_branch_shot(index=0)),
            _match(operation="unchanged", base=base1, branch=_branch_shot(index=1)),
        ],
    )
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    base_units = [
        _base_record(index=0, text="original."),
        _base_record(index=1, text="second."),
    ]
    combined = compose_combined(base_units=base_units, set_a=set_a, set_b=set_b)
    # The insert attaches to BASE[0] (preceding BASE position);
    # that slice is unresolved with empty combined_text and
    # carries the insert provenance.
    by_idx = {s.base_index: s for s in combined.slices}
    assert by_idx[0].verdict == "unresolved"
    assert by_idx[0].combined_text == ""
    assert len(by_idx[0].unanchored_inserts) == 1
    assert by_idx[0].unanchored_inserts[0].provenance.retained_text == "inserted line."
    # BASE[1] is preserved (B's unchanged; A had no BASE-anchored
    # edit there).
    assert by_idx[1].verdict == "preserved"
    assert by_idx[1].combined_text == "second."


# ---------------------------------------------------------------------------
# Slice verdict / reason diagnostic completeness.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a_op", "b_op", "expected_verdict", "expected_text_substr"),
    [
        ("unchanged", "unchanged", "preserved", "alpha"),
        ("delete", "unchanged", "deleted", ""),
        ("unchanged", "delete", "deleted", ""),
        ("delete", "delete", "deleted", ""),
        ("replace", "unchanged", "replaced", "alpha replacement"),
        ("unchanged", "replace", "replaced", "alpha replacement"),
        ("trim", "unchanged", "trimmed", "alpha trimmed"),
        ("unchanged", "trim", "trimmed", "alpha trimmed"),
    ],
)
def test_one_sided_composition_table(
    a_op: str,
    b_op: str,
    expected_verdict: str,
    expected_text_substr: str,
) -> None:
    """One-sided composition table — every cell with one
    branch absent is verified to take the touching branch's
    behavior.
    """
    base0 = _base_shot(index=0, transcript="alpha")
    a_branch0 = _branch_shot(index=0, transcript="alpha replacement")
    a_branch0_t = _branch_shot(index=0, transcript="alpha trimmed")
    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="b",
        branch_video_id="a",
        matches=[
            _match(
                operation=a_op,  # type: ignore[arg-type]
                base=base0,
                branch=a_branch0
                if a_op == "replace"
                else (
                    a_branch0_t
                    if a_op == "trim"
                    else (None if a_op == "delete" else _branch_shot(index=0))
                ),
            ),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="b",
        branch_video_id="b",
        matches=[
            _match(
                operation=b_op,  # type: ignore[arg-type]
                base=base0,
                branch=a_branch0
                if b_op == "replace"
                else (
                    a_branch0_t
                    if b_op == "trim"
                    else (None if b_op == "delete" else _branch_shot(index=0))
                ),
            ),
        ],
    )
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined = compose_combined(
        base_units=[_base_record(index=0, text="alpha")],
        set_a=set_a,
        set_b=set_b,
    )
    slice_ = combined.slices[0]
    assert slice_.verdict == expected_verdict
    if expected_text_substr:
        assert expected_text_substr in slice_.combined_text
    else:
        assert slice_.combined_text == ""


# ---------------------------------------------------------------------------
# Integration: canonical MergeCut case through
# ``reconstruct_combined_actual_content``.
#
# This test exercises the public ``represent.py`` API
# (``reconstruct_combined_actual_content``) end-to-end and
# proves that the shifted canonical A-match is keyed by BASE
# identity: the surviving branch shot for BASE[1] sits at
# current branch sequence position 0 after A's delete on
# BASE[0], but the combined timeline still attributes B's
# replacement to BASE[1] (not to the deleted BASE[0] slot).
#
# The combined transcript MUST contain "Lift the cover" and
# "battery"; MUST NOT contain "Before opening", "unplug it
# from the wall", or "Once the device is unplugged" (the
# prerequisite sentence). The test does NOT require ASR /
# FFmpeg / M3; it builds synthetic Phase 3 alignments and
# confirms ``ReconstructedActualContent`` (which is what the
# orchestrator hands to M3) and the underlying
# ``CombinedTimeline`` both agree.
# ---------------------------------------------------------------------------


def _make_video_representation(*, video_id: str, shots: list[Shot]) -> VideoRepresentation:
    """Build a minimal ``VideoRepresentation`` for tests that
    don't need real media metadata.

    The fields the composer / reconstruction actually consume
    are ``shots`` and ``video_id``. Other fields carry
    placeholders.
    """
    duration = float(sum(max(0.0, s.end - s.start) for s in shots))
    metadata = VideoMetadata(
        duration_seconds=duration,
        width=320,
        height=240,
        fps=30.0,
        codec="h264",
        audio_present=any(bool(s.transcript) for s in shots),
    )
    normalization = NormalizationInfo(
        normalized=False,
        reason=None,
    )
    placeholder = Path(f"/tmp/mergecut-test-{video_id}.mp4")
    return VideoRepresentation.from_components(
        video_id=video_id,
        source_path=placeholder,
        normalized_path=placeholder,
        audio_path=None,
        metadata=metadata,
        normalization=normalization,
        shots=shots,
    )


def _make_base_shot(*, index: int, text: str) -> Shot:
    """Build one BASE ``Shot`` matching the canonical MergeCut case."""
    start = float(index) * 3.0
    end = start + 3.0
    return Shot(
        shot_id=f"base_{index:04d}",
        start=start,
        end=end,
        keyframe_paths=[],
        transcript=text,
        transcript_segments=[],
    )


def _make_branch_shot(*, index: int, text: str) -> Shot:
    """Build one branch ``Shot`` for the canonical case."""
    start = float(index) * 3.0
    end = start + 3.0
    return Shot(
        shot_id=f"branch_{index:04d}",
        start=start,
        end=end,
        keyframe_paths=[],
        transcript=text,
        transcript_segments=[],
    )


def test_represent_combined_canonical_prereq_loss_keyed_by_base_identity() -> None:
    """Canonical MergeCut prerequisite-loss case end-to-end
    through ``reconstruct_combined_actual_content``.

    BASE[0] = "Before opening the device, unplug it from the wall."
    BASE[1] = "Once the device is unplugged, lift the cover."
    BASE[2] = "Then you can access the battery compartment."

    Branch A deletes BASE[0] (the prerequisite). The DP
    alignment pairs A's surviving branch shots with BASE[1]
    and BASE[2] respectively; in A's current timeline those
    surviving shots sit at sequence positions 0 and 1.

    Branch B replaces BASE[1] with "Lift the cover." and
    leaves BASE[0] and BASE[2] unchanged.

    The combined reconstruction must:

      - Attribute B's replacement to BASE[1] (not to the
        shifted current position 0 — that would mis-key the
        edit and either lose the prerequisite loss or
        re-attribute the replacement to BASE[0]).
      - Emit only the BASE-anchored BASE[2] content
        ("battery") and the B replacement ("lift the cover").
      - Carry the full provenance (BASE identity + per-branch
        unit + verdict) on the ``combined_timeline``.
      - NOT contain any of "before opening", "unplug it from
        the wall", or "once the device is unplugged" (the
        prerequisite sentence).
    """
    from app.services.semantic.claims.represent import (
        reconstruct_combined_actual_content,
    )

    base_texts = [
        "Before opening the device, unplug it from the wall.",
        "Once the device is unplugged, lift the cover.",
        "Then you can access the battery compartment.",
    ]
    base = _make_video_representation(
        video_id="base",
        shots=[_make_base_shot(index=i, text=t) for i, t in enumerate(base_texts)],
    )

    # Branch A: deletes BASE[0]; surviving branch shots are the
    # unchanged versions of BASE[1] and BASE[2]. In A's
    # current timeline those shots sit at sequence positions
    # 0 and 1 (the position shift caused by the BASE[0] delete).
    a_branch0 = _branch_shot(index=0, transcript=base_texts[1])
    a_branch1 = _branch_shot(index=1, transcript=base_texts[2])
    branch_a_video = _make_video_representation(
        video_id="branch_a",
        shots=[
            _make_branch_shot(index=0, text=base_texts[1]),
            _make_branch_shot(index=1, text=base_texts[2]),
        ],
    )

    base_shot0 = _base_shot(index=0, transcript=base_texts[0])
    base_shot1 = _base_shot(index=1, transcript=base_texts[1])
    base_shot2 = _base_shot(index=2, transcript=base_texts[2])

    b_branch0 = _branch_shot(index=0, transcript=base_texts[0])
    b_branch1 = _branch_shot(index=1, transcript="Lift the cover.")
    b_branch2 = _branch_shot(index=2, transcript=base_texts[2])

    a_alignment = _alignment(
        branch_name="branch_a",
        base_video_id="base",
        branch_video_id="branch_a",
        matches=[
            _match(operation="delete", base=base_shot0, branch=None),
            _match(operation="unchanged", base=base_shot1, branch=a_branch0),
            _match(operation="unchanged", base=base_shot2, branch=a_branch1),
        ],
    )
    b_alignment = _alignment(
        branch_name="branch_b",
        base_video_id="base",
        branch_video_id="branch_b",
        matches=[
            _match(operation="unchanged", base=base_shot0, branch=b_branch0),
            _match(operation="replace", base=base_shot1, branch=b_branch1),
            _match(operation="unchanged", base=base_shot2, branch=b_branch2),
        ],
    )

    branch_b_video = _make_video_representation(
        video_id="branch_b",
        shots=[
            _make_branch_shot(index=0, text=base_texts[0]),
            _make_branch_shot(index=1, text="Lift the cover."),
            _make_branch_shot(index=2, text=base_texts[2]),
        ],
    )

    combined_content = reconstruct_combined_actual_content(
        a_alignment=a_alignment,
        b_alignment=b_alignment,
        branch_a=branch_a_video,
        branch_b=branch_b_video,
        base=base,
    )

    # The public ``text_lines()`` API (what M3 reads in STEP 3)
    # contains ONLY the surviving actual content — no edit
    # markers, no BASE leakage into deleted slots.
    text_lines = combined_content.text_lines()
    joined_lower = " ".join(text_lines).lower()
    assert "lift the cover" in joined_lower
    assert "battery" in joined_lower
    assert "before opening" not in joined_lower
    assert "unplug it from the wall" not in joined_lower
    assert "once the device is unplugged" not in joined_lower

    # The explicit mechanical-conflict surface: the composer
    # attached the combined timeline so forensic consumers can
    # inspect every slice verdict, the per-position unit_a /
    # unit_b provenance, and any unanchored inserts.
    timeline = combined_content.combined_timeline
    assert timeline is not None
    verdicts = {s.base_index: s.verdict for s in timeline.slices}
    assert verdicts[0] == "deleted"
    assert verdicts[1] == "replaced"
    assert verdicts[2] == "preserved"
    # B's replacement is keyed to BASE[1] (the original BASE
    # identity), NOT to BASE[0] (which was deleted). The
    # branch_sequence_position metadata captures the shifted
    # current position separately.
    s1 = next(s for s in timeline.slices if s.base_index == 1)
    assert s1.unit_b is not None
    assert s1.unit_b.base_index == 1
    assert s1.unit_b.operation == EditKind.REPLACE
    assert s1.unit_b.replacement_text == "lift the cover."
    # BASE[0]'s slice carries A's delete on the right identity.
    s0 = next(s for s in timeline.slices if s.base_index == 0)
    assert s0.unit_a is not None
    assert s0.unit_a.operation == EditKind.DELETE
    assert s0.unit_a.base_shot_id == "base_0000"
    # No unanchored inserts on this case.
    assert timeline.unresolved_inserts == []
    assert all(not s.unanchored_inserts for s in timeline.slices)

    # The per-line metadata records the canonical delete (BASE[0])
    # and the canonical replace (BASE[1]).
    op_seq = [line.operation for line in combined_content.lines]
    assert op_seq == ["delete", "replace", "unchanged"]
    delete_entry = next(
        e
        for e in combined_content.edit_metadata.entries
        if e.operation == "delete" and e.base_sequence_index == 0
    )
    assert delete_entry.branch == "combined"
    replace_entry = next(
        e
        for e in combined_content.edit_metadata.entries
        if e.operation == "replace" and e.base_sequence_index == 1
    )
    assert replace_entry.branch == "combined"
