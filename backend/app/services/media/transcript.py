"""Timestamped transcript generation.

Wraps `faster-whisper` to produce a list of `TranscriptSegment`
(start, end, text, confidence) from a mono 16 kHz WAV.

Determinism: faster-whisper is deterministic given a fixed model
and `beam_size`. We use the smallest reasonable `beam_size=1`
(greedy) for reproducibility.

The first call to `transcribe` on a fresh machine will download
the chosen model (default `base`, ~150 MB). Subsequent calls reuse
the cached model. We expose `model_name` so tests can pin it.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from faster_whisper import WhisperModel

from app.config import Settings, get_settings
from app.models.media import TranscriptSegment

# Module-level cache: one model handle per (model_name, device,
# compute_type) tuple. WhisperModel() loads weights into memory and
# we don't want to pay that cost per pipeline call.
_MODEL_CACHE: dict[tuple[str, str, str], WhisperModel] = {}
_MODEL_LOCK = threading.Lock()


def _get_model(model_name: str, device: str, compute_type: str) -> WhisperModel:
    key = (model_name, device, compute_type)
    with _MODEL_LOCK:
        m = _MODEL_CACHE.get(key)
        if m is not None:
            return m
        m = WhisperModel(model_name, device=device, compute_type=compute_type)
        _MODEL_CACHE[key] = m
        return m


def clear_model_cache() -> None:
    """Drop all cached model handles. Used by tests."""
    with _MODEL_LOCK:
        _MODEL_CACHE.clear()


def _file_fingerprint(path: Path) -> str:
    """Stable short hash of the audio file used for cache-busting.

    faster-whisper can't be told 'this audio has not changed, skip
    re-transcription' directly, but the pipeline layer can hash the
    WAV and reuse the cached transcript JSON.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def transcribe(
    audio_path: Path,
    settings: Settings | None = None,
) -> tuple[list[TranscriptSegment], str]:
    """Transcribe `audio_path` (mono 16 kHz WAV) into timestamped segments.

    Returns (segments, fingerprint) where fingerprint is a stable
    short hash of the audio file. The pipeline uses the fingerprint
    to cache the transcript on disk.
    """
    settings = settings or get_settings()
    model = _get_model(
        settings.whisper_model,
        settings.whisper_device,
        settings.whisper_compute_type,
    )

    # beam_size=1 (greedy) for reproducibility; word_timestamps=True
    # so we get fine-grained timestamps the pipeline can join into
    # shots later. We disable `vad_filter` because, on Phase 3's
    # controlled real-speech fixtures, VAD over-segments short
    # inter-shot silence and faster-whisper's natural decoder
    # produces cleaner per-sentence segments without VAD. For
    # general real-world audio (longer clips, more silence,
    # background noise) we'd want VAD on; for the Phase 3
    # acceptance gate it's an unnecessary source of variance.
    #
    # `chunk_length` is exposed as a setting so per-shot
    # transcription can be tuned without changing the model.
    segments_iter, _info = model.transcribe(
        str(audio_path),
        beam_size=1,
        word_timestamps=True,
        vad_filter=False,
        chunk_length=settings.whisper_chunk_length,
        language="en",
    )

    out: list[TranscriptSegment] = []
    for seg in segments_iter:
        # `seg.words` may be empty when vad_filter suppresses
        # everything. We still want to record the segment for
        # completeness.
        text = (seg.text or "").strip()
        if not text:
            continue
        if seg.words:
            confidences = [
                float(getattr(w, "probability", 0.0))
                for w in seg.words
                if getattr(w, "probability", None) is not None
            ]
            confidence = (sum(confidences) / len(confidences)) if confidences else None
        else:
            confidence = float(getattr(seg, "avg_logprob", 0.0)) or None
            # avg_logprob is in [-inf, 0]; not a 0..1 probability.
            # Surface as None so downstream knows it's not comparable
            # to word-level confidence.
            confidence = None
        out.append(
            TranscriptSegment(
                start=float(seg.start),
                end=float(seg.end),
                text=text,
                confidence=confidence,
            )
        )

    return out, _file_fingerprint(audio_path)


def transcript_cache_path(
    derived_dir: Path,
    fingerprint: str,
) -> Path:
    """Where the pipeline should cache transcripts for `fingerprint`."""
    return derived_dir / f"transcript_{fingerprint}.json"


def load_cached_transcript(
    derived_dir: Path,
    fingerprint: str,
) -> list[TranscriptSegment] | None:
    """Read a previously-cached transcript, if any."""
    p = transcript_cache_path(derived_dir, fingerprint)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    out: list[TranscriptSegment] = []
    for row in data:
        try:
            out.append(TranscriptSegment.model_validate(row))
        except Exception:
            return None
    return out


def save_cached_transcript(
    derived_dir: Path,
    fingerprint: str,
    segments: list[TranscriptSegment],
) -> Path:
    """Persist a transcript to disk so re-runs hit the cache."""
    p = transcript_cache_path(derived_dir, fingerprint)
    p.write_text(json.dumps([s.model_dump(mode="json") for s in segments], indent=2))
    return p


__all__ = [
    "transcribe",
    "load_cached_transcript",
    "save_cached_transcript",
    "transcript_cache_path",
    "clear_model_cache",
]
