"""Minimal upload-to-analysis API for the MergeCut MVP.

This module is deliberately a thin transport adapter. It saves the three
uploaded videos, runs the existing Phase 2–4 pipeline unchanged, and maps the
claim-centric artifacts into a compact response for the one-page frontend.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.models.claims import ClaimCentricAnalysis, ClaimSurvival
from app.models.media import MediaError
from app.services.media.pipeline import process_video
from app.services.minimax.client import MiniMaxClient, MiniMaxError
from app.services.semantic.claims.orchestrate import ClaimAnalysisArtifacts, analyze_claims

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["analysis"])


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float
    end: float
    description: str


class EffectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    rationale: str
    evidence: list[EvidenceResponse] = Field(default_factory=list)


class ClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim: str
    claim_type: str
    importance: str
    base_evidence: list[EvidenceResponse] = Field(default_factory=list)
    branch_a: EffectResponse
    branch_b: EffectResponse
    combined: EffectResponse
    interaction: str
    deterministic_rule: str
    explanation: str | None = None


class CombinedSliceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_index: int
    start: float
    end: float
    verdict: str
    text: str
    reason: str


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_detected: bool
    interaction: str
    overall_impact: str
    overall_confidence: float
    summary: str
    provider: str = "GMI Cloud"
    model: str
    claims: list[ClaimResponse]
    combined_timeline: list[CombinedSliceResponse] = Field(default_factory=list)


def _effect(survival: ClaimSurvival) -> EffectResponse:
    return EffectResponse(
        status=survival.status.value,
        rationale=survival.rationale,
        evidence=[EvidenceResponse(**item.model_dump()) for item in survival.surviving_evidence],
    )


def _survivals_by_id(analysis: ClaimCentricAnalysis, branch: str) -> dict[str, ClaimSurvival]:
    branch_claims = getattr(analysis, f"{branch}_claims")
    return {item.claim_id: item for item in branch_claims.claim_survivals}


def analysis_response_from_artifacts(artifacts: ClaimAnalysisArtifacts) -> AnalysisResponse:
    """Project frozen semantic artifacts into the public MVP API contract."""
    analysis = artifacts.analysis
    a_by_id = _survivals_by_id(analysis, "branch_a")
    b_by_id = _survivals_by_id(analysis, "branch_b")
    combined_by_id = _survivals_by_id(analysis, "combined")
    base_by_id = {claim.claim_id: claim for claim in analysis.base_claims}

    claims: list[ClaimResponse] = []
    for interaction in analysis.interactions:
        claim_id = interaction.claim_id
        if claim_id not in a_by_id or claim_id not in b_by_id or claim_id not in combined_by_id:
            continue
        base_claim = base_by_id.get(claim_id)
        claims.append(
            ClaimResponse(
                claim_id=claim_id,
                claim=interaction.claim_meaning,
                claim_type=interaction.claim_type.value,
                importance=interaction.claim_importance.value,
                base_evidence=(
                    [
                        EvidenceResponse(**item.model_dump())
                        for item in [*base_claim.evidence_regions, *base_claim.equivalents]
                    ]
                    if base_claim is not None
                    else []
                ),
                branch_a=_effect(a_by_id[claim_id]),
                branch_b=_effect(b_by_id[claim_id]),
                combined=_effect(combined_by_id[claim_id]),
                interaction=interaction.interaction.value,
                deterministic_rule=interaction.derivation_reason,
                explanation=interaction.m3_explanation,
            )
        )

    combined_timeline: list[CombinedSliceResponse] = []
    timeline = artifacts.representation.combined.combined_timeline
    if timeline is not None:
        combined_timeline = [
            CombinedSliceResponse(
                base_index=item.base_index,
                start=item.base_range.start,
                end=item.base_range.end,
                verdict=item.verdict,
                text=item.combined_text,
                reason=item.reason,
            )
            for item in timeline.slices
        ]

    interaction_value = analysis.overall_interaction.value
    conflict_detected = interaction_value != "none"
    if interaction_value == "creates_new_conflict":
        summary = (
            "The branches are individually non-breaking, but together remove or break a BASE claim."
        )
    elif interaction_value == "amplifies_existing_issue":
        summary = "The combined edits make an existing semantic issue materially worse."
    else:
        summary = "No new semantic conflict was detected between these edits."
    return AnalysisResponse(
        conflict_detected=conflict_detected,
        interaction=interaction_value,
        overall_impact=analysis.overall_impact.value,
        overall_confidence=analysis.overall_confidence,
        summary=summary,
        model=artifacts.model,
        claims=claims,
        combined_timeline=combined_timeline,
    )


def run_analysis(base_path: Path, branch_a_path: Path, branch_b_path: Path) -> AnalysisResponse:
    """Run the existing media, alignment, composition, and semantic pipeline."""
    base = process_video(base_path)
    branch_a = process_video(branch_a_path)
    branch_b = process_video(branch_b_path)
    client = MiniMaxClient()
    artifacts = analyze_claims(base=base, branch_a=branch_a, branch_b=branch_b, client=client)
    return analysis_response_from_artifacts(artifacts)


async def _save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> None:
    filename = upload.filename or ""
    if Path(filename).suffix.lower() != ".mp4":
        raise HTTPException(status_code=415, detail=f"{filename or 'Upload'} must be an MP4 file.")

    size = 0
    with destination.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(status_code=413, detail=f"{filename} exceeds the upload limit.")
            output.write(chunk)
    await upload.close()
    if size == 0:
        raise HTTPException(status_code=400, detail=f"{filename} is empty.")


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_uploads(
    base: Annotated[UploadFile, File(description="Original BASE MP4")],
    branch_a: Annotated[UploadFile, File(description="Branch A MP4")],
    branch_b: Annotated[UploadFile, File(description="Branch B MP4")],
) -> AnalysisResponse:
    """Analyze one BASE/A/B video triplet synchronously for the local MVP."""
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.max_upload_mb * 1024 * 1024

    try:
        with tempfile.TemporaryDirectory(prefix="mergecut-", dir=settings.upload_dir) as tmp:
            workdir = Path(tmp)
            paths = {
                "base": workdir / "base.mp4",
                "branch_a": workdir / "branch_a.mp4",
                "branch_b": workdir / "branch_b.mp4",
            }
            await _save_upload(base, paths["base"], max_bytes)
            await _save_upload(branch_a, paths["branch_a"], max_bytes)
            await _save_upload(branch_b, paths["branch_b"], max_bytes)
            return await run_in_threadpool(
                run_analysis,
                paths["base"],
                paths["branch_a"],
                paths["branch_b"],
            )
    except HTTPException:
        raise
    except MiniMaxError as exc:
        logger.warning("MiniMax analysis failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="MiniMax M3 analysis failed. Check GMI Cloud configuration and retry.",
        ) from exc
    except MediaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - return a stable public error contract
        logger.exception("Unexpected end-to-end analysis failure")
        raise HTTPException(
            status_code=500,
            detail="Analysis failed while processing the uploaded videos.",
        ) from exc


__all__ = ["AnalysisResponse", "analysis_response_from_artifacts", "router", "run_analysis"]
