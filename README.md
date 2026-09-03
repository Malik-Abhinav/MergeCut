# MergeCut

MergeCut catches when two video edits that are safe on their own combine to change what the video says.

Traditional merge tools can tell when two branches touch the same timeline object. MergeCut analyzes the rendered audiovisual content of an original video (`BASE`) and two independently edited versions (`Branch A` and `Branch B`) to find cross-edit meaning conflicts—even when the edits occur at different timestamps.

## The demo case

The original hardware tutorial communicates an unplug-before-opening prerequisite twice. Branch A removes one expression; Branch B changes the other. Each cut still carries the prerequisite alone, but the composed result does not.

MergeCut reports:

```text
SEMANTIC CONFLICT DETECTED

Branch A: preserved
Branch B: preserved
Combined: broken
Interaction: creates_new_conflict
```

The result includes the affected BASE claim, the effect of each branch, the Combined effect, timestamped evidence, the deterministic rule that fired, and MiniMax M3 confidence.

## How it works

```text
BASE + Branch A + Branch B
          │
          ▼
FFmpeg normalization + faster-whisper transcription
          │
          ▼
BASE↔branch shot alignment and edit reconstruction
          │
          ▼
Provenance-aware, BASE-anchored Combined representation
          │
          ▼
MiniMax M3 claim extraction + preservation analysis
          │
          ▼
Deterministic cross-edit interaction rule
          │
          ▼
Conflict/no-conflict result with evidence
```

Deterministic processing owns media normalization, alignment, provenance composition, and final interaction derivation. MiniMax M3 performs BASE claim extraction and claim-preservation reasoning. M3 is served through GMI Cloud; it does not directly choose the final cross-edit interaction.

## Evaluation

The final Phase 4 controlled benchmark used four successful MiniMax M3 evaluations per fixture:

> 8/8 controlled fixtures were modal-correct across 32 successful evaluations.

- Canonical conflict: 4/4
- Safe run-level accuracy: 27/28
- Modal false positives: 0
- False negatives: 0

These are results on a small controlled fixture set, not a claim of general 100% accuracy.

## Run locally

Requirements:

- Python 3.12 managed by `uv`
- Node.js 18+
- FFmpeg and ffprobe on `PATH`
- A GMI Cloud API key with access to `MiniMaxAI/MiniMax-M3`

Install and configure:

```bash
cp .env.example .env
# Set GMI_API_KEY in .env

make backend-install
make frontend-install
make check-ffmpeg
```

Start the API from the repository root so `.env` is loaded:

```bash
backend/.venv/bin/uvicorn app.main:app --app-dir backend --reload --port 8000
```

In another terminal:

```bash
cd frontend
npm run dev
```

Open [http://localhost:3000](http://localhost:3000), select three MP4 files, and click **Analyze merge**. The frontend uses `http://localhost:8000` by default; override it with `NEXT_PUBLIC_API_URL` when needed.

## API

`POST /api/analyze` accepts multipart fields:

- `base`: original MP4
- `branch_a`: first edited MP4
- `branch_b`: second edited MP4

The response contains:

```json
{
  "conflict_detected": true,
  "interaction": "creates_new_conflict",
  "overall_impact": "broken",
  "overall_confidence": 0.93,
  "summary": "...",
  "provider": "GMI Cloud",
  "model": "MiniMaxAI/MiniMax-M3",
  "claims": [
    {
      "claim_id": "C1",
      "claim": "The device must be unplugged before opening it.",
      "claim_type": "prerequisite",
      "importance": "critical",
      "base_evidence": [{ "start": 0, "end": 7.5, "description": "..." }],
      "branch_a": { "status": "preserved", "rationale": "...", "evidence": [] },
      "branch_b": { "status": "preserved", "rationale": "...", "evidence": [] },
      "combined": { "status": "broken", "rationale": "...", "evidence": [] },
      "interaction": "creates_new_conflict",
      "deterministic_rule": "R1: ...",
      "explanation": "..."
    }
  ],
  "combined_timeline": []
}
```

The MVP endpoint is synchronous: the HTTP request stays open while preprocessing and M3 analysis run. Invalid media, upload limits, missing provider configuration, and provider failures return structured HTTP errors.

## Tests

```bash
make test
make lint

cd frontend
npm run typecheck
npm run lint
npm run build
```

Normal tests do not make live GMI calls. The controlled live evaluation is separate from the unit suite.

## Known limitations

- Same-source videos only; unrelated or heavily re-shot inputs are unsupported.
- MP4 input, English speech, and short videos are the tested MVP path.
- Alignment operates at shot/sentence granularity, not frame-perfect edit boundaries.
- ASR errors can change downstream semantic results.
- MiniMax M3 analysis can produce false positives, false negatives, or run-to-run variance.
- The controlled benchmark is small and synthetic; it does not establish production accuracy.
- Upload processing is synchronous and local; there is no job queue, persistence, or multi-user isolation beyond per-request temporary files.
- The Combined timeline is analyzed and displayed, but this MVP does not render a final merged video.
- MergeCut does not establish factual truth, detect deepfakes, replace project-file version control, or act as a full video editor.

## Competition disclosure

MergeCut was built for MiniMax Week × GMI Cloud. Its core semantic reasoning uses MiniMax M3 through GMI Cloud. Supporting media processing uses deterministic Python/FFmpeg tooling and faster-whisper ASR.
