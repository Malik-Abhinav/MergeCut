"""Pydantic models for the MergeCut domain.

The full domain model (Project, Segment, MechanicalEdit, Conflict, Resolution,
Verification) is defined progressively per phase. Only models required for
Phase 1 (semantic analysis schema) live here for now.
"""

from __future__ import annotations

from app.models.alignment import (  # noqa: F401
    AlignmentMatch,
    AlignmentResult,
    EditOperationType,
    ShotFingerprint,
    SimilarityComponents,
)

# Phase 4 — claim-centric redesign (replaces the edit-centric v2
# schema for production use). The deterministic cross-edit
# interaction derivation lives in `app.services.semantic.claims.interact`.
from app.models.claims import (  # noqa: F401
    BaseClaim,
    BranchClaims,
    ClaimCentricAnalysis,
    ClaimEvaluation,
    ClaimEvaluationRequest,
    ClaimEvidenceRegion,
    ClaimImportance,
    ClaimInteraction,
    ClaimStatus,
    ClaimSurvival,
    ClaimType,
)
from app.models.claims import CrossEditInteraction as CrossEditInteraction  # noqa: F401
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

# Phase 4 — richer two-axis taxonomy. Kept additive so the v1
# model above is still importable for the existing spike
# runner. New production code should use these.
from app.models.semantic_v2 import (  # noqa: F401
    BranchImpact,
    CrossEditInteraction_,
    ImpactLevel,
    LegacyV1Compat,
    SemanticAnalysisV2,
    TimestampedEvidence,
    to_legacy_v1,
)
