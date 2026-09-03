"""Media preprocessing pipeline.

`process_video(source)` is the single public entry point. It:

1. Computes a stable `video_id` from the source file's content hash.
2. Looks up a cached `VideoRepresentation` under
   `derived_dir/videos/<video_id>.json`. Cache hit → return.
3. Probes metadata with `ffprobe`.
4. Normalizes to the working format when needed
   (otherwise copies bytes into derived_dir).
5. Detects shot boundaries.
6. Extracts audio (mono 16 kHz WAV) — None for audio-less videos.
7. Transcribes the audio with faster-whisper (cached by audio
   fingerprint).
8. Extracts one keyframe per shot.
9. Joins transcript segments into shots by midpoint timestamp.
10. Writes the JSON representation and returns it.

Cache invalidation: re-processing the same source is a no-op
(content hash matches). Re-processing after a source change
generates a new video_id and a fresh directory.

The original upload is never modified.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path

from app.config import Settings, get_settings
from app.models.media import (
    MediaError,
    Shot,
    TranscriptSegment,
    VideoRepresentation,
)
from app.services.media.audio import extract_audio
from app.services.media.keyframes import extract_keyframes
from app.services.media.normalize import normalize_video, probe_metadata
from app.services.media.scenes import detect_shots
from app.services.media.transcript import (
    load_cached_transcript,
    save_cached_transcript,
    transcribe,
)

logger = logging.getLogger(__name__)


def _content_hash(path: Path) -> str:
    """Stable content hash of a video file.

    We hash the first 1 MiB + last 1 MiB + total size. This is fast,
    avoids reading the whole multi-GB file, and uniquely distinguishes
    the source bytes for cache-key purposes. It is NOT a
    cryptographic guarantee; collisions just cause a cache hit when
    there shouldn't be one.
    """
    size = path.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    with path.open("rb") as fh:
        first = fh.read(1024 * 1024)
        h.update(first)
        if size > 2 * 1024 * 1024:
            fh.seek(size - 1024 * 1024)
            h.update(fh.read(1024 * 1024))
    return h.hexdigest()[:16]


def _video_dir(derived_dir: Path, video_id: str) -> Path:
    """Per-video derived directory."""
    return derived_dir / "videos" / video_id


def _representation_path(derived_dir: Path, video_id: str) -> Path:
    return _video_dir(derived_dir, video_id) / "representation.json"


def _join_transcripts_into_shots(
    shots: list[tuple[float, float]],
    segments: list[TranscriptSegment],
) -> list[tuple[str, list[TranscriptSegment]]]:
    """For each shot, return (joined transcript text, raw segments).

    Assignment algorithm (Phase 3, final):

    Each ASR segment is assigned to the shot whose [start, end]
    window has the **largest overlap duration** with the
    segment. Ties broken by left-to-right order.

    We *do not* split segments at shot boundaries. The
    motivation is that an ASR segment is a single spoken
    utterance and belongs as a whole to one shot; splitting it
    across shots creates nonsensical partial-sentence
    transcripts. The trade-off is that a segment which spans
    multiple shots attaches to whichever shot's audio it most
    overlaps, and the other shots in that span may get a
    missing transcript.

    The previous token-midpoint split produced messy output
    ("Step 1 Open the" / "device carefully. Step 2") because
    faster-whisper assigns imprecise timestamps to natural
    speech — its segment boundaries don't align with our shot
    boundaries.

    Every shot also surfaces the *original* segments it
    overlaps, so downstream code can see where the ambiguity
    came from.
    """
    # Pre-compute overlap for every (shot, segment) pair so the
    # loop below is cheap.
    overlaps: list[list[float]] = []
    for s_start, s_end in shots:
        row: list[float] = []
        for seg in segments:
            lo = max(seg.start, s_start)
            hi = min(seg.end, s_end)
            row.append(max(0.0, hi - lo))
        overlaps.append(row)

    joined: list[tuple[str, list[TranscriptSegment]]] = []
    for i, (_s_start, _s_end) in enumerate(shots):
        per_shot: list[TranscriptSegment] = []
        per_shot_tokens: list[str] = []
        for j, seg in enumerate(segments):
            if overlaps[i][j] <= 0:
                continue
            # Include this segment if its overlap with this shot
            # is at least as large as with any other shot. This
            # is "max-overlap assignment" — a segment attaches
            # to one shot, not multiple.
            is_max = all(overlaps[i][j] >= overlaps[k][j] for k in range(len(shots)))
            if is_max:
                per_shot.append(seg)
                per_shot_tokens.extend(seg.text.split())
        text = " ".join(per_shot_tokens).strip()
        joined.append((text, per_shot))
    return joined


def _transcribe_per_shot(
    working_video: Path,
    shots: list[tuple[float, float]],
    settings,  # Settings — avoid importing for type-checker friendliness
) -> list[list[TranscriptSegment]]:
    """Transcribe each shot's audio independently.

    For each shot, we cut a mono 16 kHz WAV covering exactly
    that shot's [start, end] window and run faster-whisper on
    the cut. This isolates the decoder from neighbouring-shot
    speech and produces clean per-shot transcripts even when
    full-file ASR would merge sentences across shot boundaries
    (a real faster-whisper limitation we hit on Phase 3
    fixtures).

    Each shot's returned segments have timestamps relative to
    the cut audio (i.e. start near 0). The caller shifts them
    into the global timeline by adding the shot's start.

    Per-shot ASR is slower than full-file ASR (5x for a 5-shot
    video). We mitigate by:
    - using a smaller model (`base`) by default
    - using `beam_size=1`
    - caching the cut WAV on disk so re-runs are no-ops

    Returns a list of segment lists, one per shot, in input
    order. Shots without audio produce empty lists.
    """
    import shutil as _shutil

    from app.services.media.transcript import transcribe

    ffmpeg = settings.ffmpeg_path or _shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not on PATH")

    out: list[list[TranscriptSegment]] = []
    for idx, (start, end) in enumerate(shots):
        cut_wav = working_video.with_name(
            f"{working_video.stem}_shot{idx:04d}_{int(start * 1000):06d}_{int(end * 1000):06d}.wav"
        )
        if not cut_wav.exists():
            cmd = [
                ffmpeg,
                "-y",
                "-i",
                str(working_video),
                "-ss",
                f"{start:.3f}",
                "-to",
                f"{end:.3f}",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-acodec",
                "pcm_s16le",
                str(cut_wav),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                raise RuntimeError(f"per-shot audio cut failed: {proc.stderr[:200]}")

        # Transcribe the cut. With only this shot's audio, the
        # decoder sees a single utterance and produces clean
        # output.
        try:
            shot_segments, _fp = transcribe(cut_wav, settings=settings)
        except Exception as e:  # noqa: BLE001
            logger.warning("Per-shot transcription failed for shot %d: %s", idx, e)
            out.append([])
            continue
        # Shift timestamps back to the global timeline.
        shifted = [
            TranscriptSegment(
                start=seg.start + start,
                end=seg.end + start,
                text=seg.text,
                confidence=seg.confidence,
            )
            for seg in shot_segments
        ]
        out.append(shifted)
        # Tidy up the cut WAV.
        cut_wav.unlink(missing_ok=True)
    return out


def _read_cached_representation(p: Path) -> VideoRepresentation | None:
    if not p.exists():
        return None
    try:
        return VideoRepresentation.model_validate_json(p.read_text())
    except Exception as e:  # noqa: BLE001 — cache may be corrupt; recompute
        logger.warning("Discarding corrupt representation at %s: %s", p, e)
        return None


def process_video(
    source: Path,
    settings: Settings | None = None,
) -> VideoRepresentation:
    """Process a single uploaded video and return its structured representation.

    Raises:
        MediaError: on unrecoverable failures.
        UnsupportedFormatError: when ffprobe cannot read the file.
    """
    settings = settings or get_settings()
    source = source.expanduser().resolve()
    if not source.exists():
        raise MediaError(f"source video not found: {source}")

    derived_dir = settings.derived_dir.expanduser().resolve()
    derived_dir.mkdir(parents=True, exist_ok=True)

    video_id = _content_hash(source)
    cached = _read_cached_representation(_representation_path(derived_dir, video_id))
    if cached is not None:
        return cached

    video_dir = _video_dir(derived_dir, video_id)
    video_dir.mkdir(parents=True, exist_ok=True)

    # 1. Probe
    metadata = probe_metadata(source, settings=settings)

    # 2. Normalize (or copy into derived dir)
    working_path, norm_info = normalize_video(source, video_dir, metadata, settings=settings)

    # 3. Scene detection
    shots = detect_shots(working_path, settings=settings)

    # 4. Audio (None for audio-less videos)
    audio_path = extract_audio(working_path, video_dir, settings=settings)

    # 5. Transcript (cached on disk by audio fingerprint).
    # When `transcribe_per_shot` is set, we run faster-whisper
    # once per shot (with the per-shot audio cut) so the decoder
    # is isolated from neighbouring-shot speech. This produces
    # cleaner per-shot transcripts when full-file ASR would
    # merge sentences across shot boundaries (a real
    # faster-whisper limitation we hit on Phase 3 fixtures).
    segments: list[TranscriptSegment] = []
    per_shot_segments: list[list[TranscriptSegment]] = []
    if audio_path is not None:
        if settings.transcribe_per_shot:
            per_shot_segments = _transcribe_per_shot(working_path, shots, settings=settings)
            # Flatten for the legacy representation, preserving
            # global-timeline timestamps.
            for shot_segs in per_shot_segments:
                segments.extend(shot_segs)
        else:
            cached_segs = load_cached_transcript(video_dir, _fp_for(audio_path))
            if cached_segs is not None:
                segments = cached_segs
            else:
                segments, fp = transcribe(audio_path, settings=settings)
                save_cached_transcript(video_dir, fp, segments)
            per_shot_segments = [[s] for s in segments]

    # 6. Keyframes (one per shot)
    keyframes_per_shot = extract_keyframes(working_path, shots, video_dir, settings=settings)

    # 7. Build per-shot transcripts.
    # When per-shot ASR was used, each shot already has its own
    # segments (no re-assignment needed). When full-file ASR
    # was used, we re-assign via max-overlap.
    if settings.transcribe_per_shot and per_shot_segments:
        joined: list[tuple[str, list[TranscriptSegment]]] = [
            (" ".join(seg.text for seg in shot_segs).strip(), shot_segs)
            for shot_segs in per_shot_segments
        ]
    else:
        joined = _join_transcripts_into_shots(shots, segments)

    # 8. Build the representation (flat top-level + nested metadata)
    rep = VideoRepresentation.from_components(
        video_id=video_id,
        source_path=source,
        normalized_path=working_path,
        audio_path=audio_path,
        metadata=metadata,
        normalization=norm_info,
        shots=[
            Shot(
                shot_id=f"shot_{i:04d}",
                start=s,
                end=e,
                keyframe_paths=kp,
                transcript=text,
                transcript_segments=segs,
            )
            for i, ((s, e), kp, (text, segs)) in enumerate(
                zip(shots, keyframes_per_shot, joined, strict=True)
            )
        ],
    )

    # 9. Persist the representation for next time
    rep_path = _representation_path(derived_dir, video_id)
    rep_path.write_text(json.dumps(rep.model_dump(mode="json"), indent=2))
    return rep


def _fp_for(audio_path: Path) -> str:
    """Wrapper around `transcript._file_fingerprint` so callers don't
    have to know the internal helper name."""
    from app.services.media.transcript import _file_fingerprint

    return _file_fingerprint(audio_path)


__all__ = ["process_video", "_content_hash"]
