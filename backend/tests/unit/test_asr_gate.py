"""Unit tests for the Phase 4 ASR semantic-integrity gate.

These tests exercise the deterministic lexical gate on synthetic
text pairs; they do not need faster-whisper or a real video. The
goal is to lock in the *behavior* of the gate so the live eval
can trust the eligibility verdicts.
"""

from __future__ import annotations

from tests.fixtures.asr_gate import (
    DEFAULT_SIMILARITY_THRESHOLD,
    NEGATION_MARKERS,
    PREREQUISITE_MARKERS,
    QUALIFIER_MARKERS,
    SEMANTIC_MARKER_CATEGORIES,
    find_missing_markers,
    jaccard,
    similarity,
    tokenize,
)  # noqa: I001

# ---------------------------------------------------------------------------
# tokenize.
# ---------------------------------------------------------------------------


def test_tokenize_lowercases_and_strips_punctuation() -> None:
    assert tokenize("Before opening the device,") == [
        "before",
        "opening",
        "the",
        "device",
    ]


def test_tokenize_handles_number_word_equivalence() -> None:
    assert "10" in tokenize("for ten minutes")
    assert "10" in tokenize("for 10 minutes")
    assert "7" in tokenize("for seven days")
    assert "7" in tokenize("for 7 days")


def test_tokenize_drops_plural_s() -> None:
    # "allergies" and "allergy" should tokenize to the same stem
    # so the marker scan can match them. The tokenizer strips a
    # trailing "ies" (→ "y") and a single trailing "s" (for
    # "days" → "day"). The set is what we use for membership;
    # both expected and recognized share the same tokenizer, so
    # this is consistent.
    e = set(tokenize("severe nut allergies"))
    r = set(tokenize("severe nut allergy"))
    assert "allergy" in e
    assert "allergy" in r
    assert e == r
    # "days" → "day"
    assert "day" in tokenize("for 7 days")
    assert "day" in tokenize("for 7 day")


def test_tokenize_strips_trailing_apostrophe_s() -> None:
    # Daniel may say "customers'" (possessive). Strip the "'s"
    # so the token is "customer", which matches the bare plural
    # after the 's' strip.
    assert "customer" in tokenize("customers'")


def test_tokenize_empty() -> None:
    assert tokenize("") == []
    assert tokenize("   ") == []


# ---------------------------------------------------------------------------
# jaccard / similarity.
# ---------------------------------------------------------------------------


def test_jaccard_identical_is_one() -> None:
    assert jaccard(["before", "opening", "the"], ["before", "opening", "the"]) == 1.0


def test_jaccard_disjoint_is_zero() -> None:
    assert jaccard(["a", "b"], ["c", "d"]) == 0.0


def test_jaccard_partial_overlap() -> None:
    # 3 in intersection (a, b, c); 7 in union (a..g) → 3/7.
    assert jaccard(["a", "b", "c", "d", "e"], ["a", "b", "c", "f", "g"]) == 3 / 7


def test_jaccard_both_empty_is_one() -> None:
    assert jaccard([], []) == 1.0


def test_similarity_handles_punctuation_differences() -> None:
    # "Once the device is unplugged: lift the cover." vs
    # "Once the device is unplugged, lift the cover." — same
    # meaning, different punctuation. Token Jaccard should be
    # high enough to pass the threshold.
    sim = similarity(
        "Once the device is unplugged: lift the cover.",
        "Once the device is unplugged, lift the cover.",
    )
    assert sim >= DEFAULT_SIMILARITY_THRESHOLD


def test_similarity_drops_negation() -> None:
    # "Do not exceed the dose" vs "Do exceed the dose" — Jaccard
    # should be lower because "not" is missing. The exact delta
    # depends on the rest of the tokens; we just require the
    # loss to be measurable.
    a = "Do not exceed the recommended dose of this medication."
    b = "Do exceed the recommended dose of this medication."
    sim_neg = similarity(a, b)
    sim_full = similarity(a, a)
    assert sim_full > sim_neg
    # Token sets: expected = {do, not, exceed, the, recommended, dose, of, this, medication}
    # (9 tokens). Recognized (b) = {do, exceed, the, recommended, dose, of, this, medication}
    # (8 tokens). Intersection = 8; union = 9. Jaccard = 8/9 ≈ 0.889.
    # We confirm that the missing "not" token drops the
    # similarity below 1.0 by a non-trivial margin.
    assert sim_full - sim_neg > 0.05


# ---------------------------------------------------------------------------
# find_missing_markers.
# ---------------------------------------------------------------------------


def test_find_missing_markers_negation_lost() -> None:
    missing = find_missing_markers(
        "Do not exceed the recommended dose of this medication.",
        "Do exceed the recommended dose of this medication.",
    )
    assert "negation" in missing
    assert "not" in missing["negation"]


def test_find_missing_markers_prerequisite_lost() -> None:
    # "Before opening the device" — "before" is a prerequisite
    # marker; if it's missing, the gate flags it.
    missing = find_missing_markers(
        "Before opening the device, unplug it from the wall.",
        "After opening the device, unplug it from the wall.",
    )
    assert "prerequisite" in missing
    assert "before" in missing["prerequisite"]


def test_find_missing_markers_qualifier_lost() -> None:
    missing = find_missing_markers(
        "Patients with severe nut allergies must avoid this product.",
        "Patients with nut allergies must avoid this product.",
    )
    # "severe" is a qualifier marker; when it's missing, the
    # gate flags the qualifier category.
    assert "qualifier" in missing
    assert "severe" in missing["qualifier"]


def test_find_missing_markers_no_flags_when_clean() -> None:
    # Same text on both sides → no missing markers.
    missing = find_missing_markers(
        "Before opening the device, unplug it from the wall.",
        "Before opening the device, unplug it from the wall.",
    )
    assert missing == {}


def test_find_missing_markers_only_checks_present_categories() -> None:
    # An expected line with no temporal/causal content should
    # not produce a "temporal" flag just because the recognized
    # text also has no temporal content.
    missing = find_missing_markers(
        "Lift the cover.",
        "Lift the cover.",
    )
    assert "temporal" not in missing
    assert "causal" not in missing


def test_find_missing_markers_entity_multi_word() -> None:
    # "all customers" is a multi-word entity marker. The gate
    # only flags the entity category when *all* words of the
    # marker are present in the expected AND at least one is
    # missing from the recognized.
    missing = find_missing_markers(
        "All customers with nut allergies: ask staff for alternatives.",
        "Customers with nut allergies: ask staff for alternatives.",
    )
    assert "entity" in missing
    assert "all customers" in missing["entity"]


def test_find_missing_markers_temporal_multi_word() -> None:
    missing = find_missing_markers(
        "Apply the cream twice daily for seven days.",
        "Apply the cream twice daily.",
    )
    assert "temporal" in missing
    # Either "for 7 days" or "for seven days" should be flagged;
    # we accept both because the gate stores the marker literal.
    assert any("7 days" in m or "ten minutes" in m for m in missing["temporal"])


# ---------------------------------------------------------------------------
# Sanity: marker categories.
# ---------------------------------------------------------------------------


def test_marker_categories_nonempty() -> None:
    assert NEGATION_MARKERS
    assert PREREQUISITE_MARKERS
    assert QUALIFIER_MARKERS
    assert "negation" in SEMANTIC_MARKER_CATEGORIES
    assert "prerequisite" in SEMANTIC_MARKER_CATEGORIES
    assert "qualifier" in SEMANTIC_MARKER_CATEGORIES
    assert "exception" in SEMANTIC_MARKER_CATEGORIES
    assert "entity" in SEMANTIC_MARKER_CATEGORIES
    assert "temporal" in SEMANTIC_MARKER_CATEGORIES
    assert "causal" in SEMANTIC_MARKER_CATEGORIES
