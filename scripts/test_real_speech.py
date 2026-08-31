"""Phase 2.5 real-speech validation.

Calls the existing `process_video()` pipeline on a single user-
supplied video path and prints:

- video duration
- detected shots with start / end timestamps
- transcript segments with timestamps
- transcript text assigned to each shot

The purpose is to validate the Phase 2 pipeline against a real
recording (with real speech) before Phase 3 begins. The script
makes no changes to the pipeline itself unless it exposes a real
bug, in which case the user will see the error and decide what to
do.

Usage:

    uv run --project backend python scripts/test_real_speech.py <path-to-video>

The pipeline will write derived artifacts under
`data/derived/videos/<video_id>/` (per PROJECT_PLAN §13.2 / Phase 2
implementation) and may take a few minutes on first run because it
downloads the faster-whisper model weights.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `app` importable when this script is run directly. Same
# convention as scripts/run_spike.py.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.media import MediaError, UnsupportedFormatError  # noqa: E402
from app.services.media.pipeline import process_video  # noqa: E402


def _format_seconds(s: float) -> str:
    """Render a float number of seconds as `MM:SS.mmm`."""
    if s < 0:
        s = 0.0
    minutes = int(s // 60)
    seconds = s - 60 * minutes
    return f"{minutes:02d}:{seconds:06.3f}"


def _print_header(title: str) -> None:
    bar = "=" * len(title)
    print(f"\n{title}\n{bar}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 2.5 real-speech validation: runs the Phase 2 "
            "pipeline on a single video and prints shots + transcript."
        )
    )
    parser.add_argument(
        "video",
        type=Path,
        help="Path to a video file (MP4/H.264 preferred).",
    )
    args = parser.parse_args(argv)

    if not args.video.exists():
        print(f"ERROR: video not found: {args.video}", file=sys.stderr)
        return 2

    try:
        rep = process_video(args.video)
    except UnsupportedFormatError as e:
        print(f"ERROR: unsupported format — {e}", file=sys.stderr)
        return 3
    except MediaError as e:
        print(f"ERROR: pipeline failed — {e}", file=sys.stderr)
        return 4

    meta = rep.metadata

    _print_header("video")
    print(f"path               {args.video}")
    print(f"video_id           {rep.video_id}")
    print(
        f"duration           {_format_seconds(meta.duration_seconds)} ({meta.duration_seconds:.2f}s)"
    )
    print(f"dimensions         {meta.width}x{meta.height}")
    print(f"fps                {meta.fps:.3f}")
    print(f"video codec        {meta.codec}")
    print(
        "audio              "
        + ("yes" if meta.audio_present else "no")
        + (f" ({meta.audio_codec})" if meta.audio_codec else "")
    )
    print(
        "normalization      "
        + ("re-encoded" if rep.normalization.normalized else "copy")
        + (f" — {rep.normalization.reason}" if rep.normalization.reason else "")
    )
    print(f"working file       {rep.normalized_path}")
    print(f"audio file         {rep.audio_path if rep.audio_path else '(none)'}")

    _print_header(f"shots ({len(rep.shots)})")
    if not rep.shots:
        print("(no shots detected)")
    for s in rep.shots:
        start = _format_seconds(s.start)
        end = _format_seconds(s.end)
        dur = s.end - s.start
        transcript_one_line = s.transcript.replace("\n", " ").strip()
        if len(transcript_one_line) > 80:
            transcript_one_line = transcript_one_line[:77] + "..."
        print(
            f"  {s.shot_id}  {start} – {end}  ({dur:5.2f}s)"
            f"  keyframes={len(s.keyframe_paths)}"
            f"  segments={len(s.transcript_segments)}"
        )
        if transcript_one_line:
            print(f"      text: {transcript_one_line}")
        else:
            print(f"      text: (none)")

    total_segments = sum(len(s.transcript_segments) for s in rep.shots)
    _print_header(f"transcript segments ({total_segments})")
    if total_segments == 0:
        print("(no transcript segments — no audio track, or ASR produced no text)")
    for s in rep.shots:
        if not s.transcript_segments:
            continue
        print(f"  -- {s.shot_id} ({_format_seconds(s.start)} – {_format_seconds(s.end)}) --")
        for seg in s.transcript_segments:
            start = _format_seconds(seg.start)
            end = _format_seconds(seg.end)
            conf = f"{seg.confidence:.2f}" if seg.confidence is not None else "  n/a"
            text = seg.text.replace("\n", " ").strip()
            print(f"  {start} – {end}  conf={conf}  {text}")

    _print_header("summary")
    print(f"shots detected     {len(rep.shots)}")
    print(f"transcript segments {total_segments}")
    if rep.shots:
        total_chars = sum(len(s.transcript) for s in rep.shots)
        print(f"transcript chars   {total_chars}")
        for s in rep.shots:
            chars = len(s.transcript)
            dur = max(0.0, s.end - s.start)
            density = chars / dur if dur > 0 else 0.0
            print(f"  {s.shot_id}  {chars:5d} chars  {density:6.1f} chars/sec")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
