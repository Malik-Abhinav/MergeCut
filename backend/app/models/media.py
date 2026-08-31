"""Pydantic models for the Phase 2 media representation.

This is the structured output of `process_video` (see
`app/services/media/pipeline.py`). The schema is deliberately a
*superset* of the example shape in the user's Phase 2 brief:

    {
      "video_id": "...",
      "duration_seconds": 0,
      "width": 0,
      "height": 0,
      "fps": 0,
      "codec": "",
      "audio_present": true,
      "normalized_path": "",
      "shots": [
        {
          "shot_id": "...",
          "start": 0.0,
          "end": 0.0,
          "keyframe_paths": [],
          "transcript": ""
        }
      ]
    }

The pipeline attaches a few additional fields (source path, content
hash, audio path, transcripts as structured segments with per-word
confidence when available). They are *additive* — every field in the
brief shape is present and named the same.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Transcript pieces.
# ---------------------------------------------------------------------------


class TranscriptSegment(BaseModel):
    """One timestamped segment from the ASR pass."""

    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0.0, description="Segment start in seconds.")
    end: float = Field(ge=0.0, description="Segment end in seconds.")
    text: str = Field(description="Recognized text for this segment.")
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Average word-level confidence if the ASR backend reports it. None when unavailable."
        ),
    )


# ---------------------------------------------------------------------------
# Shot representation.
# ---------------------------------------------------------------------------


class Shot(BaseModel):
    """One coarse shot detected by PySceneDetect."""

    model_config = ConfigDict(extra="forbid")

    shot_id: str = Field(description="Stable id (e.g. 'shot_03').")
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    keyframe_paths: list[Path] = Field(
        default_factory=list,
        description=(
            "Absolute paths to one or more representative keyframes for this "
            "shot. Currently one per shot (midpoint); the field is a list so "
            "Phase 3 alignment can pick multiple fingerprints without a "
            "schema change."
        ),
    )
    transcript: str = Field(
        default="",
        description=(
            "Joined transcript text for segments whose midpoint falls inside "
            "this shot's [start, end] window. Empty when the video has no "
            "speech or no audio track."
        ),
    )
    transcript_segments: list[TranscriptSegment] = Field(
        default_factory=list,
        description=(
            "Structured transcript segments for this shot (same as `transcript` "
            "but with timestamps + per-segment confidence)."
        ),
    )


# ---------------------------------------------------------------------------
# Top-level representation.
# ---------------------------------------------------------------------------


class VideoMetadata(BaseModel):
    """Probe-level facts about the original upload."""

    model_config = ConfigDict(extra="forbid")

    duration_seconds: float = Field(ge=0.0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    fps: float = Field(ge=0.0)
    codec: str = Field(description="ffmpeg codec name (e.g. 'h264').")
    audio_present: bool
    audio_codec: str | None = Field(
        default=None, description="None when no audio track is present."
    )
    bit_rate: int | None = Field(default=None, ge=0)


class NormalizationInfo(BaseModel):
    """Record of whether the pipeline had to re-encode the upload."""

    model_config = ConfigDict(extra="forbid")

    normalized: bool = Field(
        description=(
            "True iff the upload was re-encoded to the working format. "
            "False when the upload was already in a supported working "
            "format and we just symlinked/copied the bytes."
        )
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Why normalization happened (codec, container, fps, ...). "
            "None when no normalization was needed."
        ),
    )


class VideoRepresentation(BaseModel):
    """Structured representation of a single uploaded video.

    The brief shape (§29 Phase 2 of PROJECT_PLAN) lists metadata fields
    at the top level. Internally we keep them in a nested
    `VideoMetadata` for clarity, but we also expose them as top-level
    fields here so the serialized JSON matches the brief exactly
    without forcing every caller to dig into `metadata.duration_seconds`.
    """

    model_config = ConfigDict(extra="forbid")

    # Top-level metadata (mirrors the brief shape).
    video_id: str = Field(description="Stable id derived from content hash.")
    duration_seconds: float = Field(ge=0.0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    fps: float = Field(ge=0.0)
    codec: str
    audio_present: bool
    audio_codec: str | None = None
    bit_rate: int | None = Field(default=None, ge=0)

    # Pipeline-level fields.
    source_path: Path = Field(description="Absolute path to the original upload.")
    normalized_path: Path = Field(description="Absolute path to the working MP4.")
    audio_path: Path | None = Field(
        default=None,
        description=(
            "Absolute path to the extracted mono 16 kHz WAV. None when the "
            "video has no audio track."
        ),
    )
    normalization: NormalizationInfo
    metadata: VideoMetadata = Field(
        description=(
            "Same data as the top-level metadata fields, kept here for "
            "completeness. Pydantic keeps them in sync."
        )
    )
    shots: list[Shot] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Keep top-level + nested metadata in sync on every construction.
    # ------------------------------------------------------------------

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Accept either nested or flat metadata on input."""
        if isinstance(obj, dict) and "metadata" in obj:
            meta = obj["metadata"]
            # Promote any missing top-level fields from the nested metadata.
            for k in (
                "duration_seconds",
                "width",
                "height",
                "fps",
                "codec",
                "audio_present",
                "audio_codec",
                "bit_rate",
            ):
                if k not in obj and k in meta:
                    obj[k] = meta[k]
        return super().model_validate(obj, *args, **kwargs)

    @classmethod
    def from_components(
        cls,
        *,
        video_id: str,
        source_path: Path,
        normalized_path: Path,
        audio_path: Path | None,
        metadata: VideoMetadata,
        normalization: NormalizationInfo,
        shots: list[Shot],
    ) -> VideoRepresentation:
        """Build a representation that keeps the nested and flat metadata
        fields consistent in one place."""
        return cls(
            video_id=video_id,
            duration_seconds=metadata.duration_seconds,
            width=metadata.width,
            height=metadata.height,
            fps=metadata.fps,
            codec=metadata.codec,
            audio_present=metadata.audio_present,
            audio_codec=metadata.audio_codec,
            bit_rate=metadata.bit_rate,
            source_path=source_path,
            normalized_path=normalized_path,
            audio_path=audio_path,
            normalization=normalization,
            metadata=metadata,
            shots=shots,
        )


# ---------------------------------------------------------------------------
# Errors raised by the pipeline.
# ---------------------------------------------------------------------------


class MediaError(RuntimeError):
    """Raised when a video cannot be processed (unsupported format, etc.)."""


class UnsupportedFormatError(MediaError):
    """Raised when ffprobe cannot read the file as a supported container."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"unsupported format at {path}: {reason}")
        self.path = path
        self.reason = reason


__all__ = [
    "Shot",
    "TranscriptSegment",
    "VideoMetadata",
    "NormalizationInfo",
    "VideoRepresentation",
    "MediaError",
    "UnsupportedFormatError",
]
