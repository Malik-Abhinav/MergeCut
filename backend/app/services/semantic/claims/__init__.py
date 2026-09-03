"""Phase 4 claim-centric pipeline package.

Submodules:

- `extract`     : STEP 1 — M3 call to extract BASE claims.
- `reconstruct` : STEP 2 — deterministic per-branch claim
                   reconstruction from the Phase 3 alignment.
- `evaluate`    : STEP 3 — M3 per-claim per-branch verdicts.
- `interact`    : STEP 4 — deterministic cross-edit interaction
                   derivation (no M3).
- `explain`     : STEP 5 — M3 prose for the explanation.
- `orchestrate` : the top-level `analyze_claims()` orchestrator.
- `prompts_claims` : the M3 prompts (claim extraction, evaluation,
                   explanation).
"""

from __future__ import annotations

from app.services.semantic.claims.evaluate import (
    evaluate_all_claims,
    evaluate_one_claim_in_branch,
)
from app.services.semantic.claims.explain import (
    EXPLANATION_PROMPT_VERSION,
    explain_interaction,
)
from app.services.semantic.claims.extract import extract_base_claims
from app.services.semantic.claims.interact import (
    aggregate_overall_impact,
    aggregate_overall_interaction,
    build_claim_interaction,
    derive_interaction,
)
from app.services.semantic.claims.orchestrate import (
    ClaimAnalysisArtifacts,
    analyze_claims,
)
from app.services.semantic.claims.prompts_claims import (
    EVALUATION_PROMPT_VERSION,
    EVALUATION_REPAIR_INSTRUCTION,
    EVALUATION_SYSTEM_INTENT,
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_REPAIR_INSTRUCTION,
    EXTRACTION_SYSTEM_INTENT,
    build_evaluation_user_payload,
    build_explanation_user_payload,
    build_extraction_user_payload,
)
from app.services.semantic.claims.reconstruct import (
    deterministic_surrogate_status,
    reconstruct_branch_claims,
    reconstruct_combined_claims,
)
from app.services.semantic.claims.represent import (
    ActualContentLine,
    EditMetadata,
    EditMetadataEntry,
    ReconstructedActualContent,
    reconstruct_base_actual_content,
    reconstruct_branch_actual_content,
    reconstruct_combined_actual_content,
    write_representation_diagnostics,
)

__all__ = [
    "ClaimAnalysisArtifacts",
    "analyze_claims",
    "evaluate_all_claims",
    "evaluate_one_claim_in_branch",
    "EXPLANATION_PROMPT_VERSION",
    "explain_interaction",
    "extract_base_claims",
    "aggregate_overall_impact",
    "aggregate_overall_interaction",
    "build_claim_interaction",
    "derive_interaction",
    "EVALUATION_PROMPT_VERSION",
    "EVALUATION_REPAIR_INSTRUCTION",
    "EVALUATION_SYSTEM_INTENT",
    "EXTRACTION_PROMPT_VERSION",
    "EXTRACTION_REPAIR_INSTRUCTION",
    "EXTRACTION_SYSTEM_INTENT",
    "build_evaluation_user_payload",
    "build_explanation_user_payload",
    "build_extraction_user_payload",
    "deterministic_surrogate_status",
    "reconstruct_branch_claims",
    "reconstruct_combined_claims",
    "ActualContentLine",
    "EditMetadata",
    "EditMetadataEntry",
    "ReconstructedActualContent",
    "reconstruct_base_actual_content",
    "reconstruct_branch_actual_content",
    "reconstruct_combined_actual_content",
    "write_representation_diagnostics",
]
