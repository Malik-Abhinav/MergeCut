"""Shot fingerprints: deterministic shot-level representations.

Built from a Phase 2 `VideoRepresentation`. Each fingerprint holds:

- timeline bounds (start, end, duration)
- a perceptual visual fingerprint (64-bit aHash, 16 hex chars)
- a colour fingerprint (mean RGB + per-channel histogram)
- a normalized transcript and token list
- flags (has_speech, sequence_index)

The visual fingerprint is computed from the *first* available
keyframe. Phase 2 ships exactly one keyframe per shot (the
midpoint frame); we use whichever the Phase 2 pipeline produced.

Implementation note: we use a pure-Pillow DCT-based pHash rather
than depending on `imagehash` (which is not in `pyproject.toml`
and which we don't want to add when 60 lines of numpy + PIL give
us the same answer). The output is a 16-hex-char string.

Phase 3 visual-fingerprint repair: the 64-bit pHash alone
*degenerates* on visually uniform / solid-colour content (e.g.
the Phase 3 controlled fixtures). The fingerprint therefore
additionally carries a colour fingerprint (mean RGB + a small
per-channel histogram). Both signals are exposed
independently; `app.services.alignment.similarity.visual_similarity`
blends them. See the build-log entry for the Phase 3 repair
for the rationale.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

import numpy as np
from PIL import Image

from app.models.alignment import ShotFingerprint
from app.models.media import VideoRepresentation

# 64-bit pHash → 16 hex chars. Stored as a string for portability
# across JSON serialization and human-readable diagnostics.
_HASH_BITS = 64
_HASH_HEX_CHARS = _HASH_BITS // 4
_ZERO_HASH = "0" * _HASH_HEX_CHARS

# Colour histogram: 4 bins per channel x 3 channels = 12 bins
# total. The histogram is *normalized* (sums to 1.0) so the
# downstream `histogram_intersection` similarity is in [0, 1].
_HIST_BINS_PER_CHANNEL = 4
_HIST_TOTAL_BINS = _HIST_BINS_PER_CHANNEL * 3

# Transcript normalization. Lowercase, strip punctuation, collapse
# whitespace. Keep digits + ASCII letters; everything else becomes
# whitespace before collapsing.
_PUNCT_RE = re.compile(f"[{re.escape(string.punctuation)}]")


def _normalize_transcript(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    lowered = text.lower()
    no_punct = _PUNCT_RE.sub(" ", lowered)
    return " ".join(no_punct.split())


def _tokenize(normalized: str) -> list[str]:
    """Whitespace-split tokenization.

    Kept trivial on purpose: the same logic the Phase 2 pipeline
    would have used at the boundary, and the simplest possible
    deterministic function for Jaccard similarity.
    """
    if not normalized:
        return []
    return normalized.split()


# ---------------------------------------------------------------------------
# Visual fingerprint (pHash via DCT).
# ---------------------------------------------------------------------------


def _dct_2d_8x8(gray32: np.ndarray) -> np.ndarray:
    """Compute an 8x8 DCT-II on a 32x32 grayscale float array.

    Implemented in numpy using the orthonormal DCT-II matrix
    factorization `C @ x @ C.T` with the standard orthonormal
    basis. This is deterministic, has no external deps, and is
    plenty fast for one keyframe per shot.

    We only need the top-left 8x8 of the result (the lowest
    frequencies), so we slice before allocating the full
    transform.
    """
    n = 32
    if gray32.shape != (n, n):
        raise ValueError(f"expected ({n},{n}) input, got {gray32.shape}")

    # Orthonormal DCT-II basis.
    k = np.arange(n)
    basis = np.cos(np.pi * np.outer(k + 0.5, k) / n)
    basis[0, :] *= 1.0 / np.sqrt(2.0)
    basis *= np.sqrt(2.0 / n)

    full = basis @ gray32 @ basis.T
    # Top-left 8x8 holds the lowest-frequency coefficients.
    return full[:8, :8]


def _phash_from_keyframe(path: Path) -> str:
    """Compute a 64-bit pHash of a keyframe image.

    Steps (standard pHash recipe):
    1. Reduce to 32x32 grayscale.
    2. Compute DCT, keep top-left 8x8 (excluding DC).
    3. Compare each coefficient against the median of the kept
       block; bits encode above/below median.
    4. Pack into 16 hex chars.

    To avoid the well-known pHash degeneracy on near-monochrome
    images (where the non-DC coefficients are all roughly the
    same sign, so the median test produces the same bit pattern
    regardless of colour), we replace the top 8 bits of the hash
    with a quantized mean-luminance code (8 levels). The
    remaining 56 bits are still the standard pHash median test,
    so visually-similar content still scores similarly.

    Returns "0" * 16 if the file cannot be read.
    """
    try:
        with Image.open(path) as im:
            gray = im.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    except (OSError, FileNotFoundError, ValueError):
        return _ZERO_HASH

    arr = np.asarray(gray, dtype=np.float64)
    dct = _dct_2d_8x8(arr)
    block = dct.copy()
    block[0, 0] = 0.0
    flat = block.flatten()
    med = float(np.median(flat))
    bits = (flat > med).astype(np.uint8)

    # 9-bit luminance prefix: 3 bits per channel of the
    # down-sampled image (mean R, G, B). This breaks the well-
    # known pHash degeneracy on near-monochrome frames where
    # the median test would otherwise collapse all
    # single-colour shots onto the same hash.
    r = g = b = 0
    try:
        with Image.open(path) as im2:
            px = im2.convert("RGB").resize((1, 1), Image.Resampling.LANCZOS).getpixel((0, 0))
        # `px` is the (R, G, B) tuple. PIL returns ints for an
        # "RGB" image but be defensive against future format
        # changes.
        if isinstance(px, tuple) and len(px) == 3:
            r, g, b = int(px[0]), int(px[1]), int(px[2])
    except (OSError, FileNotFoundError, ValueError, TypeError):
        r = g = b = 0
    prefix_bits = (
        ((int(r) >> 5) & 0x7) << 6 | ((int(g) >> 5) & 0x7) << 3 | ((int(b) >> 5) & 0x7)
    )  # 9 bits

    # Pack pHash bits (we have 64 of them; we use the bottom 55
    # to keep the total at 64).
    phash_packed = 0
    for bit in bits[:55]:
        phash_packed = (phash_packed << 1) | int(bit)

    full = (prefix_bits << 55) | phash_packed
    return f"{full:0{_HASH_HEX_CHARS}x}"


# ---------------------------------------------------------------------------
# Colour fingerprint: mean RGB + normalized per-channel histogram.
# ---------------------------------------------------------------------------


def _mean_rgb_from_keyframe(path: Path) -> tuple[float, float, float] | None:
    """Return mean RGB in [0, 1] from the keyframe at `path`.

    Returns None on any read/decode failure. The mean is
    computed once on a small (32x32) downsampled image — we do
    not need the full-resolution pixel mean for a coarse
    colour fingerprint.
    """
    try:
        with Image.open(path) as im:
            small = im.convert("RGB").resize((32, 32), Image.Resampling.LANCZOS)
            arr = np.asarray(small, dtype=np.float64) / 255.0  # (H, W, 3) in [0, 1]
    except (OSError, FileNotFoundError, ValueError, TypeError):
        return None
    mean = arr.mean(axis=(0, 1))  # (3,)
    return (float(mean[0]), float(mean[1]), float(mean[2]))


def _histogram_from_keyframe(
    path: Path,
) -> tuple[float, ...] | None:
    """Return a length-12 tuple of normalized histogram bins.

    4 bins per channel (R, G, B). The values are first
    *quantized* to `_HIST_BINS_PER_CHANNEL` bins per channel,
    then *normalized* to sum to 1.0 so the result is a
    distribution over the three channels. The bin index is
    `min(int(v * _HIST_BINS_PER_CHANNEL), _HIST_BINS_PER_CHANNEL - 1)`.

    Returns None on any read/decode failure.
    """
    try:
        with Image.open(path) as im:
            small = im.convert("RGB").resize((32, 32), Image.Resampling.LANCZOS)
            arr = np.asarray(small, dtype=np.uint8)  # (H, W, 3) in [0, 255]
    except (OSError, FileNotFoundError, ValueError, TypeError):
        return None
    counts = np.zeros((3, _HIST_BINS_PER_CHANNEL), dtype=np.float64)
    for c in range(3):
        chan = arr[..., c]
        # Quantize [0, 255] to 4 bins: 0..63, 64..127, 128..191, 192..255.
        bin_idx = (chan // (256 // _HIST_BINS_PER_CHANNEL)).astype(np.int64)
        bin_idx = np.clip(bin_idx, 0, _HIST_BINS_PER_CHANNEL - 1)
        for b in range(_HIST_BINS_PER_CHANNEL):
            counts[c, b] = float((bin_idx == b).sum())
    total = counts.sum()
    if total <= 0.0:
        return None
    counts /= total
    flat = counts.flatten()  # 12 values, R then G then B
    return tuple(float(x) for x in flat)


def build_fingerprints(rep: VideoRepresentation) -> list[ShotFingerprint]:
    """Convert every shot in `rep` into a `ShotFingerprint`.

    The order is the same as `rep.shots` (sequence_index is
    derived from that order).
    """
    out: list[ShotFingerprint] = []
    for idx, shot in enumerate(rep.shots):
        normalized = _normalize_transcript(shot.transcript)
        tokens = _tokenize(normalized)
        first_kf = shot.keyframe_paths[0] if shot.keyframe_paths else None
        vfp = _phash_from_keyframe(first_kf) if first_kf is not None else _ZERO_HASH
        mean_rgb = _mean_rgb_from_keyframe(first_kf) if first_kf is not None else None
        histogram = _histogram_from_keyframe(first_kf) if first_kf is not None else None
        out.append(
            ShotFingerprint(
                shot_id=shot.shot_id,
                start=shot.start,
                end=shot.end,
                duration=max(0.0, shot.end - shot.start),
                keyframe_paths=list(shot.keyframe_paths),
                visual_fingerprint=vfp,
                color_mean_rgb=mean_rgb,
                color_histogram=histogram,
                normalized_transcript=normalized,
                transcript_tokens=tokens,
                has_speech=bool(normalized),
                sequence_index=idx,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Hamming distance between two hex-encoded pHashes.
# ---------------------------------------------------------------------------


def hamming_hex(a: str, b: str) -> int:
    """Bit-level Hamming distance between two equal-length hex
    strings (e.g. two 16-hex-char pHashes).

    Works on Python int bit-ops so it doesn't depend on
    bit-strings. Returns 0 on empty strings.
    """
    if not a or not b or len(a) != len(b):
        return 0
    try:
        ai = int(a, 16)
        bi = int(b, 16)
    except ValueError:
        return 0
    return bin(ai ^ bi).count("1")


# ---------------------------------------------------------------------------
# Per-component colour similarity (used by similarity.py to build the
# blend). These live in `fingerprints.py` so the data definition and
# the metric that consumes it stay together.
# ---------------------------------------------------------------------------


def mean_rgb_similarity(
    a: tuple[float, float, float] | None,
    b: tuple[float, float, float] | None,
) -> float | None:
    """1.0 - L1 distance between two mean RGB vectors, normalized.

    RGB values live in [0, 1]. The L1 distance `|dr|+|dg|+|db|`
    is at most 2.0 across all 3 channels (when one side is
    (0,0,0) and the other (1,1,1) — note green's full range
    is [0, 1] so the L1 ceiling is 2.0, not 3.0). We
    normalize by 2.0 and clip to [0, 1] for safety.

    Returns None only when BOTH sides are missing. When
    exactly one side is missing, we return 0.0 (a real
    mismatch, not a missing-modality case).
    """
    if a is None and b is None:
        return None
    if a is None or b is None:
        return 0.0
    l1 = abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
    return max(0.0, 1.0 - (l1 / 2.0))


def histogram_intersection(
    a: tuple[float, ...] | None,
    b: tuple[float, ...] | None,
) -> float | None:
    """Histogram intersection (Min-kernel) between two histograms.

    Both inputs are expected to be normalized to sum to 1.0;
    the result is in [0, 1] where 1.0 means identical
    distributions.

    Returns None only when BOTH sides are missing. When
    exactly one side is missing, we return 0.0 (a real
    mismatch).
    """
    if a is None and b is None:
        return None
    if a is None or b is None:
        return 0.0
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    return float(sum(min(x, y) for x, y in zip(a, b, strict=True)))


__all__ = [
    "build_fingerprints",
    "hamming_hex",
    "mean_rgb_similarity",
    "histogram_intersection",
    "_HIST_BINS_PER_CHANNEL",
    "_HIST_TOTAL_BINS",
]
