"""Tests for the v2 Phase 1 fixtures.

We never feed the model the expected label; these tests only verify that
the fixtures themselves are well-formed and that the local labels are
internally consistent with the schema. Live model evaluation lives in
`scripts/run_spike.py`.
"""

from __future__ import annotations

import pytest

from tests.fixtures.spike_fixtures import FIXTURES, get_fixture

# v2 fixture set: 5 original + 3 new = 8 total.
EXPECTED_TOTAL = 8
EXPECTED_CONFLICTS = 5
EXPECTED_SAFES = 3


def test_fixture_count() -> None:
    assert len(FIXTURES) == EXPECTED_TOTAL


def test_fixture_label_counts() -> None:
    conflicts = [f for f in FIXTURES if f.expected_label == "conflict"]
    safes = [f for f in FIXTURES if f.expected_label == "safe"]
    assert len(conflicts) == EXPECTED_CONFLICTS
    assert len(safes) == EXPECTED_SAFES


def test_fixture_ids_unique() -> None:
    ids = [f.id for f in FIXTURES]
    assert len(set(ids)) == len(ids)


def test_each_fixture_has_nonempty_fields() -> None:
    for f in FIXTURES:
        assert f.id, f
        assert f.base_context.strip(), f
        assert f.branch_a_change.strip(), f
        assert f.branch_b_change.strip(), f
        assert f.mechanical_diff.strip(), f


def test_conflict_fixtures_have_conflict_type() -> None:
    for f in FIXTURES:
        if f.expected_label == "conflict":
            assert f.expected_conflict_type is not None, f
            assert f.expected_conflict_type != "other", (
                f"{f.id}: 'other' is reserved for cases that don't fit; "
                "a hand-built fixture should be specific."
            )


def test_get_fixture_roundtrip() -> None:
    for f in FIXTURES:
        assert get_fixture(f.id) is f
    with pytest.raises(KeyError):
        get_fixture("does_not_exist")


# ---------------------------------------------------------------------------
# v2 NEW: per-branch safety fields.
# ---------------------------------------------------------------------------


def test_per_branch_safety_present_and_bool() -> None:
    for f in FIXTURES:
        assert isinstance(f.expected_branch_a_safe, bool), f
        assert isinstance(f.expected_branch_b_safe, bool), f


def test_canonical_conflict_axis_per_plan() -> None:
    """The three canonical cross-edit conflict fixtures must match the
    PROJECT_PLAN §2 / §15 axis:
        branch_a_safe = True
        branch_b_safe = True
        combined_safe = False (== expected_label == 'conflict')
    """
    canonical_ids = {"01_prereq_loss", "02_qualifier_loss", "03_cause_effect"}
    for f in FIXTURES:
        if f.id in canonical_ids:
            assert f.expected_label == "conflict", f
            assert f.expected_branch_a_safe is True, f
            assert f.expected_branch_b_safe is True, f


def test_07_has_a_unsafe_alone() -> None:
    """Fixture 07 is the new 'A truly unsafe alone, B safe, combined
    unsafe' case. It pins the rule that branch A can be unsafe even
    when it removes only one statement."""
    f = get_fixture("07_a_unsafe_b_safe")
    assert f.expected_label == "conflict"
    assert f.expected_branch_a_safe is False
    assert f.expected_branch_b_safe is True


def test_08_combined_safe_with_redundant_claim() -> None:
    """Fixture 08 is the new 'both weaken wording, redundant claim
    survives, combined safe' case. It pins the corollary that
    weakening is not the same as removing."""
    f = get_fixture("08_redundant_safe")
    assert f.expected_label == "safe"
    assert f.expected_branch_a_safe is True
    assert f.expected_branch_b_safe is True


def test_06_is_canonical_individually_safe_pattern() -> None:
    """Fixture 06 is the alternate framing of the canonical case —
    verifies the prompt change generalises rather than memorises 01."""
    f = get_fixture("06_classic_safeAB")
    assert f.expected_label == "conflict"
    assert f.expected_branch_a_safe is True
    assert f.expected_branch_b_safe is True


def test_original_safe_controls_remain_correct() -> None:
    """The two original safe controls must remain safe in v2."""
    for fid in ("04_safe_unrelated", "05_safe_independent"):
        f = get_fixture(fid)
        assert f.expected_label == "safe", f
        assert f.expected_branch_a_safe is True, f
        assert f.expected_branch_b_safe is True, f
