"""Phase 2 media preprocessing services.

This package is the *only* place that talks to FFmpeg / PySceneDetect /
faster-whisper for video processing. The split mirrors the
`app.services.minimax` pattern: thin, focused modules with one
responsibility each, glued together by `pipeline.py`.
"""

from __future__ import annotations
