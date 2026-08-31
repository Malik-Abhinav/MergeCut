"""FFmpeg prerequisite check for MergeCut.

Used by `make smoke` and the FastAPI startup path. Returns a structured
report rather than raising so callers can decide how to surface failures.

Per PROJECT_PLAN §13.2 we normalize uploads with FFmpeg, so a working
ffmpeg + ffprobe on PATH is a hard prerequisite.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass


@dataclass
class FFmpegReport:
    ok: bool
    ffmpeg_path: str | None
    ffprobe_path: str | None
    ffmpeg_version: str | None
    ffprobe_version: str | None
    errors: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _version_of(path: str) -> str | None:
    try:
        out = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return (out.stdout or "").split("\n", 1)[0].strip() or None


def check_ffmpeg() -> FFmpegReport:
    """Return a structured FFmpeg readiness report."""
    errors: list[str] = []
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if not ffmpeg_path:
        errors.append("ffmpeg not found on PATH")
    if not ffprobe_path:
        errors.append("ffprobe not found on PATH")
    ffmpeg_version = _version_of(ffmpeg_path) if ffmpeg_path else None
    ffprobe_version = _version_of(ffprobe_path) if ffprobe_path else None
    if ffmpeg_path and not ffmpeg_version:
        errors.append(f"ffmpeg at {ffmpeg_path} failed to report version")
    if ffprobe_path and not ffprobe_version:
        errors.append(f"ffprobe at {ffprobe_path} failed to report version")
    return FFmpegReport(
        ok=not errors,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        ffmpeg_version=ffmpeg_version,
        ffprobe_version=ffprobe_version,
        errors=errors,
    )


def main() -> int:
    report = check_ffmpeg()
    import json

    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())