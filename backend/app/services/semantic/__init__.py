"""Phase 4 semantic analysis package.

Submodules:

- `context`    : build a `SemanticContext` from the Phase 3
                 alignment results (no semantic interpretation).
- `prompts_v2` : the v3.0 system intent + user-payload builder
                 (used by the LEGACY edit-centric v2 orchestrator).
- `run`        : the LEGACY edit-centric v2 orchestrator
                 `analyze_merge()` (kept for the Phase 1 v1
                 compatibility harness).
- `claims`     : the NEW claim-centric pipeline (STEP 1-5
                 per the user's brief). Production code should
                 prefer `claims.analyze_claims()`.
"""

from __future__ import annotations

from app.services.semantic import claims
from app.services.semantic.context import (
    BaseShotInfo,
    CandidatePair,
    EditInfo,
    ReconstructedBranchContent,
    SemanticContext,
    build_semantic_context,
    render_context_for_prompt,
)
from app.services.semantic.prompts_v2 import (
    PROMPT_VERSION,
    REPAIR_INSTRUCTION,
    SYSTEM_INTENT,
    build_user_payload,
)
from app.services.semantic.run import AnalysisArtifacts, analyze_merge

__all__ = [
    "BaseShotInfo",
    "CandidatePair",
    "EditInfo",
    "ReconstructedBranchContent",
    "SemanticContext",
    "AnalysisArtifacts",
    "build_semantic_context",
    "render_context_for_prompt",
    "PROMPT_VERSION",
    "SYSTEM_INTENT",
    "REPAIR_INSTRUCTION",
    "build_user_payload",
    "analyze_merge",
    "claims",
]
