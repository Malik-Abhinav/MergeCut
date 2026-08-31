"""Tests pinning the v2.0.0 prompt contract.

These are not tests of model behaviour; they pin the *structure* of the
prompt itself so a future prompt edit cannot silently drop the
decision rule or the branch-full-content material that makes the rule
operable. If you change the prompt, you must update these tests in
lockstep and bump `PROMPT_VERSION`.
"""

from __future__ import annotations

from app.services.minimax import PROMPT_VERSION, build_user_payload_for_fixture
from app.services.minimax.prompts import (
    PROMPT_VERSION as _PROMPT_VERSION,
)
from app.services.minimax.prompts import (
    SYSTEM_INTENT,
    build_branch_view,
    build_user_payload,
)
from tests.fixtures.spike_fixtures import FIXTURES, get_fixture


def test_prompt_version_bumped() -> None:
    # v2.x — current is whatever is set in prompts.py.
    assert PROMPT_VERSION.startswith("2.")
    assert _PROMPT_VERSION.startswith("2.")


def test_system_intent_carries_decision_rule_verbatim() -> None:
    """The user's exact decision rule must be present in the system
    intent so M3 cannot paraphrase it away."""
    expected_phrases = [
        "branch_a_safe",
        "branch_b_safe",
        "combined_safe",
        "Would a reasonable viewer watching Branch A alone",
        "Would a reasonable viewer watching Branch B alone",
        "Would a reasonable viewer watching the result after",
        "A branch is NOT unsafe merely because it removes one statement",
        # v2.1.0+: explicit guidance that implicit / presupposed
        # statements count as equivalent meaning.
        "even when that equivalent meaning is communicated IMPLICITLY",
        "presupposes that prerequisite",
        "parallel prohibition",
    ]
    for phrase in expected_phrases:
        assert phrase in SYSTEM_INTENT, f"missing in SYSTEM_INTENT: {phrase!r}"


def test_user_payload_includes_full_branch_views() -> None:
    """The v2 prompt must show M3 the FULL reconstructed branch
    content so it can check whether equivalent meaning survives."""
    payload = build_user_payload(
        base_context="BASE",
        branch_a_change="BRANCH A",
        branch_b_change="BRANCH B",
        mechanical_diff="MECH",
        branch_a_full="BRANCH A FULL CONTENT",
        branch_b_full="BRANCH B FULL CONTENT",
    )
    assert "BASE CONTEXT" in payload
    assert "BRANCH A FULL CONTENT" in payload
    assert "BRANCH B FULL CONTENT" in payload
    assert "BRANCH A FULL CONTENT (BASE after applying ONLY Branch A's edit)" in payload
    assert "BRANCH B FULL CONTENT (BASE after applying ONLY Branch B's edit)" in payload


def test_user_payload_for_fixture_embeds_reconstructed_views() -> None:
    """`build_user_payload_for_fixture` must materialise the branch
    views from the base + branch-change prose (not just echo them)."""
    f = get_fixture("01_prereq_loss")
    payload = build_user_payload_for_fixture(f)
    # Branch A deletes the unplug-prereq sentence. The reconstructed
    # view should mark it as deleted in branch A but leave it intact in
    # branch B.
    a_section_start = payload.index("BRANCH A FULL CONTENT")
    b_section_start = payload.index("BRANCH B FULL CONTENT")
    a_section = payload[a_section_start:b_section_start]
    b_section = payload[b_section_start:]
    assert "Before opening the device, unplug it" in b_section  # preserved in B
    assert "<<segment deleted in this branch>>" in a_section  # dropped in A


def test_branch_view_marks_deleted_segments() -> None:
    base = (
        "BASE video:\n"
        "[00:00\u201300:10] 'Keep this segment.'\n"
        "[00:10\u201300:20] 'Drop this segment.'\n"
        "[00:20\u201300:30] 'And this one stays.'\n"
    )
    branch = "BRANCH A: [00:10\u201300:20] 'Drop this segment.' --> DELETED."
    rendered = build_branch_view(base, branch)
    assert "Keep this segment." in rendered
    assert "And this one stays." in rendered
    assert "<<segment deleted in this branch>>" in rendered
    assert "Drop this segment." not in rendered


def test_branch_view_returns_base_unchanged_when_unparseable() -> None:
    """No `[mm:ss\u2013mm:ss]` lines -> return BASE as-is."""
    base = "BASE without timestamps."
    branch = "BRANCH A: something was deleted."
    assert build_branch_view(base, branch) == base


def test_every_fixture_has_a_well_formed_branch_view() -> None:
    """Smoke: every fixture renders a non-empty branch view for both A
    and B, otherwise the v2 prompt would be malformed for that
    fixture."""
    for f in FIXTURES:
        a = build_branch_view(f.base_context, f.branch_a_change)
        b = build_branch_view(f.base_context, f.branch_b_change)
        assert a.strip(), f
        assert b.strip(), f
