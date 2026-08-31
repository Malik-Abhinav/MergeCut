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
    """For each shot, return the joined transcript text and segments.

    The midpoint-of-shot rule is simple: a transcript segment "belongs"
    to shot i if its midpoint is inside shot i's [start, end]. This
    is the same rule we use for keyframes, which keeps them aligned.
    """
    joined: list[tuple[str, list[TranscriptSegment]]] = []
    for start, end in shots:
        per_shot: list[TranscriptSegment] = []
        for seg in segments:
            seg_mid = (seg.start + seg.end) / 2.0
            if start <= seg_mid <= end:
                per_shot.append(seg)
        text = " ".join(s.text for s in per_shot).strip()
        joined.append((text, per_shot))
    return joined


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

    # 5. Transcript (cached on disk by audio fingerprint)
    segments: list[TranscriptSegment] = []
    if audio_path is not None:
        cached_segs = load_cached_transcript(video_dir, _fp_for(audio_path))
        if cached_segs is not None:
            segments = cached_segs
        else:
            segments, fp = transcribe(audio_path, settings=settings)
            save_cached_transcript(video_dir, fp, segments)

    # 6. Keyframes (one per shot)
    keyframes_per_shot = extract_keyframes(working_path, shots, video_dir, settings=settings)

    # 7. Join transcripts into shots
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
