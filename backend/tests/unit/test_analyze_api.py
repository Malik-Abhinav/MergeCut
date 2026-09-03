"""Contract tests for the minimal end-to-end analysis endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.analyze import AnalysisResponse
from app.main import app
from app.services.minimax.client import MiniMaxError


def _response(*, conflict: bool = True) -> AnalysisResponse:
    interaction = "creates_new_conflict" if conflict else "none"
    return AnalysisResponse.model_validate(
        {
            "conflict_detected": conflict,
            "interaction": interaction,
            "overall_impact": "broken" if conflict else "preserved",
            "overall_confidence": 0.94,
            "summary": "A concise result.",
            "provider": "GMI Cloud",
            "model": "MiniMaxAI/MiniMax-M3",
            "claims": [
                {
                    "claim_id": "C1",
                    "claim": "Unplug the device before opening it.",
                    "claim_type": "prerequisite",
                    "importance": "critical",
                    "base_evidence": [
                        {"start": 0.0, "end": 7.5, "description": "BASE prerequisite."}
                    ],
                    "branch_a": {
                        "status": "preserved",
                        "rationale": "The reminder survives.",
                        "evidence": [{"start": 7.5, "end": 15.0, "description": "Once unplugged."}],
                    },
                    "branch_b": {
                        "status": "preserved",
                        "rationale": "The first instruction survives.",
                        "evidence": [{"start": 0.0, "end": 7.5, "description": "Unplug first."}],
                    },
                    "combined": {
                        "status": "broken",
                        "rationale": "No prerequisite survives.",
                        "evidence": [],
                    },
                    "interaction": interaction,
                    "deterministic_rule": "R1",
                    "explanation": "The edits jointly remove the prerequisite.",
                }
            ],
            "combined_timeline": [],
        }
    )


def _files() -> dict[str, tuple[str, bytes, str]]:
    return {
        "base": ("base.mp4", b"base-video", "video/mp4"),
        "branch_a": ("a.mp4", b"a-video", "video/mp4"),
        "branch_b": ("b.mp4", b"b-video", "video/mp4"),
    }


def test_analyze_accepts_three_mp4s_and_returns_contract(monkeypatch) -> None:
    seen: dict[str, bytes] = {}

    def fake_run(base: Path, branch_a: Path, branch_b: Path) -> AnalysisResponse:
        seen.update(
            base=base.read_bytes(), branch_a=branch_a.read_bytes(), branch_b=branch_b.read_bytes()
        )
        return _response()

    monkeypatch.setattr("app.api.analyze.run_analysis", fake_run)
    response = TestClient(app).post("/api/analyze", files=_files())

    assert response.status_code == 200
    assert seen == {"base": b"base-video", "branch_a": b"a-video", "branch_b": b"b-video"}
    payload = response.json()
    assert payload["conflict_detected"] is True
    assert payload["interaction"] == "creates_new_conflict"
    assert payload["claims"][0]["branch_a"]["status"] == "preserved"
    assert payload["claims"][0]["combined"]["status"] == "broken"


def test_analyze_rejects_non_mp4_upload() -> None:
    files = _files()
    files["branch_b"] = ("notes.txt", b"not-video", "text/plain")
    response = TestClient(app).post("/api/analyze", files=files)

    assert response.status_code == 415
    assert "must be an MP4" in response.json()["detail"]


def test_analyze_surfaces_provider_failure(monkeypatch) -> None:
    def fail(*_args: Path) -> AnalysisResponse:
        raise MiniMaxError("GMI Cloud 503")

    monkeypatch.setattr("app.api.analyze.run_analysis", fail)
    response = TestClient(app).post("/api/analyze", files=_files())

    assert response.status_code == 502
    assert "MiniMax M3 analysis failed" in response.json()["detail"]


def test_analyze_requires_all_three_uploads() -> None:
    files = _files()
    del files["branch_b"]
    response = TestClient(app).post("/api/analyze", files=files)

    assert response.status_code == 422
