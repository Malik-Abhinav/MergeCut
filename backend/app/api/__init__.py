"""HTTP routers (Phase 0: meta only).

Future routers — projects, analyze, resolve, media — are deliberately
deferred to their respective phases per PROJECT_PLAN §29.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"pong": "mergecut"}
