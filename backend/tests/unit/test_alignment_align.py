"""Unit tests for `app.services.alignment.align`.

Covers the DP alignment of `ShotFingerprint` sequences — the
core of Phase 3. We use synthetic fingerprints (no real video)
because the DP is fully deterministic and pure-Python.

Cases:

- Identical sequences → all MATCH.
- One branch shot deleted → one DELETE.
- One branch shot inserted → one INSERT.
- Branch is a strict subset of BASE → consecutive DELETEs.
- Mismatch on one pair → MATCH elsewhere (skip-penalty logic).
- Empty sequences.
"""

from __future__ import annotations

from app.models.alignment import ShotFingerprint
from app.services.alignment.align import SKIP_PENALTY, Transition, align_sequences


def _fp(idx: int, *, duration: float = 1.0, vfp: str | None = None) -> ShotFingerprint:
    if vfp is None:
        # Stable, distinct hashes so the similarity blend is
        # deterministic and clean.
        vfp = f"{idx:016x}"
    return ShotFingerprint(
        shot_id=f"shot_{idx:04d}",
        start=float(idx),
        end=float(idx) + duration,
        duration=duration,
        keyframe_paths=[],
        visual_fingerprint=vfp,
        normalized_transcript="",
        transcript_tokens=[],
        has_speech=False,
        sequence_index=idx,
    )


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------


def test_both_empty() -> None:
    assert align_sequences([], []) == []


def test_empty_base_all_inserts() -> None:
    branch = [_fp(0), _fp(1)]
    out = align_sequences([], branch)
    assert len(out) == 2
    assert all(t == Transition.INSERT for t, _, _ in out)
    assert [b.shot_id for _, _, b in out] == ["shot_0000", "shot_0001"]


def test_empty_branch_all_deletes() -> None:
    base = [_fp(0), _fp(1)]
    out = align_sequences(base, [])
    assert len(out) == 2
    assert all(t == Transition.DELETE for t, _, _ in out)
    assert [b.shot_id for t, b, _ in out] == ["shot_0000", "shot_0001"]


# ---------------------------------------------------------------------------
# Identical sequences.
# ---------------------------------------------------------------------------


def test_identical_sequences_all_match() -> None:
    a = [_fp(0), _fp(1), _fp(2)]
    b = [_fp(0), _fp(1), _fp(2)]
    out = align_sequences(a, b)
    assert len(out) == 3
    assert all(t == Transition.MATCH for t, _, _ in out)
    for (_t, base_s, branch_s), expected_idx in zip(out, [0, 1, 2], strict=True):
        assert base_s is not None
        assert branch_s is not None
        assert base_s.sequence_index == expected_idx
        assert branch_s.sequence_index == expected_idx


# ---------------------------------------------------------------------------
# Single deletion.
# ---------------------------------------------------------------------------


def test_one_deletion_in_middle() -> None:
    a = [_fp(0), _fp(1), _fp(2)]
    # Branch drops shot 1 → only shots 0 and 2.
    b = [_fp(0), _fp(2)]
    out = align_sequences(a, b)
    # 3 transitions: MATCH(0,0), DELETE(1,None), MATCH(2,2)
    assert [t for t, _, _ in out] == [
        Transition.MATCH,
        Transition.DELETE,
        Transition.MATCH,
    ]
    delete_match = out[1]
    assert delete_match[1] is not None
    assert delete_match[1].sequence_index == 1
    assert delete_match[2] is None


def test_deletion_at_start() -> None:
    a = [_fp(0), _fp(1), _fp(2)]
    b = [_fp(1), _fp(2)]
    out = align_sequences(a, b)
    assert [t for t, _, _ in out] == [
        Transition.DELETE,
        Transition.MATCH,
        Transition.MATCH,
    ]


def test_deletion_at_end() -> None:
    a = [_fp(0), _fp(1), _fp(2)]
    b = [_fp(0), _fp(1)]
    out = align_sequences(a, b)
    assert [t for t, _, _ in out] == [
        Transition.MATCH,
        Transition.MATCH,
        Transition.DELETE,
    ]


# ---------------------------------------------------------------------------
# Single insertion.
# ---------------------------------------------------------------------------


def test_one_insertion_in_middle() -> None:
    a = [_fp(0), _fp(2)]
    b = [_fp(0), _fp(1), _fp(2)]
    out = align_sequences(a, b)
    assert [t for t, _, _ in out] == [
        Transition.MATCH,
        Transition.INSERT,
        Transition.MATCH,
    ]
    insert_match = out[1]
    assert insert_match[1] is None
    assert insert_match[2] is not None
    assert insert_match[2].sequence_index == 1


# ---------------------------------------------------------------------------
# Multiple consecutive operations.
# ---------------------------------------------------------------------------


def test_two_consecutive_deletes() -> None:
    a = [_fp(0), _fp(1), _fp(2), _fp(3)]
    b = [_fp(0), _fp(3)]
    out = align_sequences(a, b)
    assert [t for t, _, _ in out] == [
        Transition.MATCH,
        Transition.DELETE,
        Transition.DELETE,
        Transition.MATCH,
    ]


def test_two_consecutive_inserts() -> None:
    a = [_fp(0), _fp(3)]
    b = [_fp(0), _fp(1), _fp(2), _fp(3)]
    out = align_sequences(a, b)
    assert [t for t, _, _ in out] == [
        Transition.MATCH,
        Transition.INSERT,
        Transition.INSERT,
        Transition.MATCH,
    ]


# ---------------------------------------------------------------------------
# Monotonicity.
# ---------------------------------------------------------------------------


def test_alignment_respects_monotonicity() -> None:
    # Branch is reversed. The DP must NOT reorder — it has to
    # either pair everything as inserts (skipping all base) or
    # pair with low-quality matches. Whatever it does, the
    # base shots must appear in order.
    a = [_fp(0), _fp(1), _fp(2)]
    b = [_fp(2), _fp(1), _fp(0)]
    out = align_sequences(a, b)
    base_seq = [s.sequence_index for t, s, _ in out if s is not None]
    assert base_seq == sorted(base_seq)


# ---------------------------------------------------------------------------
# Skip penalty is close to zero.
# ---------------------------------------------------------------------------


def test_skip_penalty_is_close_to_zero() -> None:
    # We want the DP to prefer a match over a skip even when the
    # match is mediocre. Verify the constant is the right shape.
    assert -0.5 < SKIP_PENALTY < 0.0


def test_weak_match_preferred_over_skip() -> None:
    # Construct a sequence where forcing a skip would yield a
    # higher score than matching two weakly-related shots. With
    # the current skip penalty, we expect MATCH to be chosen.
    a = [_fp(0, vfp="0000000000000000"), _fp(1, vfp="0000000000000000")]
    # Branch has completely different hashes (visual 0.0 match).
    b = [_fp(0, vfp="ffffffffffffffff"), _fp(1, vfp="ffffffffffffffff")]
    out = align_sequences(a, b)
    # The DP still prefers MATCH because the blend also weights
    # duration + order which are both 1.0 → final ~ 0.25, and
    # the skip penalty is only -0.05.
    matches = [t for t, _, _ in out if t == Transition.MATCH]
    assert len(matches) == 2
