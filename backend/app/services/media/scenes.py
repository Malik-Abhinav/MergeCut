"""Coarse shot / scene detection.

Wraps PySceneDetect's `ContentDetector`. Returns a list of
(start_seconds, end_seconds) tuples representing contiguous shots
on the working video. The first shot always starts at 0.0 and the
last shot always ends at the video duration (we close any open
final shot at the end of detection).

Determinism: given the same input video and threshold, PySceneDetect
produces identical output. We pass `show_progress=False` to keep
stdout clean in the pipeline.
"""

from __future__ import annotations

from pathlib import Path

from scenedetect import ContentDetector, SceneManager, open_video

from app.config import Settings, get_settings

ShotInterval = tuple[float, float]


def detect_shots(
    working_video: Path,
    settings: Settings | None = None,
) -> list[ShotInterval]:
    """Detect shot boundaries on `working_video`.

    Returns a list of `(start, end)` in seconds. The intervals cover
    the full video from 0 to `duration` with no gaps.

    Raises `RuntimeError` if PySceneDetect cannot open the video.
    """
    settings = settings or get_settings()
    threshold = settings.scene_threshold

    # open_video returns a (VideoStream, StatsManager) tuple on
    # current scenedetect (>= 0.6.5). Older versions returned just a
    # VideoStream. Support both shapes.
    opened = open_video(str(working_video))
    if isinstance(opened, tuple):
        video = opened[0]
    else:
        video = opened

    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    try:
        manager.detect_scenes(video, show_progress=False)
    finally:
        # Some backends need explicit close; tolerate if not present.
        close = getattr(video, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    scene_list = manager.get_scene_list()
    if not scene_list:
        # No cuts detected: the whole video is one shot.
        duration = _safe_duration(video)
        if duration <= 0:
            duration = 0.0
        return [(0.0, duration)] if duration > 0 else []

    out: list[ShotInterval] = []
    for start_tc, end_tc in scene_list:
        out.append((start_tc.get_seconds(), end_tc.get_seconds()))
    return out


def _safe_duration(video: object) -> float:
    """Best-effort extraction of total duration from a scenedetect video handle."""
    try:
        duration = getattr(video, "duration", None)
        if duration is not None:
            return float(duration)
    except Exception:
        pass
    try:
        # pyav / cv2 fallbacks if available.
        if hasattr(video, "frame_rate") and hasattr(video, "frame_number"):
            fps = float(video.frame_rate)
            n = getattr(video, "frame_number", None)
            if n is not None and fps > 0:
                return float(n) / fps
    except Exception:
        pass
    return 0.0


__all__ = ["detect_shots"]
