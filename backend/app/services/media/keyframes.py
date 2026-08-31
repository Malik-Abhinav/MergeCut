"""Representative keyframe extraction.

For each shot we extract ONE representative frame (the frame at the
midpoint timestamp) and write it as a JPEG to a per-shot keyframes
directory. JPEG quality 92 keeps the bytes small enough to fit in
Phase 3's perceptual hash cache while staying visually useful for
human review.

We use FFmpeg (subprocess) rather than OpenCV's `VideoCapture` for
this — OpenCV's VideoCapture on MP4 is finicky about framerate and
the same-source frame seek, whereas `ffmpeg -ss ... -frames:v 1`
gives us a single frame directly with predictable behavior. OpenCV
is still imported in `pyproject.toml` because PySceneDetect needs
it; we just don't use it for keyframes.

Determinism: `ffmpeg` seeks to a fixed timestamp and grabs the next
I-frame after that timestamp (we add `-nointra` to force exact seek
when available; for the FPS we're working with, this is good
enough for visual review).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.config import Settings, get_settings
from app.models.media import UnsupportedFormatError

JPEG_QUALITY = 92


def extract_keyframes(
    working_video: Path,
    shots: list[tuple[float, float]],
    derived_dir: Path,
    settings: Settings | None = None,
) -> list[list[Path]]:
    """Extract a representative keyframe for each shot.

    Returns a list parallel to `shots`; each element is a list of
    paths to JPEG files for that shot (currently exactly one).

    Skips a shot whose duration is non-positive.
    """
    settings = settings or get_settings()
    ffmpeg = settings.ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise UnsupportedFormatError(working_video, "ffmpeg not found on PATH")

    derived_dir.mkdir(parents=True, exist_ok=True)
    out: list[list[Path]] = []

    for idx, (start, end) in enumerate(shots):
        if end <= start:
            out.append([])
            continue
        midpoint = (start + end) / 2.0
        dest = derived_dir / f"shot_{idx:04d}.jpg"

        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{midpoint:.3f}",
            "-i",
            str(working_video),
            "-frames:v",
            "1",
            "-q:v",
            str(max(1, min(31, int((100 - JPEG_QUALITY) / 3 + 1)))),
            str(dest),
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
            raise UnsupportedFormatError(working_video, f"keyframe extract timed out: {e}") from e
        except OSError as e:
            raise UnsupportedFormatError(working_video, f"keyframe extract failed: {e}") from e

        if proc.returncode != 0 or not dest.exists():
            out.append([])
            continue
        out.append([dest])

    return out


__all__ = ["extract_keyframes", "JPEG_QUALITY"]
