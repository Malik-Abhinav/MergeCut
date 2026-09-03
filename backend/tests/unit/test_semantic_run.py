"""Unit tests for `app.services.semantic.run` (orchestrator).

Covers:
- The end-to-end pipeline (alignment -> context -> M3 ->
  validation) with a mock `MiniMaxClient`.
- The one-retry-on-validation-failure behaviour.
- The legacy v1 projection populated on the returned result.
- The M3 client is *not* called twice on the happy path.
- The M3 client *is* called twice on the first-attempt
  validation failure (one retry).
- The orchestrator raises on two consecutive validation
  failures.
- Prompt version + model are surfaced on the artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models.media import Shot, VideoMetadata, VideoRepresentation
from app.models.semantic_v2 import (
    ImpactLevel,
    SemanticAnalysisV2,
)
from app.services.minimax.client import MiniMaxError
from app.services.semantic.prompts_v2 import PROMPT_VERSION
from app.services.semantic.run import analyze_merge

# ---------------------------------------------------------------------------
# Mock M3 client.
# ---------------------------------------------------------------------------


class _MockM3:
    """Records every call to chat_json_sync and replays a queued
    sequence of responses (or raises)."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.model = "MiniMaxAI/MiniMax-M3"

    def chat_json_sync(self, *, system: str, user: str, **kwargs: Any) -> str:
        self.calls.append({"system": system, "user": user})
        if not self._responses:
            raise MiniMaxError("mock: no more responses queued")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _make_rep(video_id: str, *, transcripts: list[str]) -> VideoRepresentation:
    from app.models.media import NormalizationInfo

    metadata = VideoMetadata(
        duration_seconds=float(len(transcripts)),
        width=320,
        height=240,
        fps=30.0,
        codec="h264",
        audio_present=bool(transcripts),
    )
    shots = [
        Shot(
            shot_id=f"shot_{i:04d}",
            start=float(i),
            end=float(i) + 1.0,
            keyframe_paths=[],
            transcript=text,
            transcript_segments=[],
        )
        for i, text in enumerate(transcripts)
    ]
    return VideoRepresentation.from_components(
        video_id=video_id,
        source_path=Path(f"/tmp/{video_id}.mp4"),
        normalized_path=Path(f"/tmp/{video_id}.working.mp4"),
        audio_path=None,
        metadata=metadata,
        normalization=NormalizationInfo(normalized=False),
        shots=shots,
    )


def _valid_v2_payload() -> dict[str, Any]:
    """A valid SemanticAnalysisV2 JSON for the mock to return."""
    return {
        "branch_a_impact": {
            "branch": "branch_a",
            "impact_level": "preserved",
            "affected_claims": ["claim A"],
            "preserved_equivalents": ["other claim"],
            "evidence": [
                {
                    "video": "base",
                    "start": 0.0,
                    "end": 1.0,
                    "description": "BASE says claim A",
                }
            ],
            "confidence": 0.9,
            "rationale": "claim A is preserved in branch_a",
        },
        "branch_b_impact": {
            "branch": "branch_b",
            "impact_level": "preserved",
            "affected_claims": ["claim A"],
            "preserved_equivalents": ["other claim"],
            "evidence": [
                {
                    "video": "base",
                    "start": 0.0,
                    "end": 1.0,
                    "description": "BASE says claim A",
                }
            ],
            "confidence": 0.9,
            "rationale": "claim A is preserved in branch_b",
        },
        "combined_impact": "broken",
        "interactions": [
            {
                "branch_a_edit_ids": ["shot_0000"],
                "branch_b_edit_ids": ["shot_0000"],
                "combined_impact": "broken",
                "interaction_type": "creates_new_conflict",
                "conflict_type": "prerequisite_loss",
                "base_claim": "claim A",
                "branch_a_effect": "A removes one restatement",
                "branch_b_effect": "B removes the other restatement",
                "combined_effect": "claim A no longer appears",
                "evidence": [
                    {
                        "video": "base",
                        "start": 0.0,
                        "end": 2.0,
                        "description": "claim A in BASE",
                    }
                ],
                "confidence": 0.92,
                "recommended_resolution": "keep at least one restatement",
            }
        ],
        "overall_confidence": 0.9,
        "notes": "",
    }


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_analyze_merge_happy_path() -> None:
    base = _make_rep("base", transcripts=["claim A", "claim B"])
    a = _make_rep("a", transcripts=["claim A"])
    b = _make_rep("b", transcripts=["claim B"])
    client = _MockM3([json.dumps(_valid_v2_payload())])
    artifacts = analyze_merge(base=base, branch_a=a, branch_b=b, client=client)  # type: ignore[arg-type]
    # M3 was called exactly once.
    assert len(client.calls) == 1
    # And the orchestrator returned a validated v2 result.
    assert isinstance(artifacts.analysis, SemanticAnalysisV2)
    assert artifacts.analysis.combined_impact == ImpactLevel.BROKEN
    # And the legacy projection was populated.
    assert artifacts.analysis.legacy_v1_compat is not None
    assert artifacts.analysis.legacy_v1_compat.branch_a_safe is True
    assert artifacts.analysis.legacy_v1_compat.combined_safe is False
    # And the artifacts carry diagnostics.
    assert artifacts.prompt_version == PROMPT_VERSION
    assert artifacts.model == "MiniMaxAI/MiniMax-M3"
    assert artifacts.retries == 0


def test_analyze_merge_sends_prompt_version_in_system() -> None:
    base = _make_rep("base", transcripts=["x"])
    a = _make_rep("a", transcripts=["x"])
    b = _make_rep("b", transcripts=["x"])
    client = _MockM3([json.dumps(_valid_v2_payload())])
    analyze_merge(base=base, branch_a=a, branch_b=b, client=client)  # type: ignore[arg-type]
    assert PROMPT_VERSION in client.calls[0]["system"]
    assert PROMPT_VERSION in client.calls[0]["user"]


# ---------------------------------------------------------------------------
# Retry behaviour.
# ---------------------------------------------------------------------------


def test_analyze_merge_retries_on_first_validation_failure() -> None:
    base = _make_rep("base", transcripts=["x"])
    a = _make_rep("a", transcripts=["x"])
    b = _make_rep("b", transcripts=["x"])
    # First response is invalid (missing `interactions`); second
    # is the valid payload.
    client = _MockM3(
        [
            json.dumps({"branch_a_impact": {}, "branch_b_impact": {}}),
            json.dumps(_valid_v2_payload()),
        ]
    )
    artifacts = analyze_merge(base=base, branch_a=a, branch_b=b, client=client)  # type: ignore[arg-type]
    assert len(client.calls) == 2
    assert artifacts.retries == 1
    assert isinstance(artifacts.analysis, SemanticAnalysisV2)
    # The repair instruction was appended to the second user
    # payload.
    assert "schema" in client.calls[1]["user"].lower() or "Re-emit" in client.calls[1]["user"]


def test_analyze_merge_raises_after_two_validation_failures() -> None:
    base = _make_rep("base", transcripts=["x"])
    a = _make_rep("a", transcripts=["x"])
    b = _make_rep("b", transcripts=["x"])
    client = _MockM3(
        [
            json.dumps({"bad": "first response"}),
            json.dumps({"bad": "second response"}),
        ]
    )
    with pytest.raises(MiniMaxError):
        analyze_merge(base=base, branch_a=a, branch_b=b, client=client)  # type: ignore[arg-type]
    # M3 was called exactly twice.
    assert len(client.calls) == 2


def test_analyze_merge_raises_on_non_json_first_response() -> None:
    base = _make_rep("base", transcripts=["x"])
    a = _make_rep("a", transcripts=["x"])
    b = _make_rep("b", transcripts=["x"])
    client = _MockM3(["this is not json", json.dumps(_valid_v2_payload())])
    artifacts = analyze_merge(base=base, branch_a=a, branch_b=b, client=client)  # type: ignore[arg-type]
    # The retry should succeed.
    assert len(client.calls) == 2
    assert isinstance(artifacts.analysis, SemanticAnalysisV2)


# ---------------------------------------------------------------------------
# Context-level diagnostics.
# ---------------------------------------------------------------------------


def test_analyze_merge_produces_context() -> None:
    base = _make_rep("base", transcripts=["x", "y"])
    a = _make_rep("a", transcripts=["x", "y"])
    b = _make_rep("b", transcripts=["x", "y"])
    client = _MockM3([json.dumps(_valid_v2_payload())])
    artifacts = analyze_merge(base=base, branch_a=a, branch_b=b, client=client)  # type: ignore[arg-type]
    # Context is exposed for diagnostics.
    assert artifacts.context is not None
    # Alignment produced ≥1 op for each branch (the alignment
    # has its own coverage; we just sanity-check the count
    # is positive).
    assert artifacts.branch_a_alignment_op_count >= 1
    assert artifacts.branch_b_alignment_op_count >= 1
