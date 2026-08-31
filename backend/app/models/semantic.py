"""Strict Pydantic schema for MiniMax M3 semantic analysis output.

This is the contract from PROJECT_PLAN §15. M3 is asked to return
*only* data matching this schema. Every response is validated against
this module before being trusted downstream; if validation fails we
retry once with a repair prompt per PROJECT_PLAN §14.4.

The schema is intentionally narrower than the full domain model: it is
only what the M3 semantic-analyzer role returns. Mechanical-edit,
project, render, and verifier models live in their own files (Phase 2+).
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enums (PROJECT_PLAN §8.3)
# ---------------------------------------------------------------------------


class ConflictType(enum.StrEnum):
    """Semantic conflict categories from PROJECT_PLAN §8.3.

    The seven categories listed in the plan plus `contradiction` and `other`
    for cases that don't fit cleanly. Use the literal values in prompts and
    stored JSON so they round-trip cleanly.
    """

    PREREQUISITE_LOSS = "prerequisite_loss"
    QUALIFIER_LOSS = "qualifier_loss"
    EXCEPTION_LOSS = "exception_loss"
    TEMPORAL_SCOPE_CHANGE = "temporal_scope_change"
    CAUSAL_DEPENDENCY_BREAK = "causal_dependency_break"
    ENTITY_SCOPE_CHANGE = "entity_scope_change"
    NARRATIVE_DEPENDENCY_BREAK = "narrative_dependency_break"
    CONTRADICTION = "contradiction"
    OTHER = "other"


class Severity(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Literal alias kept for typing convenience (matches PROJECT_PLAN §8.3 example).
SemanticConflictTypeLiteral = Literal[
    "prerequisite_loss",
    "qualifier_loss",
    "exception_loss",
    "temporal_scope_change",
    "causal_dependency_break",
    "entity_scope_change",
    "narrative_dependency_break",
    "contradiction",
    "other",
]


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


class ConflictEvidence(BaseModel):
    """Timestamped evidence pointer for a semantic conflict."""

    model_config = ConfigDict(extra="forbid")

    video: Literal["base", "branch_a", "branch_b", "merged"] = Field(
        description="Which video the evidence snippet comes from."
    )
    start: float = Field(ge=0.0, description="Start timestamp in seconds.")
    end: float = Field(ge=0.0, description="End timestamp in seconds.")
    description: str = Field(min_length=1, description="What this evidence shows.")

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: float, info) -> float:  # type: ignore[no-untyped-def]
        start = info.data.get("start")
        if start is not None and v < start:
            raise ValueError("end must be >= start")
        return v


class SemanticConflict(BaseModel):
    """One cross-edit semantic conflict identified by M3."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable id, e.g. 'conflict_03'.")
    type: ConflictType
    severity: Severity
    base_claim: str = Field(min_length=1)
    branch_a_effect: str = Field(min_length=1)
    branch_b_effect: str = Field(min_length=1)
    combined_effect: str = Field(min_length=1)
    branch_a_safe_alone: bool
    branch_b_safe_alone: bool
    combined_safe: bool
    evidence: list[ConflictEvidence] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_resolution: str = Field(min_length=1)


class BranchSafety(BaseModel):
    """Per-branch independent safety verdict."""

    model_config = ConfigDict(extra="forbid")

    safe: bool
    rationale: str = Field(min_length=1)
    affected_claims: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class SemanticAnalysisResult(BaseModel):
    """Top-level structured response from M3 semantic analyzer.

    Field names are snake_case in Python but JSON is emitted in snake_case
    to match how M3 is asked to respond in the prompt.
    """

    model_config = ConfigDict(extra="forbid")

    branch_a_safe: BranchSafety
    branch_b_safe: BranchSafety
    combined_safe: bool = Field(
        description=(
            "True iff applying BOTH branches together still preserves the original "
            "meaning. False means a semantic conflict was detected."
        )
    )
    conflicts: list[SemanticConflict] = Field(default_factory=list)
    overall_confidence: float = Field(ge=0.0, le=1.0)
    notes: str | None = Field(
        default=None,
        description="Optional short note from the model. Not load-bearing.",
    )


# Convenience aggregate used by the spike runner.

SpikeVerdict = Literal["safe", "conflict"]
"""Spike-level classification the spike script reports per fixture."""
