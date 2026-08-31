"""FastAPI application entrypoint (Phase 0).

Only a health endpoint is wired up at this stage. Real routes for upload,
analysis, resolution, and verification are introduced in later phases per
PROJECT_PLAN §29.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(
    title="MergeCut API",
    version="0.1.0",
    description=(
        "Semantic three-way merge analyzer for video. "
        "Built for MiniMax Week using MiniMax M3 through GMI Cloud."
    ),
)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe used by `make smoke` and external monitors."""
    settings = get_settings()
    return {
        "status": "ok",
        "model": settings.minimax_m3_model,
        "provider": "GMI Cloud",
    }


@app.get("/", tags=["meta"], include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": "mergecut", "phase": "0"}
