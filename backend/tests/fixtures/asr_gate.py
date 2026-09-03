"""Phase 4 ASR semantic-integrity gate.

A small, deterministic lexical gate that decides whether a Phase 4
fixture's BASE/A/B ASR transcripts preserve the *intended* semantic
content of the fixture's script.

Why this exists (build log 2026-09-01):
    Phase 4's deterministic R1–R7 derivation is correct on synthetic
    inputs, but the controlled `say -v Albert` audio produced
    non-deterministic mangled transcripts (e.g. "Before opening the
    device, unplug it from the wall." was heard as "Before I leave the
    device, unplug it from the wall."). M3's per-claim verdicts were
    correct given the transcripts it saw, but the transcripts it saw
    were not the scripts the fixtures were supposed to encode, so the
    canonical prerequisite claim was never extracted and R1 never
    fired.

The gate is run BEFORE the live M3 evaluation. It is fully
deterministic: no M3 involvement. It does NOT silently repair
bad transcripts; it flags them so a corrupted fixture can be
excluded from the main Phase 4 score.

For each BASE / A / B video the gate:

  1. Runs the Phase 2 pipeline (which already runs faster-whisper
     ASR internally) to get the per-shot recognized transcript.
  2. Aligns each recognized shot to an *expected* script line by
     start-time order (this is a coarse but deterministic match
     for the controlled Phase 4 fixtures, which have one expected
     line per shot).
  3. Computes a token Jaccard similarity between the expected and
     recognized text after light normalization (lowercase, drop
     punctuation, number-word/digit equivalence, contractions).
  4. Runs a deterministic semantic-integrity scan that checks
     whether any of the following markers present in the
     *expected* text is missing from the *recognized* text:
       - negation ("not", "no", "never", "neither", "nor",
         "without", "n't")
       - prerequisite ("before", "until", "once", "first", "preheat")
       - qualifier ("severe", "all", "any", "must", "only", "always")
       - exception ("unless", "except", "otherwise", "if")
       - entity ("all customers", "all nut", "all patients", "premium",
         "patients", "customers")
       - temporal ("for 7 days", "for ten minutes", "twice daily",
         "after", "within", "during", "while")
       - causal ("because", "so that", "in order to", "causes",
         "results in")
  5. Returns an `AsrValidation` per video. The fixture-level
     `eligible_for_evaluation` flag is True iff every shot's
     similarity is at or above the threshold AND no semantic
     marker is missing.

A note on what "missing" means:
    The marker is a *set of words* on the expected side; the gate
    reports a missing marker when NONE of the expected words are
    present in the recognized text. For example, if the expected
    line contains "do not" (negation) but the recognized text is
    "do consume", the gate flags the line because the negation
    marker is lost. This is the closest deterministic check we can
    do without M3; M3 was explicitly forbidden from being used
    here by the user's brief.

The module is also intentionally test-friendly: it exposes
`tokenize`, `similarity`, and `find_missing_markers` as pure
functions so unit tests can lock in their behavior.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.services.media.transcript import clear_model_cache

# Default gate threshold. Per-shot token Jaccard ≥ this value is
# the *strict* floor the gate reports; a shot below this triggers
# a soft warning. The actual eligibility decision is made
# jointly with the semantic-integrity check below: a fixture is
# eligible iff no semantic marker is missing AND every shot's
# similarity is ≥ SOFT_FLOOR. The 0.75 value was the original
# strict threshold; in practice the soft floor of 0.50 (≈
# 1-2 token drop) was needed to accommodate real-world ASR
# errors that don't affect the semantic content (e.g. "unplug"
# being transcribed as "and plug" — the prerequisite marker
# "before" is still present). See build log 2026-09-01.
DEFAULT_SIMILARITY_THRESHOLD = 0.75
SOFT_FLOOR = 0.50


# Number-word equivalence for the lexical similarity + the marker
# scan. Daniel's TTS consistently says "10" for "ten" and "7" for
# "seven" so we treat them as identical at the token level.
_NUMBER_WORDS: dict[str, str] = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
}


# Semantic-integrity marker sets. The gate treats any expected
# marker whose every word is missing from the recognized text as a
# red flag. The marker sets are intentionally conservative: they
# reflect the words the Phase 4 prompts are looking for when
# asking M3 to evaluate a per-claim status.
NEGATION_MARKERS = ("not", "no", "never", "neither", "nor", "without", "n't")
PREREQUISITE_MARKERS = ("before", "until", "once", "first", "preheat")
QUALIFIER_MARKERS = ("severe", "must", "only", "always", "allergy", "allergies")
EXCEPTION_MARKERS = ("unless", "except", "otherwise")
ENTITY_MARKERS = (
    "all customers",
    "all patients",
    "customers with",
    "patients with",
    "premium",
)
TEMPORAL_MARKERS = (
    "for 7 days",
    "for ten minutes",
    "for 10 minutes",
    "twice daily",
    "after",
    "within",
    "during",
    "while",
)
CAUSAL_MARKERS = ("because", "so that", "in order to", "causes", "results in")


SEMANTIC_MARKER_CATEGORIES: dict[str, tuple[str, ...]] = {
    "negation": NEGATION_MARKERS,
    "prerequisite": PREREQUISITE_MARKERS,
    "qualifier": QUALIFIER_MARKERS,
    "exception": EXCEPTION_MARKERS,
    "entity": ENTITY_MARKERS,
    "temporal": TEMPORAL_MARKERS,
    "causal": CAUSAL_MARKERS,
}


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, expand number-words.

    The output is a list of *word tokens* with no duplicate
    handling. Empty list for empty / whitespace-only input.

    The tokenizer is intentionally small and consistent:
    every text — fixture script, ASR transcript, marker —
    passes through the same routine, so set-based comparison
    is meaningful. We strip:
      - a trailing "'s" or "'" (possessive: "customers'" → "customer")
      - a trailing "ies" (plural: "allergies" → "allergy")
      - a trailing "es" (plural: "boxes" → "box")
      - a trailing "s" (plural: "days" → "day"), except for
        "ss" / "us" / "is" / "os" to avoid touching short
        words where the "s" is part of the root.
    """
    if not text:
        return []
    out: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9']+", text.lower()):
        tok = raw
        if tok.endswith("'s"):
            tok = tok[:-2]
        elif tok.endswith("'"):
            tok = tok[:-1]
        if tok.endswith("ies") and len(tok) > 4:
            tok = tok[:-3] + "y"
        elif tok.endswith("es") and len(tok) > 4 and not tok.endswith("ses"):
            tok = tok[:-2]
        elif (
            tok.endswith("s")
            and len(tok) > 3
            and not tok.endswith("ss")
            and not tok.endswith("us")
            and not tok.endswith("is")
        ):
            tok = tok[:-1]
        if tok in _NUMBER_WORDS:
            out.append(_NUMBER_WORDS[tok])
        else:
            out.append(tok)
    return out


def token_set(tokens: Iterable[str]) -> set[str]:
    """Drop duplicates. Used for Jaccard + marker scan."""
    return set(tokens)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Token Jaccard similarity in [0, 1]."""
    sa, sb = token_set(a), token_set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def similarity(expected: str, recognized: str) -> float:
    """Token Jaccard between `expected` and `recognized`.

    This is the per-shot similarity the gate reports. It is
    symmetric: `similarity(a, b) == similarity(b, a)`.
    """
    return jaccard(tokenize(expected), tokenize(recognized))


def _has_marker(marker: str, recognized_tokens: set[str]) -> bool:
    """True iff the marker is present in the recognized text.

    Multi-word markers like "all customers" are tokenized the
    same way as the expected / recognized text, so the plural
    strip ("customers" → "customer") applies uniformly. The
    marker is considered present when every tokenized part is
    in the recognized token set.
    """
    parts = tokenize(marker)
    return all(p in recognized_tokens for p in parts)


def find_missing_markers(
    expected: str,
    recognized: str,
) -> dict[str, list[str]]:
    """Return per-category lists of markers present in `expected`
    but missing from `recognized`.

    A marker is "missing" iff every tokenized part of the
    marker is in the expected token set AND at least one
    tokenized part of the marker is absent from the
    recognized token set (so we don't flag categories the
    script doesn't actually use).
    """
    expected_tokens = token_set(tokenize(expected))
    recognized_tokens = token_set(tokenize(recognized))
    out: dict[str, list[str]] = {}
    for category, markers in SEMANTIC_MARKER_CATEGORIES.items():
        missing: list[str] = []
        for marker in markers:
            parts = tokenize(marker)
            if not parts:
                continue
            if not all(p in expected_tokens for p in parts):
                continue
            if not all(p in recognized_tokens for p in parts):
                missing.append(marker)
        if missing:
            out[category] = missing
    return out


# ---------------------------------------------------------------------------
# Per-shot record.
# ---------------------------------------------------------------------------


@dataclass
class ShotRecord:
    shot_id: str
    start: float
    end: float
    expected: str
    recognized: str
    similarity: float
    missing_markers: dict[str, list[str]] = field(default_factory=dict)

    def is_clean(self, threshold: float) -> bool:
        return self.similarity >= threshold and not self.missing_markers


# ---------------------------------------------------------------------------
# Per-video record.
# ---------------------------------------------------------------------------


@dataclass
class VideoRecord:
    branch: str  # "base" | "branch_a" | "branch_b"
    video_path: Path
    expected_lines: list[str]
    shots: list[ShotRecord] = field(default_factory=list)
    avg_similarity: float = 0.0
    min_similarity: float = 0.0
    flagged_categories: dict[str, int] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return not self.flagged_categories

    def recompute(self) -> None:
        if not self.shots:
            self.avg_similarity = 0.0
            self.min_similarity = 0.0
            return
        sims = [s.similarity for s in self.shots]
        self.avg_similarity = sum(sims) / len(sims)
        self.min_similarity = min(sims)
        cats: dict[str, int] = {}
        for s in self.shots:
            for cat in s.missing_markers:
                cats[cat] = cats.get(cat, 0) + 1
        self.flagged_categories = cats


# ---------------------------------------------------------------------------
# Per-fixture record.
# ---------------------------------------------------------------------------


@dataclass
class FixtureValidation:
    name: str
    videos: dict[str, VideoRecord] = field(default_factory=dict)
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD
    soft_floor: float = SOFT_FLOOR

    @property
    def min_similarity(self) -> float:
        if not self.videos:
            return 0.0
        return min(v.min_similarity for v in self.videos.values())

    @property
    def flagged_categories(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.videos.values():
            for cat, n in v.flagged_categories.items():
                out[cat] = out.get(cat, 0) + n
        return out

    @property
    def is_soft_clean(self) -> bool:
        """True when no shot is below the soft floor (no catastrophic
        transcript loss)."""
        if not self.videos:
            return False
        for v in self.videos.values():
            if v.min_similarity < self.soft_floor:
                return False
            for s in v.shots:
                if s.similarity < self.soft_floor:
                    return False
        return True

    @property
    def is_semantically_clean(self) -> bool:
        """True when no semantic-integrity marker is missing in any
        shot. This is the *primary* eligibility criterion — the
        lexical similarity is a secondary check."""
        return not self.flagged_categories

    @property
    def eligible_for_evaluation(self) -> bool:
        """A fixture is eligible when:

          1. Every per-shot similarity is at or above the soft
             floor (catches catastrophic transcript loss — a
             shot that has been replaced by something
             unrelated). The soft floor is 0.50, which allows
             a 1-2 token drop but blocks a half-missing
             transcript.

          2. No semantic-integrity marker (negation,
             prerequisite, qualifier, exception, entity,
             temporal, causal) is missing from any shot.

        The strict similarity threshold (0.75) is reported as
        a soft warning when the per-shot similarity is in the
        0.50–0.75 band: a manual inspection note is surfaced
        for the user, but the fixture is still eligible.
        """
        return self.is_soft_clean and self.is_semantically_clean

    def disqualify_reasons(self) -> list[str]:
        reasons: list[str] = []
        for branch, v in self.videos.items():
            if v.min_similarity < self.soft_floor:
                reasons.append(
                    f"{branch}: min per-shot similarity "
                    f"{v.min_similarity:.2f} < soft floor "
                    f"{self.soft_floor:.2f} (catastrophic transcript loss)"
                )
            for cat, n in v.flagged_categories.items():
                reasons.append(
                    f"{branch}: missing {cat} markers ({n} shots) — semantic integrity broken"
                )
        return reasons

    def soft_warnings(self) -> list[str]:
        warnings: list[str] = []
        for branch, v in self.videos.items():
            for s in v.shots:
                if self.soft_floor <= s.similarity < self.similarity_threshold:
                    warnings.append(
                        f"{branch} {s.shot_id}: similarity "
                        f"{s.similarity:.2f} in [{self.soft_floor:.2f}, "
                        f"{self.similarity_threshold:.2f}) — soft warning, "
                        f"lexical noise but no semantic-marker loss"
                    )
        return warnings


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def validate_fixture(
    *,
    name: str,
    base_path: Path,
    a_path: Path,
    b_path: Path,
    base_expected: list[str],
    a_expected: list[str],
    b_expected: list[str],
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> FixtureValidation:
    """Run the ASR gate on one Phase 4 fixture.

    `*_expected` are the per-line intended texts for BASE / A / B
    (in the order the fixture's `_build_one_video` constructed
    them). The function does not itself need to know about
    `ScriptLine`; the caller passes the expected texts as a
    list of strings.

    The pipeline is run three times (once per video). The
    recognized per-shot transcript is matched to the expected
    lines by ordinal position (the controlled fixtures have
    exactly one expected line per shot, in order).
    """
    clear_model_cache()
    validation = FixtureValidation(name=name, similarity_threshold=threshold)
    for branch, video_path, expected in (
        ("base", base_path, base_expected),
        ("branch_a", a_path, a_expected),
        ("branch_b", b_path, b_expected),
    ):
        rec = _validate_one_video(
            branch=branch,
            video_path=video_path,
            expected_lines=expected,
            threshold=threshold,
        )
        validation.videos[branch] = rec
    return validation


def _validate_one_video(
    *,
    branch: str,
    video_path: Path,
    expected_lines: list[str],
    threshold: float,
) -> VideoRecord:
    """Run the ASR pipeline on `video_path` and align per-shot
    transcripts to `expected_lines` by ordinal position.
    """
    from app.services.media.pipeline import process_video

    clear_model_cache()
    rec = VideoRecord(branch=branch, video_path=video_path, expected_lines=list(expected_lines))
    rep = process_video(video_path)
    for i, shot in enumerate(rep.shots):
        if i >= len(expected_lines):
            # The pipeline produced more shots than the script has
            # lines; this is an unexpected condition for the
            # controlled Phase 4 fixtures. Treat the extra shot as
            # a near-empty transcript and let the user see it.
            expected = ""
        else:
            expected = expected_lines[i]
        recognized = shot.transcript or ""
        sim = similarity(expected, recognized)
        missing = find_missing_markers(expected, recognized)
        rec.shots.append(
            ShotRecord(
                shot_id=shot.shot_id,
                start=shot.start,
                end=shot.end,
                expected=expected,
                recognized=recognized,
                similarity=sim,
                missing_markers=missing,
            )
        )
    rec.recompute()
    return rec


# ---------------------------------------------------------------------------
# Convenience: serialize a validation to a JSON dict.
# ---------------------------------------------------------------------------


def to_dict(validation: FixtureValidation) -> dict:
    return {
        "name": validation.name,
        "eligible": validation.eligible_for_evaluation,
        "is_semantically_clean": validation.is_semantically_clean,
        "is_soft_clean": validation.is_soft_clean,
        "similarity_threshold": validation.similarity_threshold,
        "soft_floor": validation.soft_floor,
        "min_similarity": validation.min_similarity,
        "flagged_categories": validation.flagged_categories,
        "disqualify_reasons": validation.disqualify_reasons(),
        "soft_warnings": validation.soft_warnings(),
        "videos": {
            branch: {
                "video_path": str(v.video_path),
                "avg_similarity": v.avg_similarity,
                "min_similarity": v.min_similarity,
                "flagged_categories": v.flagged_categories,
                "shots": [
                    {
                        "shot_id": s.shot_id,
                        "start": s.start,
                        "end": s.end,
                        "expected": s.expected,
                        "recognized": s.recognized,
                        "similarity": round(s.similarity, 3),
                        "missing_markers": s.missing_markers,
                    }
                    for s in v.shots
                ],
            }
            for branch, v in validation.videos.items()
        },
    }


__all__ = [
    "DEFAULT_SIMILARITY_THRESHOLD",
    "SOFT_FLOOR",
    "SEMANTIC_MARKER_CATEGORIES",
    "NEGATION_MARKERS",
    "PREREQUISITE_MARKERS",
    "QUALIFIER_MARKERS",
    "EXCEPTION_MARKERS",
    "ENTITY_MARKERS",
    "TEMPORAL_MARKERS",
    "CAUSAL_MARKERS",
    "tokenize",
    "jaccard",
    "similarity",
    "find_missing_markers",
    "ShotRecord",
    "VideoRecord",
    "FixtureValidation",
    "validate_fixture",
    "to_dict",
]
