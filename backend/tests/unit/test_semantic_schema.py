"""Tests for the strict Pydantic schema used by the M3 semantic analyzer.

These tests pin the contract from PROJECT_PLAN §15 — any field/type change
here must also bump the prompt version in
`app.services.minimax.prompts.PROMPT_VERSION`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.semantic import (
    ConflictEvidence,
    ConflictType,
    SemanticAnalysisResult,
    SemanticConflict,
    Severity,
)


def test_conflict_type_enum_matches_plan() -> None:
    expected = {
        "prerequisite_loss",
        "qualifier_loss",
        "exception_loss",
        "temporal_scope_change",
        "causal_dependency_break",
        "entity_scope_change",
        "narrative_dependency_break",
        "contradiction",
        "other",
    }
    assert {ct.value for ct in ConflictType} == expected


def test_severity_enum() -> None:
    assert {s.value for s in Severity} == {"low", "medium", "high"}


def test_evidence_must_have_end_after_start() -> None:
    with pytest.raises(ValidationError):
        ConflictEvidence(video="base", start=10.0, end=5.0, description="bad")


def test_minimal_valid_result_parses() -> None:
    payload = {
        "branch_a_safe": {
            "safe": True,
            "rationale": "no changes",
            "affected_claims": [],
            "confidence": 0.99,
        },
        "branch_b_safe": {
            "safe": True,
            "rationale": "no changes",
            "affected_claims": [],
            "confidence": 0.99,
        },
        "combined_safe": True,
        "conflicts": [],
        "overall_confidence": 0.99,
    }
    result = SemanticAnalysisResult.model_validate(payload)
    assert result.combined_safe is True
    assert result.conflicts == []


def test_conflict_with_evidence_parses() -> None:
    payload = {
        "branch_a_safe": {
            "safe": True,
            "rationale": "ok alone",
            "affected_claims": [],
            "confidence": 0.9,
        },
        "branch_b_safe": {
            "safe": True,
            "rationale": "ok alone",
            "affected_claims": [],
            "confidence": 0.9,
        },
        "combined_safe": False,
        "conflicts": [
            {
                "id": "conflict_03",
                "type": "prerequisite_loss",
                "severity": "high",
                "base_claim": "unplug before opening",
                "branch_a_effect": "removes the unplug instruction",
                "branch_b_effect": "removes the later prerequisite reminder",
                "combined_effect": "prerequisite disappears",
                "branch_a_safe_alone": True,
                "branch_b_safe_alone": True,
                "combined_safe": False,
                "evidence": [
                    {
                        "video": "base",
                        "start": 18.25,
                        "end": 24.40,
                        "description": "initial unplug instruction",
                    },
                ],
                "confidence": 0.94,
                "recommended_resolution": "Restore one explicit unplug step.",
            }
        ],
        "overall_confidence": 0.92,
    }
    result = SemanticAnalysisResult.model_validate(payload)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].type is ConflictType.PREREQUISITE_LOSS
    assert result.conflicts[0].severity is Severity.HIGH


def test_extra_top_level_keys_rejected() -> None:
    payload = {
        "branch_a_safe": {
            "safe": True,
            "rationale": "x",
            "affected_claims": [],
            "confidence": 0.5,
        },
        "branch_b_safe": {
            "safe": True,
            "rationale": "x",
            "affected_claims": [],
            "confidence": 0.5,
        },
        "combined_safe": True,
        "conflicts": [],
        "overall_confidence": 0.5,
        "made_up_field": "nope",
    }
    with pytest.raises(ValidationError):
        SemanticAnalysisResult.model_validate(payload)


def test_confidence_must_be_in_range() -> None:
    bad = {
        "branch_a_safe": {
            "safe": True,
            "rationale": "x",
            "affected_claims": [],
            "confidence": 1.5,  # out of range
        },
        "branch_b_safe": {
            "safe": True,
            "rationale": "x",
            "affected_claims": [],
            "confidence": 0.5,
        },
        "combined_safe": True,
        "conflicts": [],
        "overall_confidence": 0.5,
    }
    with pytest.raises(ValidationError):
        SemanticAnalysisResult.model_validate(bad)


def test_semantic_conflict_rejects_unknown_type() -> None:
    bad = {
        "id": "x",
        "type": "mystery_type",
        "severity": "low",
        "base_claim": "x",
        "branch_a_effect": "x",
        "branch_b_effect": "x",
        "combined_effect": "x",
        "branch_a_safe_alone": True,
        "branch_b_safe_alone": True,
        "combined_safe": False,
        "evidence": [],
        "confidence": 0.5,
        "recommended_resolution": "x",
    }
    with pytest.raises(ValidationError):
        SemanticConflict.model_validate(bad)
