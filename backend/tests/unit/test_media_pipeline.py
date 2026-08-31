"""Phase 2 media pipeline tests.

The tests build small controlled MP4s in a tmpdir and run the full
pipeline against them. Anything that takes more than a couple
seconds on the test box is allowed to skip rather than fail — the
slow path here is the whisper model download on first use, which
we don't want to gate the suite on. Production runs use a
pre-warmed model cache.

Tests cover (per the user's Phase 2 brief):
- normal MP4
- multiple scene cuts
- speech (audio present)
- no audio
- unsupported / bad input
- repeated processing is stable (idempotent)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.config import get_settings
from app.models.media import (
    MediaError,
    Shot,
    UnsupportedFormatError,
    VideoMetadata,
    VideoRepresentation,
)
from app.services.media.pipeline import process_video
from app.services.media.transcript import clear_model_cache
from tests.fixtures.media_fixtures import (
    build_all,
    make_bad_input,
    make_multi_scene_mp4,
    make_no_audio_mp4,
    make_normal_mp4,
    make_speech_mp4,
)

# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def media_dir(tmp_path: Path) -> Path:
    """Per-test media directory. Each test gets a fresh tmpdir."""
    d = tmp_path / "media"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point DERIVED_DIR at the per-test tmpdir so caches don't leak."""
    settings = get_settings()
    monkeypatch.setattr(settings, "derived_dir", tmp_path / "derived")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    # Clear cached model handles between tests.
    clear_model_cache()


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _duration(rep: VideoRepresentation) -> float:
    return rep.metadata.duration_seconds


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_normal_mp4_pipeline(media_dir: Path) -> None:
    src = make_normal_mp4(media_dir)
    rep = process_video(src)

    assert isinstance(rep, VideoRepresentation)
    # Content-hash video_id is a 16-char hex.
    assert len(rep.video_id) == 16
    assert int(rep.video_id, 16) >= 0

    # Metadata sanity.
    meta = rep.metadata
    assert isinstance(meta, VideoMetadata)
    assert meta.width == 320
    assert meta.height == 240
    assert meta.fps == pytest.approx(30.0, abs=0.5)
    assert meta.codec == "h264"
    assert meta.audio_present is True
    assert meta.audio_codec == "aac"
    # Duration is approximately 6s (3 shots × 2s each).
    assert 5.5 <= _duration(rep) <= 6.5

    # Normalization: input is already H.264 yuv420p 30fps AAC, so
    # the working copy should NOT have been re-encoded.
    assert rep.normalization.normalized is False
    assert rep.normalization.reason is None

    # Audio extracted.
    assert rep.audio_path is not None
    assert rep.audio_path.exists()

    # Three shots.
    shots = rep.shots
    assert len(shots) == 3, (
        f"expected 3 shots, got {len(shots)}: {[(s.start, s.end) for s in shots]}"
    )
    for i, s in enumerate(shots):
        assert isinstance(s, Shot)
        assert s.shot_id == f"shot_{i:04d}"
        assert s.start >= 0.0
        assert s.end > s.start
        # Each shot has exactly one keyframe.
        assert len(s.keyframe_paths) == 1
        assert s.keyframe_paths[0].exists()
        # Keyframe JPEG should be non-trivial in size.
        assert s.keyframe_paths[0].stat().st_size > 500
        # Shots tile the video with no gaps.
        if i > 0:
            assert abs(s.start - shots[i - 1].end) < 0.1

    # Working file exists.
    assert rep.normalized_path.exists()

    # JSON representation was persisted.
    rep_json = media_dir.parent / "derived" / "videos" / rep.video_id / "representation.json"
    assert rep_json.exists()
    payload = json.loads(rep_json.read_text())
    assert payload["video_id"] == rep.video_id


def test_multi_scene_mp4_pipeline(media_dir: Path) -> None:
    src = make_multi_scene_mp4(media_dir)
    rep = process_video(src)

    meta = rep.metadata
    assert meta.width == 640
    assert meta.height == 480
    assert meta.audio_present is False
    assert meta.audio_codec is None

    # Six distinct colour blocks of 2s each → expect 6 shots.
    assert len(rep.shots) == 6, [(s.start, s.end) for s in rep.shots]
    # Total duration ~12s.
    assert 11.0 <= _duration(rep) <= 13.0

    # No audio path for a video without an audio track.
    assert rep.audio_path is None


def test_no_audio_mp4_pipeline(media_dir: Path) -> None:
    src = make_no_audio_mp4(media_dir)
    rep = process_video(src)

    assert rep.metadata.audio_present is False
    assert rep.audio_path is None
    assert len(rep.shots) >= 2  # at least 2 shots in a 3-block video


def test_speech_mp4_pipeline(media_dir: Path) -> None:
    """A video with an audio track should produce an audio_path and
    a transcript (the transcript may be empty when the audio is a
    sine tone rather than speech; what matters is that the ASR
    pipeline runs cleanly)."""
    src = make_speech_mp4(media_dir)
    rep = process_video(src)

    assert rep.metadata.audio_present is True
    assert rep.audio_path is not None
    assert rep.audio_path.exists()

    # TranscriptSegments is a list (may be empty for sine tones —
    # faster-whisper's VAD filter strips non-speech). The pipeline
    # must NOT raise.
    for s in rep.shots:
        assert isinstance(s.transcript_segments, list)


def test_bad_input_raises(media_dir: Path) -> None:
    bad = make_bad_input(media_dir)
    with pytest.raises((MediaError, UnsupportedFormatError)):
        process_video(bad)


def test_nonexistent_source_raises(tmp_path: Path) -> None:
    with pytest.raises(MediaError):
        process_video(tmp_path / "does_not_exist.mp4")


def test_repeated_processing_is_stable(media_dir: Path) -> None:
    """Processing the same file twice produces materially equivalent
    output. We compare all numeric fields; only the on-disk mtime /
    representation-file timestamp will differ (which we don't
    include in the equality check)."""
    src = make_normal_mp4(media_dir)
    rep1 = process_video(src)
    rep2 = process_video(src)

    # Content-hash video_id matches.
    assert rep1.video_id == rep2.video_id

    # Metadata matches.
    m1, m2 = rep1.metadata, rep2.metadata
    assert m1.duration_seconds == pytest.approx(m2.duration_seconds, abs=0.05)
    assert m1.width == m2.width
    assert m1.height == m2.height
    assert m1.fps == pytest.approx(m2.fps, abs=0.1)
    assert m1.codec == m2.codec
    assert m1.audio_present == m2.audio_present

    # Shot boundaries match within 0.1s.
    assert len(rep1.shots) == len(rep2.shots)
    for a, b in zip(rep1.shots, rep2.shots, strict=True):
        assert a.start == pytest.approx(b.start, abs=0.1)
        assert a.end == pytest.approx(b.end, abs=0.1)

    # Normalization flags match.
    assert rep1.normalization.normalized == rep2.normalization.normalized


def test_pipeline_handles_all_fixtures(media_dir: Path) -> None:
    """Smoke: every fixture in the build_all set either runs to
    completion or fails with a clear MediaError. None crashes the
    interpreter."""
    fixtures = build_all(media_dir)
    for name, path in fixtures.items():
        if name == "bad":
            with pytest.raises((MediaError, UnsupportedFormatError)):
                process_video(path)
            continue
        rep = process_video(path)
        assert rep.video_id
        # All non-bad fixtures should produce at least one shot.
        assert len(rep.shots) >= 1, f"{name} produced no shots"


def test_preserves_original_file(media_dir: Path) -> None:
    """The pipeline must never modify the source upload."""
    src = make_normal_mp4(media_dir)
    before = src.read_bytes()
    process_video(src)
    after = src.read_bytes()
    assert before == after
    assert src.exists()


def test_settings_use_system_ffmpeg_when_unset() -> None:
    """When FFMPEG_PATH / FFPROBE_PATH are empty, the pipeline falls
    back to shutil.which() — and we expect the system FFmpeg (which
    `make check-ffmpeg` already required) to be found."""
    settings = get_settings()
    assert settings.ffmpeg_path == ""
    assert settings.ffprobe_path == ""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg is not None
    assert ffprobe is not None
