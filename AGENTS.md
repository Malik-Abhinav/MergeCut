# MergeCut Agent Instructions

Read `PROJECT_PLAN.md` before making architectural changes.

## Competition constraints

- Use MiniMax M3 or MiniMax M2.7 through GMI Cloud for AI-assisted coding.
- Use MiniMax M3 through GMI Cloud for the application's core semantic reasoning.
- Do not silently substitute another model/provider.
- Keep the repository public during judging.

## Development rules

- Work phase-by-phase according to `PROJECT_PLAN.md` §29.
- Do not build later phases until current acceptance criteria pass.
- Run tests after meaningful changes.
- Keep commits small.
- Do not fabricate benchmarks or metrics.
- Prefer deterministic code for video processing.
- Use M3 for semantic reasoning.
- Do not turn MergeCut into a full video editor.
- Record the active provider/model in `docs/build-log.md` at the start of every
  significant session (rule 11 of §38).

## Repo conventions

### Layout

```
backend/        FastAPI + Pydantic + MiniMax client (Python 3.12 via uv)
frontend/       Next.js shell (real UI lands in Phase 8)
scripts/        Operational scripts: ffmpeg check, spike runner
evaluation/     Datasets and the eval runner (lands in Phase 5)
docs/           build-log.md, architecture.md, limitations.md
data/           uploads/ + derived/ — gitignored, created on demand
```

### Local environment

- Python is managed via `uv` and pinned to **3.12** in `backend/pyproject.toml`
  (system Python3 may be older).
- Node 18+ for the frontend.
- FFmpeg / ffprobe must be on `PATH` (verified by `make check-ffmpeg`).
- API keys live in `.env` (copy `.env.example`, **do not commit**).

### Common commands

| Goal                        | Command                                       |
|-----------------------------|-----------------------------------------------|
| First-time backend setup    | `make backend-install`                        |
| Run tests                   | `make test`                                   |
| Run linters                 | `make lint`                                   |
| Verify FFmpeg               | `make check-ffmpeg`                           |
| Smoke check                 | `make smoke`                                  |
| Phase 1 spike (live M3)     | `make spike`                                  |
| Phase 1 spike dry-run       | `make spike-dry`                              |
| Phase 2 media pipeline test | `make media-smoke`                            |
| Frontend install            | `make frontend-install`                       |
| All offline Phase 0 checks  | `make verify`                                 |

### Phase 0 acceptance

```bash
make test    # green
make lint    # green
```

### Phase 2 acceptance

```bash
make test         # all unit tests, including media pipeline
make media-smoke  # builds controlled fixtures + runs pipeline end-to-end
```

The first call to the ASR pass will download the chosen whisper
model (default `base`, ~150 MB). Subsequent runs hit the local
cache.

Do not start Phase 3 until the Phase 2 acceptance criteria pass.