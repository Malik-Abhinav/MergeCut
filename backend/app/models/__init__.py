"""Pydantic models for the MergeCut domain.

The full domain model (Project, Segment, MechanicalEdit, Conflict, Resolution,
Verification) is defined progressively per phase. Only models required for
Phase 1 (semantic analysis schema) live here for now.
"""

from __future__ import annotations

from app.models.media import (  # noqa: F401
    MediaError,
    NormalizationInfo,
    Shot,
    TranscriptSegment,
    UnsupportedFormatError,
    VideoMetadata,
    VideoRepresentation,
)

# Re-export the semantic schema so callers can `from app.models import ...`.
from app.models.semantic import (  # noqa: F401
    BranchSafety,
    ConflictEvidence,
    ConflictType,
    SemanticAnalysisResult,
    SemanticConflict,
    Severity,
)
