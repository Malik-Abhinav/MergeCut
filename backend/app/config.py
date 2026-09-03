"""MergeCut configuration.

Environment variables (see .env.example at repo root):

- GMI_API_KEY            : required for any live M3 call.
- GMI_BASE_URL           : OpenAI-compatible base URL for GMI Cloud.
- MINIMAX_M3_MODEL       : model identifier used for semantic reasoning.
- MINIMAX_M27_MODEL      : optional second model for routine work.
- UPLOAD_DIR / DERIVED_DIR / DATABASE_PATH : local storage paths.
- MAX_VIDEO_SECONDS / MAX_UPLOAD_MB : MVP input limits.

Identifiers are kept configurable rather than hard-coded per AGENTS.md rule 7
("Never invent GMI API fields or model identifiers.").
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the MergeCut backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- GMI Cloud / MiniMax ---
    gmi_api_key: str = Field(default="", description="GMI Cloud API key")
    gmi_base_url: str = Field(
        default="https://api.gmi-serving.com/v1",
        description="OpenAI-compatible base URL for GMI Cloud",
    )
    minimax_m3_model: str = Field(
        default="MiniMaxAI/MiniMax-M3",
        description="Model id for semantic reasoning (verify against GMI docs).",
    )
    minimax_m27_model: str = Field(
        default="MiniMaxAI/MiniMax-M2.7",
        description="Optional model id for routine coding/secondary tasks.",
    )

    # --- Storage ---
    upload_dir: Path = Field(default=Path("./data/uploads"))
    derived_dir: Path = Field(default=Path("./data/derived"))
    database_path: Path = Field(default=Path("./data/mergecut.db"))

    # --- MVP limits ---
    max_video_seconds: int = Field(default=180, ge=1)
    max_upload_mb: int = Field(default=250, ge=1)

    # --- HTTP / timeouts ---
    request_timeout_s: float = Field(default=60.0, gt=0)

    # --- Media preprocessing ---
    # When empty, the normalize + check_ffmpeg modules use
    # `shutil.which(...)` to locate these on PATH.
    ffmpeg_path: str = Field(default="", description="Path to ffmpeg binary.")
    ffprobe_path: str = Field(default="", description="Path to ffprobe binary.")
    # Scene detection threshold (PySceneDetect ContentDetector).
    scene_threshold: float = Field(default=27.0, ge=0.0)
    # Whisper model identifier (passed to faster-whisper).
    whisper_model: str = Field(default="base", description="faster-whisper model name.")
    # Device for whisper inference: 'cpu' | 'cuda' | 'auto'.
    whisper_device: str = Field(default="cpu", description="faster-whisper device.")
    # Compute type for whisper: 'int8' | 'float16' | 'float32' | 'int8_float16' etc.
    whisper_compute_type: str = Field(default="int8", description="faster-whisper compute_type.")
    # VAD minimum-silence threshold (ms). Default of faster-whisper
    # is 2000ms, which is tuned for natural monologue and collapses
    # short inter-shot silence; we tighten it so Phase 3 fixtures
    # with sub-second silence boundaries produce separate segments.
    whisper_min_silence_ms: int = Field(default=200, ge=50, le=2000)
    # Whisper transcription chunk length (seconds). faster-whisper
    # concatenates VAD chunks up to `chunk_length` seconds before
    # running the decoder; smaller values force more (and shorter)
    # decoded segments at the cost of slightly more overhead.
    whisper_chunk_length: int = Field(default=5, ge=1, le=30)
    # When True, the pipeline transcribes each shot's audio
    # independently by cutting a per-shot WAV and running
    # faster-whisper on the cut. This produces cleaner per-shot
    # transcripts when full-file ASR merges sentences across
    # shot boundaries, at the cost of N ASR passes for an N-shot
    # video. Default True for Phase 3; Phase 2 acceptance tests
    # override to False to keep the single-pass behaviour.
    transcribe_per_shot: bool = Field(default=True)
    # Max audio length (seconds) per shot. If a shot's audio
    # exceeds this, the per-shot cut is rejected and the shot's
    # transcript is left empty. Defensive against pathological
    # long-shot fixtures.
    max_shot_audio_seconds: float = Field(default=30.0, ge=1.0, le=120.0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()
