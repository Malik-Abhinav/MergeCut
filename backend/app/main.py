"""FastAPI application entrypoint for the MergeCut MVP."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analyze import router as analyze_router
from app.config import get_settings

app = FastAPI(
    title="MergeCut API",
    version="0.1.0",
    description=(
        "Semantic three-way merge analyzer for video. "
        "Built for MiniMax Week using MiniMax M3 through GMI Cloud."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(analyze_router)


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
    return {"service": "mergecut", "status": "mvp"}
