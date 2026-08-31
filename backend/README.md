# Backend (Python)

This package implements the MergeCut backend: a FastAPI service that analyzes
two independently edited video branches against a common BASE video and flags
cross-edit semantic conflicts using MiniMax M3 (via GMI Cloud).

See `../PROJECT_PLAN.md` for the full plan.

## Local setup

```bash
# From repo root
uv sync --project backend
make -C backend install-dev   # or: cd backend && uv sync --extra dev
make smoke                    # checks Python deps, env, FFmpeg, GMI reachability
make test
```

## Layout (Phase 0 + Phase 1 only)

```
backend/app/
  main.py              # FastAPI app entrypoint
  config.py            # Settings (env, paths, GMI config)
  api/                 # HTTP routers (Phase 0: /health only)
  services/minimax/    # M3 client + prompts + schemas (Phase 1 spike)
  models/              # Pydantic models (semantic analysis schema lives here)
```

Phases 2+ (media, alignment, merge planner, frontend, etc.) are deliberately
not built yet — see PROJECT_PLAN §29.