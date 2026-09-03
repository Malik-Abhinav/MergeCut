"""Unit tests for `app.services.alignment.fingerprints`.

Covers the pure-Python pieces (transcript normalization, tokenization,
Hamming distance) and the build_fingerprints() builder's
deterministic invariants. The pHash itself is exercised in
`test_phash.py`; here we only need to confirm the public builder
uses it correctly and that two identical inputs produce identical
fingerprints.

Phase 3 visual-fingerprint repair: also exercises the colour
fingerprint (mean RGB + per-channel histogram) and the
`mean_rgb_similarity` / `histogram_intersection` helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.media import (
    Shot,
    TranscriptSegment,
    VideoMetadata,
    VideoRepresentation,
)
from app.services.alignment.fingerprints import (
    _HIST_BINS_PER_CHANNEL,
    _HIST_TOTAL_BINS,
    _histogram_from_keyframe,
    _mean_rgb_from_keyframe,
    _normalize_transcript,
    _phash_from_keyframe,
    _tokenize,
    build_fingerprints,
    hamming_hex,
    histogram_intersection,
    mean_rgb_similarity,
)

# ---------------------------------------------------------------------------
# Transcript normalization + tokenization.
# ---------------------------------------------------------------------------


def test_normalize_lowercases_and_strips_punctuation() -> None:
    assert _normalize_transcript("Hello, World! 123?") == "hello world 123"


def test_normalize_collapses_whitespace() -> None:
    assert _normalize_transcript("  a\tb\n c  ") == "a b c"


def test_normalize_empty_input() -> None:
    assert _normalize_transcript("") == ""


def test_tokenize_basic() -> None:
    assert _tokenize("a b c") == ["a", "b", "c"]


def test_tokenize_empty() -> None:
    assert _tokenize("") == []


# ---------------------------------------------------------------------------
# Hamming distance on hex pHashes.
# ---------------------------------------------------------------------------


def test_hamming_identical_zero() -> None:
    assert hamming_hex("abcd1234", "abcd1234") == 0


def test_hamming_one_bit_diff() -> None:
    # Last hex char 0 -> 1 flips exactly one bit.
    assert hamming_hex("abcd1230", "abcd1231") == 1


def test_hamming_eight_bit_diff() -> None:
    # 0x00 -> 0xFF flips 8 bits.
    assert hamming_hex("abcd1234", "abcd12ff") >= 4


def test_hamming_handles_length_mismatch() -> None:
    # Defensive: returns 0 on unequal lengths rather than raising.
    assert hamming_hex("abc", "abcd") == 0


def test_hamming_handles_empty() -> None:
    assert hamming_hex("", "abcd") == 0


# ---------------------------------------------------------------------------
# pHash determinism.
# ---------------------------------------------------------------------------


def test_phash_returns_sixteen_hex_chars(tmp_path: Path) -> None:
    img = tmp_path / "red.png"
    # Build a small solid-colour PNG (10x10) using Pillow so we
    # don't need a fixture file in the repo.
    from PIL import Image

    Image.new("RGB", (32, 32), (255, 0, 0)).save(img)
    h = _phash_from_keyframe(img)
    assert len(h) == 16
    int(h, 16)  # parses as hex


def test_phash_deterministic(tmp_path: Path) -> None:
    from PIL import Image

    img = tmp_path / "blue.png"
    Image.new("RGB", (64, 48), (0, 0, 255)).save(img)
    assert _phash_from_keyframe(img) == _phash_from_keyframe(img)


def test_phash_different_colours_diverge(tmp_path: Path) -> None:
    from PIL import Image

    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGB", (32, 32), (255, 0, 0)).save(red)
    Image.new("RGB", (32, 32), (0, 0, 255)).save(blue)
    # The 9-bit luminance prefix guarantees these differ.
    assert _phash_from_keyframe(red) != _phash_from_keyframe(blue)


def test_phash_missing_file_returns_zero_hash(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.png"
    assert _phash_from_keyframe(missing) == "0" * 16


# ---------------------------------------------------------------------------
# build_fingerprints() builder.
# ---------------------------------------------------------------------------


def _make_rep(*, shots: list[Shot], audio: bool = True) -> VideoRepresentation:
    metadata = VideoMetadata(
        duration_seconds=shots[-1].end if shots else 0.0,
        width=320,
        height=240,
        fps=30.0,
        codec="h264",
        audio_present=audio,
        audio_codec="aac" if audio else None,
    )
    return VideoRepresentation.from_components(
        video_id="0000000000000000",
        source_path=Path("/tmp/source.mp4"),
        normalized_path=Path("/tmp/working.mp4"),
        audio_path=Path("/tmp/audio.wav") if audio else None,
        metadata=metadata,
        normalization=__import__(
            "app.models.media", fromlist=["NormalizationInfo"]
        ).NormalizationInfo(normalized=False),
        shots=shots,
    )


def _make_shot(
    *,
    shot_id: str,
    start: float,
    end: float,
    text: str = "",
    keyframe: Path | None = None,
) -> Shot:
    return Shot(
        shot_id=shot_id,
        start=start,
        end=end,
        keyframe_paths=[keyframe] if keyframe is not None else [],
        transcript=text,
        transcript_segments=([TranscriptSegment(start=start, end=end, text=text)] if text else []),
    )


def test_build_fingerprints_preserves_order(tmp_path: Path) -> None:
    from PIL import Image

    kf_a = tmp_path / "a.png"
    kf_b = tmp_path / "b.png"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(kf_a)
    Image.new("RGB", (32, 32), (200, 100, 50)).save(kf_b)

    rep = _make_rep(
        shots=[
            _make_shot(shot_id="s0", start=0.0, end=2.0, keyframe=kf_a),
            _make_shot(shot_id="s1", start=2.0, end=4.0, keyframe=kf_b),
        ]
    )
    fps = build_fingerprints(rep)
    assert [fp.shot_id for fp in fps] == ["s0", "s1"]
    assert [fp.sequence_index for fp in fps] == [0, 1]


def test_build_fingerprints_extracts_transcript_tokens() -> None:
    rep = _make_rep(
        shots=[
            _make_shot(
                shot_id="s0",
                start=0.0,
                end=2.0,
                text="Step One, Open the device.",
            )
        ]
    )
    fps = build_fingerprints(rep)
    assert len(fps) == 1
    fp = fps[0]
    assert fp.has_speech is True
    assert fp.normalized_transcript == "step one open the device"
    assert fp.transcript_tokens == ["step", "one", "open", "the", "device"]


def test_build_fingerprints_empty_shot_has_no_speech() -> None:
    rep = _make_rep(shots=[_make_shot(shot_id="s0", start=0.0, end=2.0, text="")])
    fps = build_fingerprints(rep)
    assert fps[0].has_speech is False
    assert fps[0].transcript_tokens == []
    assert fps[0].normalized_transcript == ""


def test_build_fingerprints_no_keyframe_uses_zero_hash() -> None:
    rep = _make_rep(shots=[_make_shot(shot_id="s0", start=0.0, end=2.0, text="hi")])
    fps = build_fingerprints(rep)
    assert fps[0].visual_fingerprint == "0" * 16


def test_build_fingerprints_is_deterministic(tmp_path: Path) -> None:
    from PIL import Image

    kf = tmp_path / "kf.png"
    Image.new("RGB", (32, 32), (50, 50, 50)).save(kf)
    rep = _make_rep(shots=[_make_shot(shot_id="s0", start=0.0, end=2.0, text="hi", keyframe=kf)])
    a = build_fingerprints(rep)
    b = build_fingerprints(rep)
    assert a[0].visual_fingerprint == b[0].visual_fingerprint
    assert a[0].transcript_tokens == b[0].transcript_tokens


def test_build_fingerprints_empty_video() -> None:
    rep = _make_rep(shots=[])
    assert build_fingerprints(rep) == []


def test_build_fingerprints_computes_duration() -> None:
    rep = _make_rep(shots=[_make_shot(shot_id="s0", start=1.5, end=4.0, text="hi")])
    fps = build_fingerprints(rep)
    assert fps[0].duration == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# Colour fingerprint (Phase 3 visual repair).
# ---------------------------------------------------------------------------


def test_mean_rgb_solid_red(tmp_path: Path) -> None:
    from PIL import Image

    p = tmp_path / "red.png"
    Image.new("RGB", (32, 32), (255, 0, 0)).save(p)
    rgb = _mean_rgb_from_keyframe(p)
    assert rgb is not None
    r, g, b = rgb
    assert r == pytest.approx(1.0, abs=0.01)
    assert g == pytest.approx(0.0, abs=0.01)
    assert b == pytest.approx(0.0, abs=0.01)


def test_mean_rgb_solid_blue_different_from_red(tmp_path: Path) -> None:
    from PIL import Image

    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGB", (32, 32), (255, 0, 0)).save(red)
    Image.new("RGB", (32, 32), (0, 0, 255)).save(blue)
    mean_red = _mean_rgb_from_keyframe(red)
    mean_blue = _mean_rgb_from_keyframe(blue)
    assert mean_red is not None and mean_blue is not None
    # Red and blue have very different mean colours.
    sim = mean_rgb_similarity(mean_red, mean_blue)
    assert sim is not None and sim < 0.5


def test_mean_rgb_similarity_identical() -> None:
    rgb = (0.4, 0.2, 0.6)
    assert mean_rgb_similarity(rgb, rgb) == pytest.approx(1.0)


def test_mean_rgb_similarity_both_missing_returns_none() -> None:
    assert mean_rgb_similarity(None, None) is None


def test_mean_rgb_similarity_one_missing_returns_zero() -> None:
    assert mean_rgb_similarity((0.5, 0.5, 0.5), None) == 0.0
    assert mean_rgb_similarity(None, (0.5, 0.5, 0.5)) == 0.0


def test_mean_rgb_similarity_extreme_full_distance() -> None:
    # Black (0,0,0) vs white (1,1,1) → L1=2.0 → similarity=0.0.
    assert mean_rgb_similarity((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)) == pytest.approx(0.0)


def test_histogram_total_bins() -> None:
    assert _HIST_TOTAL_BINS == 3 * _HIST_BINS_PER_CHANNEL


def test_histogram_solid_red(tmp_path: Path) -> None:
    from PIL import Image

    p = tmp_path / "red.png"
    Image.new("RGB", (32, 32), (255, 0, 0)).save(p)
    h = _histogram_from_keyframe(p)
    assert h is not None
    assert len(h) == _HIST_TOTAL_BINS
    assert sum(h) == pytest.approx(1.0, abs=1e-9)
    # The histogram concatenates R + G + B (12 values total,
    # normalized to sum to 1.0). Solid red → R channel is fully
    # in the highest bin (R=255 → bin 3), G and B are fully in
    # the lowest bin (G=0 / B=0 → bin 0). Each channel's 4-bin
    # sub-histogram is a one-hot, so the channel sums are
    # 1.0 / 3 (one third of the 1.0 total).
    r_bins = h[0:_HIST_BINS_PER_CHANNEL]
    g_bins = h[_HIST_BINS_PER_CHANNEL : 2 * _HIST_BINS_PER_CHANNEL]
    b_bins = h[2 * _HIST_BINS_PER_CHANNEL :]
    third = pytest.approx(1.0 / 3.0, abs=1e-6)
    assert sum(r_bins) == third
    assert sum(g_bins) == third
    assert sum(b_bins) == third
    # R is concentrated in the highest bin; G and B in the lowest.
    assert r_bins[-1] == pytest.approx(1.0 / 3.0, abs=1e-6)
    assert g_bins[0] == pytest.approx(1.0 / 3.0, abs=1e-6)
    assert b_bins[0] == pytest.approx(1.0 / 3.0, abs=1e-6)


def test_histogram_solid_yellow_different_from_solid_red(tmp_path: Path) -> None:
    from PIL import Image

    red = tmp_path / "red.png"
    yellow = tmp_path / "yellow.png"
    Image.new("RGB", (32, 32), (255, 0, 0)).save(red)
    Image.new("RGB", (32, 32), (255, 255, 0)).save(yellow)
    h_red = _histogram_from_keyframe(red)
    h_yellow = _histogram_from_keyframe(yellow)
    assert h_red is not None and h_yellow is not None
    sim = histogram_intersection(h_red, h_yellow)
    assert sim is not None and sim < 1.0


def test_histogram_intersection_identical() -> None:
    h = (0.25, 0.25, 0.25, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert histogram_intersection(h, h) == pytest.approx(1.0)


def test_histogram_intersection_disjoint_is_zero() -> None:
    a = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    b = (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert histogram_intersection(a, b) == pytest.approx(0.0)


def test_histogram_intersection_both_missing_returns_none() -> None:
    assert histogram_intersection(None, None) is None


def test_histogram_intersection_one_missing_returns_zero() -> None:
    h = (0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert histogram_intersection(h, None) == 0.0
    assert histogram_intersection(None, h) == 0.0


def test_histogram_intersection_length_mismatch_returns_zero() -> None:
    a = (0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    b = (0.5, 0.5, 0.0, 0.0)  # wrong length
    assert histogram_intersection(a, b) == 0.0


def test_build_fingerprints_populates_color_components(tmp_path: Path) -> None:
    from PIL import Image

    kf = tmp_path / "red.png"
    Image.new("RGB", (32, 32), (200, 50, 50)).save(kf)
    rep = _make_rep(shots=[_make_shot(shot_id="s0", start=0.0, end=1.0, text="hi", keyframe=kf)])
    fps = build_fingerprints(rep)
    assert len(fps) == 1
    fp = fps[0]
    # pHash + colour are both populated.
    assert fp.visual_fingerprint != "0" * 16
    assert fp.color_mean_rgb is not None
    assert fp.color_histogram is not None
    assert sum(fp.color_histogram) == pytest.approx(1.0, abs=1e-9)


def test_build_fingerprints_no_keyframe_uses_none_for_color(tmp_path: Path) -> None:
    rep = _make_rep(shots=[_make_shot(shot_id="s0", start=0.0, end=1.0, text="hi")])
    fps = build_fingerprints(rep)
    assert fps[0].color_mean_rgb is None
    assert fps[0].color_histogram is None


def test_color_fingerprint_deterministic(tmp_path: Path) -> None:
    from PIL import Image

    kf = tmp_path / "kf.png"
    Image.new("RGB", (32, 32), (50, 50, 50)).save(kf)
    rep = _make_rep(shots=[_make_shot(shot_id="s0", start=0.0, end=1.0, text="hi", keyframe=kf)])
    a = build_fingerprints(rep)
    b = build_fingerprints(rep)
    assert a[0].color_mean_rgb == b[0].color_mean_rgb
    assert a[0].color_histogram == b[0].color_histogram
