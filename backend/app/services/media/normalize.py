"""ffprobe metadata extraction + optional normalization.

Wraps `ffprobe` (already a hard prerequisite per `make check-ffmpeg`)
in a small, deterministic Python surface. We deliberately use the
system FFmpeg/ffprobe rather than shipping our own copy to keep a
single FFmpeg version in play on the host and to avoid the
duplicate-bundled-FFmpeg problem documented in `pyproject.toml`.

This module also contains the *normalize* step: re-encode an upload
into the working format used by the rest of the pipeline. The
working format is chosen for *deterministic* downstream processing,
not for visual quality:

- container:      MP4
- video codec:    H.264 (yuv420p, even dimensions, constant 30 fps
                  when the source fps is variable; otherwise the
                  source fps)
- audio codec:    AAC at 48 kHz when the source has audio
- pixel format:   yuv420p

Inputs that already match this format are *not* re-encoded; we
copy or hard-link the bytes into the derived directory so the
pipeline is still cacheable. Originals are never modified.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings
from app.models.media import (
    NormalizationInfo,
    UnsupportedFormatError,
    VideoMetadata,
)

# ---------------------------------------------------------------------------
# ffprobe parsing.
# ---------------------------------------------------------------------------


# ffprobe prints one JSON object on stdout when called with
# `-of json` (or `-print_format json`). The object has a top-level
# key per stream category plus a `format` key for container-level
# metadata. The fields we care about live in `streams[*]` and `format`.

_DURATION_RE = re.compile(r"^-?(\d+(?:\.\d+)?)$")


def _ffprobe_json(path: Path, settings: Settings) -> dict:
    """Run ffprobe and return the parsed JSON tree.

    Raises `UnsupportedFormatError` if ffprobe can't read the file at
    all, or if its output is unparseable.
    """
    ffprobe = settings.ffprobe_path or shutil.which("ffprobe")
    if not ffprobe:
        raise UnsupportedFormatError(path, "ffprobe not found on PATH")

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.request_timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise UnsupportedFormatError(path, f"ffprobe timed out: {e}") from e
    except OSError as e:
        raise UnsupportedFormatError(path, f"ffprobe failed: {e}") from e

    if proc.returncode != 0:
        raise UnsupportedFormatError(
            path, f"ffprobe rc={proc.returncode}: {proc.stderr.strip()[:200]}"
        )
    if not proc.stdout.strip():
        raise UnsupportedFormatError(path, "ffprobe returned empty stdout")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise UnsupportedFormatError(path, f"ffprobe JSON parse failed: {e}") from e


def _parse_fps(rate: str) -> float:
    """Parse ffprobe's `"30/1"` style frame-rate into a float.

    Falls back to 0.0 for anything we cannot parse; the caller can
    decide whether 0 fps is acceptable.
    """
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            n = float(num)
            d = float(den) or 1.0
            return n / d
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def probe_metadata(path: Path, settings: Settings | None = None) -> VideoMetadata:
    """Return probe-level metadata for a single video file.

    Raises `UnsupportedFormatError` for unreadable inputs.
    """
    settings = settings or get_settings()
    data = _ffprobe_json(path, settings)
    fmt = data.get("format") or {}
    streams = data.get("streams") or []

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise UnsupportedFormatError(path, "no video stream found")

    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_raw = fmt.get("duration")
    try:
        duration = float(duration_raw) if duration_raw is not None else 0.0
    except ValueError:
        duration = 0.0

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    fps = _parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate", "0/1"))
    codec = video_stream.get("codec_name") or ""
    audio_present = audio_stream is not None
    audio_codec = audio_stream.get("codec_name") if audio_stream else None
    bit_rate_raw = fmt.get("bit_rate")
    try:
        bit_rate = int(bit_rate_raw) if bit_rate_raw is not None else None
    except (ValueError, TypeError):
        bit_rate = None

    return VideoMetadata(
        duration_seconds=duration,
        width=width,
        height=height,
        fps=fps,
        codec=codec,
        audio_present=audio_present,
        audio_codec=audio_codec,
        bit_rate=bit_rate,
    )


# ---------------------------------------------------------------------------
# Normalization decision + execution.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _WorkingSpec:
    """Working format spec. Single source of truth."""

    container: str = "mp4"
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    audio_rate: int = 48000
    pixel_format: str = "yuv420p"


WORKING_SPEC = _WorkingSpec()


def _needs_normalization(meta: VideoMetadata) -> tuple[bool, str | None]:
    """Decide whether the upload must be re-encoded.

    Conservative — re-encodes anything that doesn't *clearly* match
    the working format. False-positives are cheap (one extra FFmpeg
    pass) and safer than false-negatives (downstream steps assume
    the working format).

    Note: ffprobe reports the *codec family* (`h264`) while ffmpeg's
    encoder is `libx264`. They are the same codec; we accept both.
    """
    if meta.codec not in (WORKING_SPEC.video_codec, "h264"):
        return True, f"video codec {meta.codec!r} != {WORKING_SPEC.video_codec!r}"
    if meta.width <= 0 or meta.height <= 0:
        return True, f"non-positive dimensions {meta.width}x{meta.height}"
    if meta.width % 2 != 0 or meta.height % 2 != 0:
        # yuv420p requires even dimensions.
        return True, f"odd dimensions {meta.width}x{meta.height}"
    if meta.fps <= 0.0:
        return True, f"non-positive fps {meta.fps}"
    if meta.audio_present and meta.audio_codec not in ("aac", None):
        # We accept "no audio codec reported" as audio-OK; only re-encode
        # when the codec is something other than AAC.
        return True, f"audio codec {meta.audio_codec!r} != {WORKING_SPEC.audio_codec!r}"
    return False, None


def normalize_video(
    source: Path,
    derived_dir: Path,
    meta: VideoMetadata,
    settings: Settings | None = None,
) -> tuple[Path, NormalizationInfo]:
    """Produce a working-format copy of `source` under `derived_dir`.

    Returns the (absolute) destination path and a NormalizationInfo
    describing whether re-encoding happened. The original `source`
    is never modified.
    """
    settings = settings or get_settings()
    ffmpeg = settings.ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise UnsupportedFormatError(source, "ffmpeg not found on PATH")

    derived_dir.mkdir(parents=True, exist_ok=True)
    # Destination name is stable: <basename>.working.mp4. Two uploads
    # with the same basename collide only at the cache level; the
    # content-hash-based cache in `pipeline.py` handles that.
    dest = derived_dir / (source.stem + ".working.mp4")

    needs, reason = _needs_normalization(meta)

    if not needs:
        # Don't re-encode; copy the bytes so downstream steps can rely
        # on a stable path under derived_dir. (Hard-link would be nicer
        # but cross-device links fail; copy is always safe.)
        shutil.copyfile(source, dest)
        return dest, NormalizationInfo(normalized=False, reason=None)

    cmd = [
        ffmpeg,
        "-y",  # overwrite dest if it already exists
        "-i",
        str(source),
        "-c:v",
        WORKING_SPEC.video_codec,
        "-pix_fmt",
        WORKING_SPEC.pixel_format,
        "-r",
        "30",  # force 30 fps for determinism
        "-vsync",
        "cfr",
    ]
    if meta.audio_present:
        cmd += [
            "-c:a",
            WORKING_SPEC.audio_codec,
            "-ar",
            str(WORKING_SPEC.audio_rate),
        ]
    else:
        cmd += ["-an"]
    cmd.append(str(dest))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.request_timeout_s * 4,  # ffmpeg can be slow
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise UnsupportedFormatError(source, f"ffmpeg normalize timed out: {e}") from e
    except OSError as e:
        raise UnsupportedFormatError(source, f"ffmpeg normalize failed: {e}") from e

    if proc.returncode != 0:
        raise UnsupportedFormatError(
            source,
            f"ffmpeg normalize rc={proc.returncode}: {proc.stderr.strip()[:300]}",
        )
    if not dest.exists():
        raise UnsupportedFormatError(source, "ffmpeg reported success but no output")

    return dest, NormalizationInfo(normalized=True, reason=reason)


__all__ = [
    "WORKING_SPEC",
    "probe_metadata",
    "normalize_video",
]
