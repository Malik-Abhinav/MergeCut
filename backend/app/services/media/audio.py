"""Audio extraction.

Pulls a mono 16 kHz WAV from the working MP4 using the system FFmpeg.
The output is the input to the faster-whisper ASR pass, which
expects mono 16 kHz float audio. We deliberately keep this simple
and use FFmpeg's resampler rather than faster-whisper's internal
ones — that gives one fewer moving part and a stable, deterministic
file format on disk.

Videos without an audio track return None from `extract_audio`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.config import Settings, get_settings
from app.models.media import UnsupportedFormatError


def extract_audio(
    working_video: Path,
    derived_dir: Path,
    settings: Settings | None = None,
) -> Path | None:
    """Extract a mono 16 kHz WAV from `working_video`.

    Returns the absolute path to the WAV file, or None when the
    source has no audio track. Raises `UnsupportedFormatError` on
    ffmpeg failures.
    """
    settings = settings or get_settings()
    ffmpeg = settings.ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise UnsupportedFormatError(working_video, "ffmpeg not found on PATH")

    derived_dir.mkdir(parents=True, exist_ok=True)
    dest = derived_dir / (working_video.stem + ".mono16k.wav")

    # `-vn` would still emit an empty WAV for silent videos. We probe
    # for audio first via `normalize.probe_metadata` — but to keep
    # this module dependency-free we use ffmpeg's stderr sniff: a
    # successful `-vn` extraction of an audio-less video still
    # produces an empty / very-short output, which we detect.
    #
    # Simpler: try the extract; if ffmpeg reports "Output file does
    # not contain any stream", treat as no audio and return None.
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(working_video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-acodec",
        "pcm_s16le",
        "-f",
        "wav",
        str(dest),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.request_timeout_s * 2,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise UnsupportedFormatError(working_video, f"audio extract timed out: {e}") from e
    except OSError as e:
        raise UnsupportedFormatError(working_video, f"audio extract failed: {e}") from e

    if proc.returncode != 0:
        # Most common case for an audio-less video: ffmpeg says
        # "Output file does not contain any stream" with rc 1.
        combined = (proc.stderr or "") + (proc.stdout or "")
        if "does not contain any stream" in combined or "no audio" in combined.lower():
            if dest.exists():
                dest.unlink(missing_ok=True)
            return None
        raise UnsupportedFormatError(
            working_video,
            f"audio extract rc={proc.returncode}: {combined[:300]}",
        )

    if not dest.exists():
        return None

    # Sanity: a 16 kHz mono 16-bit WAV that is essentially empty
    # (< 0.1 s) probably means the source had no real audio.
    if dest.stat().st_size < 4000:
        dest.unlink(missing_ok=True)
        return None

    return dest


__all__ = ["extract_audio"]
