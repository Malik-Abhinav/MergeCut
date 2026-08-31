# MergeCut Architecture

Status: only the modules required for the phase in progress exist.
Per PROJECT_PLAN §29, later phases are deliberately deferred.

## Architectural decision: two-axis semantic taxonomy

The Phase 1 v2 spike demonstrated that MiniMax M3 can identify the
core cross-edit semantic interaction, but it also exposed instability
in the **binary** safe/unsafe taxonomy for semantically
degraded-but-not-broken branches (fixtures 03 and 08 in the v2.1.0
report sat on a strict-vs-loose interpretation of the decision rule).

The final semantic model will therefore distinguish **semantic
preservation** from **cross-edit interaction**:

### Axis 1 — impact_level

How much of BASE's meaning survives in the (branch or combined)
result:

- `preserved` — the result communicates everything BASE communicated
  (modulo non-meaning changes like timing, pacing, style).
- `degraded` — the result communicates most of BASE's meaning but
  has softened, narrowed, or otherwise weakened at least one claim
  (qualifier narrowed, scope tightened, a procedure made less
  precise).
- `broken` — the result has dropped or contradicted at least one
  required claim (a prerequisite, exception, causal link, scope
  limitation, or instruction no longer reaches the viewer in any
  form).

### Axis 2 — cross_edit_interaction

How the two branches relate to each other:

- `none` — applying both branches together yields the same impact
  as the worse of the two branches in isolation.
- `amplifies_existing_issue` — both branches independently degrade
  or break the same aspect of the message; combined is materially
  worse than either alone.
- `creates_new_conflict` — both branches individually preserve
  meaning, but combined they break it. This is the canonical
  MergeCut scenario.

### Critical MergeCut condition

> A and B together introduce a materially worse semantic state than
> either branch independently.

This is exactly the `creates_new_conflict` interaction with the
quality pattern:

- A: `preserved`
- B: `preserved`
- A+B: `broken`

The other cases are still interesting but are not the load-bearing
MergeCut differentiator.

### Implementation phasing

This taxonomy will be implemented and benchmarked during the
semantic-analysis / evaluation phases (Phase 4 / Phase 5). It is
**not** implemented during Phase 2 (media preprocessing) unless an
existing type dependency forces it; the Phase 2 deliverables are
video metadata + shots + transcripts + keyframes only.

---

## Phase 0 — repo bootstrap

- `backend/` is a Python 3.12 project managed with `uv` (`pyproject.toml`).
- `frontend/` is a Next.js 14 shell with a single placeholder page.
- `scripts/check_ffmpeg.py` verifies FFmpeg/ffprobe are present and reports
  versions.
- `Makefile` wires `test`, `lint`, `check-ffmpeg`, `smoke`, `verify` targets.

## Phase 1 — MiniMax M3 capability spike (text-only)

- `backend/app/services/minimax/client.py` is the *only* module that talks to
  GMI Cloud. Identifiers are read from `app.config.Settings` and never
  hard-coded.
- `backend/app/services/minimax/prompts.py` holds the v2.1.0 system intent and
  user-payload template that implements the user's decision rule verbatim
  (PROJECT_PLAN §15 + the v2 rule for per-branch safety against the FULL
  reconstructed branch content), plus the repair instruction used after a
  schema-validation failure (§14.4).
- `backend/app/services/minimax/branch_view.py` reconstructs the FULL
  branch content (BASE minus the branch's edit) so M3 can apply the
  per-branch decision rule.
- `backend/app/models/semantic.py` defines the strict Pydantic schema for
  the v1 binary output: conflict types, severities, evidence, branch
  safety, and the overall result. (Phase 4 / 5 will extend this with the
  two-axis taxonomy above; the v1 schema is intentionally minimal so
  Phase 2 / 3 / 6 work can proceed.)
- `backend/app/services/minimax/schemas.py` orchestrates one M3 call with
  one repair retry, then returns the validated `SemanticAnalysisResult`.
- `backend/tests/fixtures/spike_fixtures.py` defines eight text-only
  fixtures. Expected labels and per-branch booleans live in the dataclass
  and are *never* serialized into the payload sent to M3.
- `scripts/run_spike.py` runs the eight fixtures through M3, persists raw
  responses + validated verdicts under `data/derived/`, and exits with
  0 / 2 / 3 to signal GO / INVESTIGATE / STOP.

## Phase 2 — media preprocessing (this entry)

- `backend/app/services/media/normalize.py` — runs ffprobe on the
  upload, extracts metadata (duration, resolution, fps, codec,
  audio presence), and produces a normalized working MP4 in the
  derived directory when the input needs it.
- `backend/app/services/media/audio.py` — extracts mono 16 kHz WAV
  audio for the ASR step.
- `backend/app/services/media/scenes.py` — runs PySceneDetect's
  ContentDetector to produce coarse shot boundaries.
- `backend/app/services/media/keyframes.py` — picks one
  representative frame per shot (midpoint frame, decoded through
  OpenCV) and writes it as JPEG to the derived directory.
- `backend/app/services/media/transcript.py` — runs
  `faster-whisper` over the extracted audio to produce timestamped
  segments with start/end and text.
- `backend/app/services/media/pipeline.py` — orchestrates the
  above steps in a single `process_video(path) -> VideoRepresentation`
  call, with content-hash-based caching so re-processing the same
  upload is a no-op.
- `backend/app/models/media.py` — Pydantic models for the
  `VideoRepresentation`, `Shot`, and `TranscriptSegment` outputs.
- `backend/tests/unit/test_media_pipeline.py` — unit tests for each
  pipeline step (mocked FFmpeg where appropriate) plus a fixture
  builder that generates small controlled MP4s.

The Phase 2 pipeline is **deterministic** except for the
faster-whisper ASR pass (which is reproducible up to numerical
precision across runs) and the FFmpeg normalization (which is
deterministic given fixed codec arguments). PySceneDetect
ContentDetector is deterministic given a fixed threshold.

## Deliberately omitted (later phases)

- Mechanical alignment, shot fingerprints, weighted similarity matcher
  (Phase 3).
- Semantic analyzer orchestration beyond the single Phase 1 call
  (Phase 4 — will introduce the two-axis taxonomy above).
- Evaluation dataset + metric reporting (Phase 5).
- Merge planner, FFmpeg render, verifier (Phase 6 / 7).
- Real frontend upload / timeline / conflict UI (Phase 8).