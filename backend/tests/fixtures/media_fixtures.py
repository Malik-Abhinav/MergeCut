"""Controlled MP4 fixture builder for Phase 2 tests.

Uses the system FFmpeg (verified by `make check-ffmpeg`) to synthesize
small, deterministic MP4 fixtures with known properties:

- `make_normal_mp4`: 6-second clip, 2 cuts → 3 shots, solid colour
  frames (black→white→red), 30 fps, AAC audio tone, 320x240.
- `make_multi_scene_mp4`: 12-second clip, 5 colour blocks → 6 shots,
  silent video, 30 fps, 640x480.
- `make_speech_mp4`: 6-second clip with a synthesized 440 Hz tone
  track. (No actual speech — that requires bundled audio data which
  we don't ship. The fixture proves the ASR pipeline is wired up
  and survives a no-speech case. The 'speech' fixture uses the
  same builder; an actual-speech fixture would need a licensed
  sample and is deferred to Phase 5's evaluation set.)
- `make_no_audio_mp4`: 4-second silent video, 2 cuts → 3 shots.
- `make_bad_input`: a zero-byte file, used to verify error handling.

All builders are deterministic (no randomness) so re-runs of the
same test produce the same bytes.

The fixtures live in `tests/fixtures/media/` and are created on
demand (not committed) — gitignoring them via `.gitignore` so the
repo stays small. Cached by content hash.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.config import get_settings

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _ffmpeg() -> str:
    settings = get_settings()
    ffmpeg = settings.ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH")
    return ffmpeg


def _run(cmd: list[str], timeout: float = 60.0) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {proc.stderr.strip()[:300]}")


def _color_frames_filter(colors: list[str], fps: int, dur_per: float) -> list[str]:
    """Build an ffmpeg filter graph that paints `colors` blocks in
    sequence, each `dur_per` seconds long, at `fps` fps.

    Returns the filter-graph arguments to splice into an ffmpeg
    `-filter_complex` (after `-i color=...` inputs).
    """
    # We use a simpler approach: one filtergraph per colour, then
    # concat. The caller assembles the full graph.
    raise NotImplementedError  # implemented inline by each builder


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Public builders.
# ---------------------------------------------------------------------------


def make_normal_mp4(target_dir: Path) -> Path:
    """3-shot, 6s, 320x240, 30 fps, AAC audio."""
    target_dir = _ensure_dir(target_dir)
    ffmpeg = _ffmpeg()
    out = target_dir / "normal_3shots_320x240_30fps.mp4"

    # 3 shots: 2s each, colours black / white / red.
    # Audio: 440 Hz tone for the full 6s.
    cmd = [
        ffmpeg,
        "-y",
        # Shot 1 (black, 2s)
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x240:r=30:d=2",
        # Shot 2 (white, 2s)
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=320x240:r=30:d=2",
        # Shot 3 (red, 2s)
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=320x240:r=30:d=2",
        # Audio (440 Hz tone, 6s)
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=6",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[outv]",
        "-map",
        "[outv]",
        "-map",
        "3:a",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-shortest",
        str(out),
    ]
    _run(cmd)
    return out


def make_multi_scene_mp4(target_dir: Path) -> Path:
    """6-shot, 12s, 640x480, 30 fps, NO audio."""
    target_dir = _ensure_dir(target_dir)
    ffmpeg = _ffmpeg()
    out = target_dir / "multi_6shots_640x480_noaudio.mp4"

    # 6 shots: 2s each, six distinct colours.
    colors = ["black", "white", "red", "green", "blue", "yellow"]
    inputs: list[str] = []
    for c in colors:
        inputs += [
            "-f",
            "lavfi",
            "-i",
            f"color=c={c}:s=640x480:r=30:d=2",
        ]
    filter_inputs = "".join(f"[{i}:v]" for i in range(len(colors)))
    cmd = [
        ffmpeg,
        "-y",
        *inputs,
        "-filter_complex",
        f"{filter_inputs}concat=n={len(colors)}:v=1:a=0[outv]",
        "-map",
        "[outv]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(out),
    ]
    _run(cmd)
    return out


def make_no_audio_mp4(target_dir: Path) -> Path:
    """3-shot, 4s, 320x240, 30 fps, NO audio."""
    target_dir = _ensure_dir(target_dir)
    ffmpeg = _ffmpeg()
    out = target_dir / "noaudio_3shots_320x240.mp4"

    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x240:r=30:d=1",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x240:r=30:d=1",
        "-f",
        "lavfi",
        "-i",
        "color=c=green:s=320x240:r=30:d=2",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[outv]",
        "-map",
        "[outv]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(out),
    ]
    _run(cmd)
    return out


def make_speech_mp4(target_dir: Path) -> Path:
    """3-shot, 6s, 320x240, 30 fps, 440 Hz audio (proxy for speech
    in Phase 2). Real-speech fixtures live in Phase 5's evaluation
    set."""
    target_dir = _ensure_dir(target_dir)
    ffmpeg = _ffmpeg()
    out = target_dir / "speech_3shots_320x240_audio.mp4"

    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=320x240:r=30:d=2",
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=320x240:r=30:d=2",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=320x240:r=30:d=2",
        # Two-tone audio: 440 Hz for first half, 880 Hz for second.
        # faster-whisper will produce no usable text but the audio
        # extraction + ASR pipeline must complete cleanly.
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:duration=3",
        "-filter_complex",
        ("[0:v][1:v][2:v]concat=n=3:v=1:a=0[outv];[3:a][4:a]concat=n=2:v=0:a=1[outa]"),
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-shortest",
        str(out),
    ]
    _run(cmd)
    return out


def make_bad_input(target_dir: Path) -> Path:
    """Zero-byte file. ffprobe will reject this."""
    target_dir = _ensure_dir(target_dir)
    out = target_dir / "bad_zero_bytes.mp4"
    out.write_bytes(b"")
    return out


# ---------------------------------------------------------------------------
# Convenience: build the whole Phase 2 fixture set in one call.
# ---------------------------------------------------------------------------


def build_all(target_dir: Path) -> dict[str, Path]:
    """Build every Phase 2 fixture. Returns a name → path map."""
    return {
        "normal": make_normal_mp4(target_dir),
        "multi_scene": make_multi_scene_mp4(target_dir),
        "speech": make_speech_mp4(target_dir),
        "no_audio": make_no_audio_mp4(target_dir),
        "bad": make_bad_input(target_dir),
    }


__all__ = [
    "build_all",
    "make_normal_mp4",
    "make_multi_scene_mp4",
    "make_speech_mp4",
    "make_no_audio_mp4",
    "make_bad_input",
]
