# MergeCut — Project Plan

## 0. Project Status

Project: MergeCut  
Competition: MiniMax Week × GMI Cloud  
Track: Synthesis  
Primary model: MiniMax M3 through GMI Cloud  
Optional model: MiniMax Speech 2.8 through GMI Cloud  
Build window: Finish competition-ready version before September 6, 2026  
Repository: Public before submission  
Demo: Maximum 3 minutes  
Core competition constraint: Core generation must run on MiniMax models served through GMI Cloud. For conservative competition compliance, all AI-assisted coding used to build MergeCut should also be performed with MiniMax M3 or MiniMax M2.7 served through the user's GMI Cloud API key. Coding shells such as Codex CLI, Claude Code, Cursor, OpenCode, Hermes, or Trae are interfaces only and must not silently route coding generations to non-MiniMax models when working on the competition submission.

---

# 0.1 Competition Compliance — Non-Negotiable

Official MiniMax Week requirements that affect implementation:

- The project must be built during the fourteen-day campaign window.
- One project may be submitted to one track only.
- The repository must remain public for the full judging period.
- Core generation must use MiniMax models served through GMI Cloud.
- Supporting infrastructure may come from elsewhere.
- The submission form requires the MiniMax models used to be declared.
- The demo video must be three minutes or less.
- Public sharing on X is part of the entry.
- The demo must be shared on X and tag MiniMax and GMI Cloud.
- The submission form requires an X handle, public repository link, and demo-video link.

## Coding-model policy for this repository

For this competition repository, use this conservative rule:

> Any AI agent that writes, edits, debugs, refactors, or reviews competition code must use MiniMax M3 or MiniMax M2.7 through GMI Cloud.

Allowed interfaces include tools such as:

- OpenCode
- Hermes
- Trae
- Codex CLI configured with a GMI custom provider
- Claude Code configured with the GMI key/provider
- Cursor configured with the GMI key/provider

The interface is not the model.

Example:

```text
Codex CLI UI
     |
     v
GMI Cloud OpenAI-compatible endpoint
     |
     v
MiniMax M3
```

This is acceptable for the project policy.

This is not:

```text
Codex CLI
     |
     v
OpenAI GPT model
```

Do not use a non-MiniMax coding model for repository generation and later describe the project as MiniMax-generated.

## Recommended coding split

Use:

- MiniMax M3 for architecture, difficult debugging, semantic reasoning, alignment design, evaluation analysis, and final review.
- MiniMax M2.7 for routine implementation, tests, refactors, API wiring, frontend work, and repetitive coding tasks.

Both must be served through the GMI Cloud key used for the campaign.


---

# 1. One-Sentence Product

MergeCut catches cases where two individually reasonable edits to the same source video combine into a final video that changes, contradicts, weakens, or removes the original meaning.

It analyzes the rendered content itself, not just an editor timeline or project metadata.

---

# 2. The Problem

Traditional merge systems detect structural conflicts.

Example:

- Branch A deletes a video clip.
- Branch B modifies that same clip.
- A timeline merge system sees both branches touching the same asset and flags a conflict.

That is useful, but it does not catch a different class of failure.

Example source video:

> “Before opening the device, unplug it from the wall.”

Later:

> “Once the device is unplugged, lift the cover.”

Branch A removes the first sentence.

Branch B independently changes the second sentence to:

> “Lift the cover.”

Each branch by itself still communicates the unplugging prerequisite.

But after combining both edits, the final video no longer tells the viewer to unplug the device before opening it.

The edits occur in different parts of the video. They can merge mechanically without any overlapping timeline changes.

The failure is semantic.

MergeCut's core task is to detect that kind of conflict.

---

# 3. What MergeCut Is

MergeCut is a semantic three-way analyzer and constrained merge engine for videos derived from the same source.

Inputs:

1. BASE video
2. BRANCH A video
3. BRANCH B video

Outputs:

1. Mechanical edit diff for A vs BASE
2. Mechanical edit diff for B vs BASE
3. Meaning-level analysis of each branch
4. Cross-branch semantic conflict detection
5. Timestamped evidence explaining each conflict
6. Resolution options
7. A merged output video for supported edits
8. A final independent verification pass over the rendered merge

Conceptual pipeline:

```text
                 BASE
                /    \
               /      \
        BRANCH A      BRANCH B
              \        /
               \      /
          Shot-level alignment
                  |
          Mechanical edit graph
                  |
            MiniMax M3
          semantic analysis
                  |
          Cross-edit conflicts
                  |
           Resolution plan
                  |
               FFmpeg
                  |
          final-merged.mp4
                  |
        Independent M3 verifier
```

---

# 4. What MergeCut Is NOT

Do not build any of the following during the competition:

- A full video editor
- A replacement for DaVinci Resolve, Premiere, Descript, or Diffusion Studio
- General Git/version control for video project files
- Frame-perfect arbitrary MP4 reverse engineering
- A deepfake detector
- A factual truth detector
- A general misinformation detector
- A social-media scraping product
- Multi-user real-time collaboration
- Arbitrary After Effects/VFX merge support
- A system that promises perfect reconstruction of any editing operation

The project succeeds if it detects meaning-level conflicts across independently edited versions and renders a correct final output for a bounded set of edits.

---

# 5. Positioning and Differentiation

## 5.1 Primary positioning

Do not lead with:

> “Git for video.”

That phrase is already associated with tools that version editing project state.

Lead with:

> “MergeCut catches when two edits that are safe on their own combine to change what a video says.”

Alternative:

> “A semantic merge checker for video.”

Alternative:

> “Merge the meaning, not just the timeline.”

## 5.2 Difference from timeline/project-file version control

Existing tools such as Vit operate on structured editing state and project metadata.

MergeCut operates on rendered audiovisual content.

The distinction:

```text
Traditional merge:
“Did both branches modify the same timeline object?”

MergeCut:
“Did these two changes interact in meaning even if they occurred in different places?”
```

Example:

```text
Timeline merge:
✓ No overlapping edit
✓ Project still renders
✓ Audio still synchronized

MergeCut:
✗ Semantic conflict

Branch A removed the prerequisite.
Branch B removed the later reminder.
The merged video no longer communicates the prerequisite.
```

This distinction must remain load-bearing throughout the implementation and README.

## 5.3 Difference from Diffusion Studio

Diffusion Studio is an agent-oriented programmable video editor.

MergeCut is not competing with its editor.

MergeCut analyzes the relationship among BASE, A, and B and detects cross-edit semantic interactions.

Diffusion Studio or OpenTimelineIO may eventually be useful infrastructure, but they are not required for the MVP.

---

# 6. Target User

Primary demo user:

- Video creator or editor collaborating on multiple cuts of the same source.

Other plausible users:

- Training-video teams
- Documentation teams
- Localization teams
- Compliance/review teams
- Educational-video creators
- Product-demo teams
- Interview/podcast editors
- Safety/instructional-video teams

Do not build specialized workflows for each user during the hackathon.

---

# 7. Competition Thesis

The Synthesis track rewards reasoning connected to video.

MergeCut creates a full video → reasoning → video loop:

```text
BASE + A + B video
        |
        v
MiniMax M3 understands content and changes
        |
        v
M3 reasons about semantic interactions
        |
        v
Structured merge plan
        |
        v
FFmpeg renders merged video
        |
        v
Fresh MiniMax M3 pass watches final result
        |
        v
Verification report
```

MiniMax M3 is not used as a generic chatbot.

It performs:

- audiovisual understanding
- cross-version comparison
- long-range dependency reasoning
- qualifier tracking
- temporal reasoning
- causal/precondition reasoning
- cross-edit conflict detection
- structured evidence generation
- merge-plan reasoning
- final-video verification

This is the core MiniMax usage story for judges.

---

# 8. Scope for the Competition MVP

## 8.1 Input constraints

Support:

- MP4
- H.264 video preferred
- AAC audio preferred
- Same underlying source video
- Maximum recommended demo length: 1–3 minutes
- English spoken content for MVP
- Two branches only: A and B
- Clear cuts/trims/replacements
- Shot-level or sentence-level changes

Reject or warn on:

- Videos from unrelated sources
- Heavy VFX changes
- Completely re-shot versions
- Extreme crop/rotation transforms
- Long-form videos beyond configured limit
- More than two branches
- Unsupported codecs if FFmpeg cannot normalize them

## 8.2 Supported edit operations for MVP

Required:

1. Delete a segment
2. Trim a segment
3. Replace a segment
4. Change spoken wording in a segment
5. Change captions/text content if detectable
6. Preserve unchanged segments

Good stretch goal:

7. Reorder complete shots
8. Insert a new short segment

Do not support arbitrary transition/effect/color merge semantics.

## 8.3 Required semantic conflict categories

Start with these:

### A. Prerequisite loss

One edit removes the initial rule. Another removes the later reference. Together a prerequisite disappears.

### B. Qualifier loss

BASE:

> “I wouldn't recommend this for production systems.”

Later:

> “For prototypes, though, it works well.”

A and B remove different qualifiers and create an overly broad statement.

### C. Exception loss

BASE establishes a general rule and an exception. Two edits jointly eliminate the exception.

### D. Temporal-scope change

BASE distinguishes initially, later, first attempt, or final attempt. Two edits jointly collapse the time distinction.

### E. Cause/effect break

One edit removes the cause. Another rewrites the consequence as though it were unconditional.

### F. Entity/scope change

BASE limits a statement to a particular group, object, or condition. Combined edits make it appear universal.

### G. Narrative dependency break

One edit removes setup. Another removes downstream clarification. Final sequence becomes misleading or incoherent.

Use an enum similar to:

```python
SemanticConflictType = Literal[
    "prerequisite_loss",
    "qualifier_loss",
    "exception_loss",
    "temporal_scope_change",
    "causal_dependency_break",
    "entity_scope_change",
    "narrative_dependency_break",
    "contradiction",
    "other"
]
```

---

# 9. Success Criteria

The project is competition-ready when all of these are true.

## Core

- User uploads BASE, A, and B.
- System confirms they share the same source sufficiently for analysis.
- System produces a shot/sentence-level mechanical diff.
- System identifies changed regions.
- MiniMax M3 receives the relevant changed content and surrounding context.
- M3 judges A independently.
- M3 judges B independently.
- M3 judges A+B together.
- System flags meaning-level conflicts.
- Every conflict includes timestamped evidence.
- Safe controls are not consistently over-flagged.
- User can select a resolution.
- System renders a final merged MP4 for supported operations.
- A fresh M3 call verifies the final rendered output.

## Evaluation

Minimum:

- 20 labeled merge scenarios
- 10 semantic-conflict cases
- 10 safe-control cases

Preferred:

- 50 labeled scenarios
- 25 conflicts
- 25 controls

Report:

- Accuracy
- Precision
- Recall
- F1
- False-positive rate
- Per-conflict-type breakdown if enough examples exist

Never invent metrics.

## UX

A judge must understand the project within 20 seconds.

The user must see:

- what A changed
- what B changed
- whether each branch is safe
- what interaction becomes unsafe
- exactly where the evidence appears
- what resolution is available
- whether the final rendered merge passes verification

---

# 10. Recommended Tech Stack

## Frontend

- Next.js
- TypeScript
- Tailwind CSS
- Native HTML5 video elements
- Lightweight timeline visualization built with normal React/CSS/SVG

Avoid adding a large canvas/video-editor framework unless absolutely necessary.

## Backend

- Python 3.12
- FastAPI
- Pydantic
- Uvicorn

## Video processing

- FFmpeg / ffprobe
- PySceneDetect
- OpenCV only where needed
- Perceptual hashing library if useful

## Transcript/alignment supporting infrastructure

Preferred options in order:

1. Timestamped transcript obtained from existing audio/transcription tooling available locally
2. faster-whisper as supporting infrastructure
3. M3-generated transcript/semantic segmentation as fallback

The semantic verdict must still come from MiniMax M3 through GMI Cloud.

## Persistence

MVP:

- SQLite for project metadata
- Local filesystem for uploads and derived media

Do not add PostgreSQL, Redis, Kafka, or distributed queues unless there is a demonstrated need.

## Deployment stretch

Frontend:

- Vercel

Backend:

- Railway / Render / Fly.io

Media:

- Local demo first
- Cloudflare R2/S3-compatible object storage only if deployment needs persistent media

Competition does not justify building cloud infrastructure before the core reasoning works.

---

# 11. Repository Structure

Use a simple monorepo.

```text
mergecut/
├── AGENTS.md
├── PROJECT_PLAN.md
├── README.md
├── .env.example
├── .gitignore
├── Makefile
│
├── frontend/
│   ├── package.json
│   └── src/
│       ├── app/
│       ├── components/
│       │   ├── upload/
│       │   ├── timeline/
│       │   ├── conflicts/
│       │   ├── resolution/
│       │   └── verification/
│       ├── lib/
│       └── types/
│
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── api/
│   │   │   ├── projects.py
│   │   │   ├── analyze.py
│   │   │   ├── resolve.py
│   │   │   └── media.py
│   │   ├── models/
│   │   │   ├── project.py
│   │   │   ├── timeline.py
│   │   │   ├── diff.py
│   │   │   ├── semantic.py
│   │   │   └── verification.py
│   │   ├── services/
│   │   │   ├── media/
│   │   │   │   ├── normalize.py
│   │   │   │   ├── scenes.py
│   │   │   │   ├── keyframes.py
│   │   │   │   └── render.py
│   │   │   ├── alignment/
│   │   │   │   ├── transcript.py
│   │   │   │   ├── shots.py
│   │   │   │   └── matcher.py
│   │   │   ├── diff/
│   │   │   │   ├── mechanical.py
│   │   │   │   └── operations.py
│   │   │   ├── minimax/
│   │   │   │   ├── client.py
│   │   │   │   ├── prompts.py
│   │   │   │   ├── schemas.py
│   │   │   │   └── multimodal.py
│   │   │   ├── semantic/
│   │   │   │   ├── analyzer.py
│   │   │   │   ├── conflict_graph.py
│   │   │   │   └── verifier.py
│   │   │   └── merge/
│   │   │       ├── planner.py
│   │   │       └── executor.py
│   │   └── db/
│   │       └── sqlite.py
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── fixtures/
│
├── evaluation/
│   ├── README.md
│   ├── cases/
│   ├── labels/
│   ├── run_eval.py
│   └── metrics.py
│
├── scripts/
│   ├── check_ffmpeg.py
│   ├── create_fixture.py
│   ├── create_textcard_fixture.py
│   └── smoke_test_minimax.py
│
└── docs/
    ├── architecture.md
    ├── limitations.md
    ├── competitor-notes.md
    └── demo-script.md
```

Do not create empty abstraction layers merely to match this tree. Keep only modules that earn their existence.

---

# 12. Core Data Model

## Project

```json
{
  "id": "project_123",
  "base_path": "...",
  "branch_a_path": "...",
  "branch_b_path": "...",
  "status": "uploaded|processing|analyzed|resolved|rendered|verified",
  "created_at": "..."
}
```

## Base segment

```json
{
  "segment_id": "base_07",
  "start": 18.25,
  "end": 24.40,
  "transcript": "Before opening the device, unplug it from the wall.",
  "keyframes": [],
  "semantic_summary": null
}
```

## Mechanical edit operation

```json
{
  "branch": "A",
  "operation": "delete",
  "base_segment_ids": ["base_07"],
  "branch_segment_ids": [],
  "base_start": 18.25,
  "base_end": 24.40,
  "confidence": 0.97
}
```

Or:

```json
{
  "branch": "B",
  "operation": "replace",
  "base_segment_ids": ["base_13"],
  "branch_segment_ids": ["b_11"],
  "base_start": 41.10,
  "base_end": 46.30,
  "before_text": "Once the device is unplugged, lift the cover.",
  "after_text": "Lift the cover.",
  "confidence": 0.93
}
```

## Semantic branch analysis

```json
{
  "branch": "A",
  "meaning_preserved": true,
  "removed_claims": [],
  "modified_claims": [],
  "dependencies_affected": [],
  "evidence": []
}
```

## Cross-edit conflict

```json
{
  "id": "conflict_03",
  "type": "prerequisite_loss",
  "severity": "high",
  "branch_a_edit_ids": ["edit_a_02"],
  "branch_b_edit_ids": ["edit_b_05"],
  "base_claim": "The device must be unplugged before the cover is opened.",
  "branch_a_effect": "Removes the initial explicit instruction to unplug the device.",
  "branch_b_effect": "Removes the later phrase confirming the device is unplugged before opening.",
  "combined_effect": "The merged video no longer communicates the unplugging prerequisite.",
  "branch_a_safe_alone": true,
  "branch_b_safe_alone": true,
  "combined_safe": false,
  "evidence": [
    {
      "video": "base",
      "start": 18.25,
      "end": 24.40,
      "description": "Initial unplug instruction."
    },
    {
      "video": "base",
      "start": 41.10,
      "end": 46.30,
      "description": "Later prerequisite reminder."
    }
  ],
  "confidence": 0.94,
  "recommended_resolution": "Restore one explicit unplug prerequisite."
}
```

## Resolution

```json
{
  "conflict_id": "conflict_03",
  "strategy": "keep_base|prefer_a|prefer_b|custom",
  "selected_segment_source": "base",
  "notes": ""
}
```

## Final verification

```json
{
  "passed": true,
  "preserved_intents": [
    {
      "branch": "A",
      "preserved": true,
      "evidence": []
    },
    {
      "branch": "B",
      "preserved": true,
      "evidence": []
    }
  ],
  "remaining_conflicts": [],
  "new_issues": [],
  "confidence": 0.91
}
```

---

# 13. Mechanical Alignment Strategy

This is the largest engineering risk. Keep it bounded.

## 13.1 Do not begin with frame-perfect alignment

The MVP aligns at:

- scene/shot level
- spoken-sentence level
- coarse timestamp windows

The project does not need to determine whether a cut moved by 83 milliseconds.

## 13.2 Normalize first

For BASE, A, B:

- Normalize video to a common working codec/resolution if needed.
- Extract metadata with ffprobe.
- Extract mono audio track.
- Detect scenes/shots.
- Generate representative keyframes.
- Obtain timestamped transcript.

## 13.3 Shot fingerprints

For every shot:

- Start/end timestamp
- Duration
- Representative frame perceptual hash
- Optional additional frame hashes
- Transcript span
- Normalized transcript tokens

Use these to align branch shots against base shots.

## 13.4 Matching

Create a similarity score combining:

- visual similarity
- transcript similarity
- duration similarity
- relative ordering

Do not over-engineer ML.

A weighted heuristic is sufficient for the competition.

Pseudo-score:

```text
score =
  0.45 * visual_similarity +
  0.40 * transcript_similarity +
  0.10 * duration_similarity +
  0.05 * order_prior
```

Tune against controlled fixtures.

## 13.5 Initial operation classification

From alignment infer:

- unchanged
- deleted
- replaced
- trimmed
- inserted
- moved/reordered, if implemented

The mechanical diff must output confidence.

If confidence is low, mark the region as uncertain rather than hallucinating an operation.

---

# 14. MiniMax M3 Integration

## 14.1 Provider

All core reasoning calls must use MiniMax M3 through GMI Cloud for the competition version.

Keep provider code isolated:

```python
class MiniMaxClient:
    async def analyze_semantic_merge(...): ...
    async def verify_final_merge(...): ...
```

Do not scatter raw HTTP calls across the project.

## 14.2 Input strategy

Preferred:

Send M3 the relevant video clips directly if GMI's M3 endpoint exposes video attachments reliably.

Fallback:

For each relevant region provide:

- timestamped transcript
- 6–12 representative keyframes
- mechanical edit description
- surrounding context
- branch identity

The product must still work through this fallback.

Do not block the project on direct MP4 ingestion.

## 14.3 Context packaging

Do not send entire videos on every call.

Mechanical alignment determines candidate changed regions.

For each semantic-analysis job include:

- BASE relevant region
- BASE preceding context
- BASE following context
- A version of relevant region
- B version of relevant region
- mechanical edit operations
- keyframes/transcripts
- already-extracted relevant claims if available

Long context is useful for cross-video dependencies, but uncontrolled context wastes latency and makes evaluation harder.

## 14.4 Structured output

Require strict structured JSON.

Validate every model result with Pydantic.

If validation fails:

1. Retry once with schema repair instructions.
2. If still invalid, mark analysis failed and expose a useful error.

Never silently parse random prose.

---

# 15. Core M3 Prompt Contract

Treat this as version 1 and version prompts for evaluation.

System intent:

```text
You are the semantic merge analyzer for MergeCut.

You receive an original video context and two independently edited branches derived from it.

A mechanical merge conflict occurs when edits touch the same timeline object.
That is NOT your primary task.

Your task is to detect semantic interactions between edits, especially cases where:
- Branch A is acceptable on its own.
- Branch B is acceptable on its own.
- Applying both changes causes an important meaning, qualifier, prerequisite, exception, causal link, temporal distinction, scope limitation, or instruction to disappear or change.

Do not flag a conflict merely because both branches edited related topics.

Ground every conclusion in supplied audiovisual evidence and timestamps.

Distinguish:
1. branch A safety in isolation
2. branch B safety in isolation
3. combined semantic safety

Return only data matching the requested schema.
```

User payload should describe:

```text
BASE CONTEXT
...

BRANCH A CHANGES
...

BRANCH B CHANGES
...

MECHANICAL DIFF
...

TASK
Analyze each branch independently, then analyze the combined result.
```

Required output fields:

- branch_a_safe
- branch_b_safe
- combined_safe
- conflicts[]
- conflict type
- severity
- base claim
- branch A effect
- branch B effect
- combined effect
- evidence
- confidence
- recommended resolution

Important:

Do not tell M3 in the prompt that a conflict definitely exists.

The evaluation must remain blind.

---

# 16. Semantic Conflict Graph

The key differentiator is detecting interactions between edits that occur in different timeline regions.

Represent each meaningful claim/dependency as a node.

Examples:

```text
C1: Device must be unplugged before cover is opened.
C2: User removes rear screws.
C3: Cover is opened only after power is disconnected.
```

Edits affect claims:

```text
Edit A2 -> weakens C1
Edit B5 -> removes evidence for C3
```

Conflict graph:

```text
A2 ----\
        > C1/C3 dependency -> combined prerequisite loss
B5 ----/
```

This graph does not need sophisticated graph algorithms for MVP.

It is primarily a structured representation that makes cross-timestamp semantic interactions explicit.

---

# 17. Merge Planning

After conflicts are identified, produce a merge plan.

## 17.1 Safe edits

Edits with no mechanical or semantic conflict can be applied automatically.

## 17.2 Conflicting edits

User receives clear options.

For a semantic conflict:

```text
Conflict:
Combined edits remove the unplugging prerequisite.

Options:
1. Restore BASE wording at 00:18–00:24.
2. Keep A, but preserve B's original prerequisite wording at 00:41–00:46.
3. Prefer Branch A.
4. Prefer Branch B.
5. Custom resolution.
```

MVP does not need model-generated creative rewriting.

A deterministic source-selection resolution is safer.

## 17.3 Optional Speech 2.8 stretch feature

If there is time:

- User enters a replacement sentence.
- M3 verifies the replacement semantically resolves the conflict.
- Speech 2.8 generates narration.
- FFmpeg inserts the new narration into the merged clip.

Do not make this required for the core project.

---

# 18. Rendering the Final Merge

Use FFmpeg.

Build a final ordered segment list.

Each segment specifies:

```json
{
  "source_video": "base|a|b",
  "source_start": 12.4,
  "source_end": 18.2
}
```

Process:

1. Cut required source segments.
2. Normalize streams.
3. Concatenate in final order.
4. Re-encode to a standard H.264/AAC MP4.
5. Save `final-merged.mp4`.

MVP resolution should favor whole-shot/sentence segments to reduce audiovisual discontinuities.

If audio/video boundaries create unacceptable artifacts, use short crossfades only where straightforward.

Do not build a general transition engine.

---

# 19. Final Verification

This is a required feature.

Use a fresh M3 request with no access to the earlier model reasoning beyond the intended merge requirements.

Inputs:

- final merged video or frame/transcript representation
- list of intended preserved meanings from BASE
- selected A edits
- selected B edits
- conflicts that were resolved

Questions:

1. Did the final video preserve each selected edit?
2. Did it preserve the required base meaning?
3. Are previously detected semantic conflicts resolved?
4. Did the merge introduce a new contradiction or missing dependency?
5. Is the final sequence understandable?

Return:

- pass/fail
- preserved intent per branch
- remaining conflicts
- new issues
- timestamped evidence
- confidence

This independent verifier makes the project a reasoning loop rather than a one-shot classifier.

---

# 20. Evaluation Dataset

## 20.1 Why evaluation matters

The competition demo proves the product looks good.

The benchmark proves the core idea works.

## 20.2 Fixture strategy

Create controlled short videos programmatically or manually.

Each BASE video should contain multiple related statements.

Generate A and B as independently safe edits.

Half of the cases should create a semantic conflict when combined.

Half should remain safe.

## 20.3 Initial categories

Create at least:

- 4 prerequisite-loss cases
- 4 qualifier-loss cases
- 4 exception-loss cases
- 4 temporal-scope cases
- 4 cause/effect cases
- 4 entity/scope cases
- 4 safe independent-topic controls
- 4 safe redundant-wording controls
- 4 safe structural edits
- 4 hard-negative related-topic cases

Target: 40–50 total if time allows.

## 20.4 Test example

BASE:

> “Premium accounts can export reports.”
>
> Later:
>
> “For premium users, click Export.”

A:

Deletes first statement.

B:

Changes second statement to:

> “Click Export.”

Label:

```json
{
  "branch_a_safe": true,
  "branch_b_safe": true,
  "combined_safe": false,
  "type": "entity_scope_change"
}
```

## 20.5 Hard negative

BASE:

> “Create an account.”
>
> “Choose a profile picture.”
>
> “Enable notifications.”

A:

Deletes profile-picture section.

B:

Rephrases notification section.

Combined label:

Safe.

M3 should not invent a semantic dependency merely because two edits exist.

## 20.6 Metrics

Calculate:

```text
accuracy
precision
recall
F1
false_positive_rate
false_negative_rate
```

If evidence labels are available, additionally measure:

```text
evidence_hit_rate
```

Definition:

Does the model cite at least one ground-truth relevant source region?

Record prompt/model/version for every evaluation run.

---

# 21. API Endpoints

Suggested MVP API.

## Create project

`POST /api/projects`

Multipart:

- base
- branch_a
- branch_b

Response:

```json
{
  "project_id": "...",
  "status": "uploaded"
}
```

## Analyze

`POST /api/projects/{id}/analyze`

Starts:

- normalization
- segmentation
- alignment
- mechanical diff
- semantic analysis

## Status

`GET /api/projects/{id}`

Returns processing stage and progress.

## Results

`GET /api/projects/{id}/analysis`

Returns:

- timelines
- mechanical edits
- semantic conflicts
- evidence

## Resolve

`POST /api/projects/{id}/resolutions`

## Render

`POST /api/projects/{id}/render`

## Verify

`POST /api/projects/{id}/verify`

## Media

`GET /api/projects/{id}/media/{asset}`

For demo/local use.

---

# 22. Frontend UX

## 22.1 Screen 1 — Upload

Headline:

> Catch the conflicts your timeline can't see.

Subtext:

> Upload the original video and two independently edited versions.

Three cards:

- BASE
- BRANCH A
- BRANCH B

Button:

`Analyze merge`

## 22.2 Screen 2 — Analysis

Top summary:

```text
Branch A changes: 4
Branch B changes: 3

Mechanical conflicts: 0
Semantic conflicts: 1
```

This contrast is important.

Show three aligned timelines.

Example:

```text
BASE      [1][2][3][4][5][6]
A         [1][ ][3][4][5][6]
B         [1][2][3][4'][5][6]

                           semantic link
          A delete [2]  ---------  B edit [4]
```

## 22.3 Conflict card

```text
HIGH · PREREQUISITE LOSS

These edits are safe individually but unsafe together.

Branch A
Removed:
“Before opening the device, unplug it.”

Branch B
Changed:
“Once the device is unplugged, lift the cover.”
→
“Lift the cover.”

Combined effect
The final video no longer tells the viewer to unplug the device before opening it.

[View evidence]

Resolution
○ Restore original prerequisite
○ Prefer A
○ Prefer B
○ Custom
```

## 22.4 Render

Button:

`Create merged video`

Show progress.

Then final video.

## 22.5 Verification

```text
FINAL VERIFICATION

✓ Branch A intent preserved
✓ Branch B intent preserved
✓ Required prerequisite preserved
✓ No unresolved semantic conflicts

Confidence: 0.93
```

Do not make the interface crowded.

The judge should understand the conflict without reading a technical explanation.

---

# 23. Demo Scenario

The competition video should use one extremely clear scenario.

Recommended: a fictional hardware tutorial.

BASE:

1. Intro
2. “Before opening the device, unplug it from the wall.”
3. “Remove the rear screws.”
4. “Once the device is unplugged, lift the cover.”
5. Replace part.
6. Close cover.

A:

Deletes statement 2.

B:

Changes statement 4 to:

> “Lift the cover.”

Each branch alone remains understandable.

Combined output loses the prerequisite.

## Demo sequence

0:00–0:15

Problem:

> “Two editors change different parts of a video. Their timelines merge cleanly, but their combined edits can silently change what the video means.”

0:15–0:35

Show BASE, A, B.

0:35–0:55

Click Analyze.

Show:

```text
Mechanical conflicts: 0
Semantic conflicts: 1
```

0:55–1:25

Open conflict. Show M3's explanation and timestamp evidence.

1:25–1:45

Choose `Restore prerequisite`.

1:45–2:10

Render final merge.

2:10–2:30

Show independent verification.

2:30–2:50

Show benchmark with real measured values only.

2:50–3:00

Architecture visual:

```text
Video → M3 reasoning → merge → video → M3 verification
```

Finish.

---

# 24. README Structure

The README must be concise at the top.

## Opening

Suggested:

```text
# MergeCut

MergeCut detects a class of video merge conflict that timeline and project-file version-control systems cannot see: two edits that are safe independently but change the video's meaning when combined.

It analyzes the rendered audiovisual content of BASE, Branch A, and Branch B, identifies cross-edit semantic dependencies with MiniMax M3, helps resolve conflicts, renders the merge, and independently verifies the final video.
```

Then immediately:

## What makes this different?

```text
Timeline conflict:
Both branches modify the same clip.

Semantic conflict:
The branches modify different clips, merge mechanically, but jointly remove or alter an important meaning.
```

Then one screenshot/GIF.

Then:

- How it works
- Architecture
- MiniMax usage
- Evaluation
- Supported edits
- Limitations
- Local setup
- Competition disclosure
- Related work / differentiation

## Related work

Mention relevant adjacent tools factually.

Do not claim:

> “Nobody has ever built anything like this.”

Say:

> “Existing video version-control systems primarily operate on project/timeline state. MergeCut focuses on cross-edit meaning conflicts in rendered audiovisual content.”

That claim is safer and technically meaningful.

---

# 25. Limitations

The README must be explicit.

Initial limitations:

- Same-source videos only
- English-first
- Short videos recommended
- Shot/sentence-level alignment
- Limited edit-operation reconstruction
- No guarantee for arbitrary VFX-heavy edits
- Semantic analysis is model-based and may produce false positives/negatives
- Does not determine whether the source video's claims are factually true
- Does not detect deepfakes
- Does not replace editor project-file version control
- Final merge rendering supports a constrained operation set

Strong limitations improve credibility.

---

# 26. Testing Strategy

## Unit tests

Test:

- ffprobe parsing
- shot representation
- transcript normalization
- similarity scoring
- operation inference
- conflict schema validation
- timeline merge planner
- resolution handling
- FFmpeg command construction

## Integration tests

Fixtures:

- simple deletion
- replacement
- unrelated changes
- semantic conflict fixture
- safe-control fixture
- final render

## Model tests

Do not put live GMI calls in normal unit tests.

Use recorded fixture responses.

Separate:

```bash
pytest
```

from:

```bash
python evaluation/run_eval.py --live
```

## Smoke tests

Provide:

```bash
make smoke
```

Which checks:

- Python dependencies
- Node dependencies
- FFmpeg
- environment variables
- GMI connectivity
- one minimal M3 structured-output request

---

# 26.5 Coding Through GMI Cloud

GMI exposes an OpenAI-compatible inference endpoint.

For all coding work, authenticate with the campaign GMI API key and select either:

```text
MiniMaxAI/MiniMax-M3
MiniMaxAI/MiniMax-M2.7
```

## Recommended low-friction option

Prefer a tool that GMI lists as natively integrated, such as OpenCode or Hermes, if it works reliably in the local environment.

## Codex CLI as an interface

Codex CLI supports user-defined model providers. If Codex CLI is used, configure it as a shell around GMI rather than using the default OpenAI provider.

Conceptual `~/.codex/config.toml`:

```toml
model = "MiniMaxAI/MiniMax-M3"
model_provider = "gmi"

[model_providers.gmi]
name = "GMI Cloud"
base_url = "https://api.gmi-serving.com/v1"
env_key = "GMI_API_KEY"
wire_api = "chat"
requires_openai_auth = false
```

Then:

```bash
export GMI_API_KEY="..."
codex
```

Before accepting generated code, verify the active provider/model shown by the tool or logs.

If the installed Codex version's custom-provider syntax differs, use its current configuration schema rather than guessing.

## M2.7 routine-coding profile

Optionally define a second profile/model selection using:

```text
MiniMaxAI/MiniMax-M2.7
```

Use M2.7 for routine coding and M3 for hard reasoning.

The important condition is that both requests are served by GMI Cloud.

---

# 27. Environment Variables

`.env.example`:

```bash
GMI_API_KEY=
GMI_BASE_URL=
MINIMAX_M3_MODEL=

UPLOAD_DIR=./data/uploads
DERIVED_DIR=./data/derived
DATABASE_PATH=./data/mergecut.db

MAX_VIDEO_SECONDS=180
MAX_UPLOAD_MB=250
```

Do not commit keys.

Keep exact GMI endpoint/model identifiers configurable until verified against current GMI documentation/account access.

---

# 28. Logging and Reproducibility

For every model analysis store:

- project ID
- model name
- prompt version
- request timestamp
- analysis input hashes
- raw structured response
- validated response
- latency
- retry count

Do not log API keys.

For evaluation, this enables reproducible metrics and prompt comparisons.

---

# 29. Build Order

This order is mandatory.

Do not build frontend first.

## Phase 0 — Repository bootstrap

Goal: a runnable backend and basic frontend shell.

Tasks:

- Create repo structure.
- Add Python and Node setup.
- Add `.env.example`.
- Add FFmpeg prerequisite check.
- Add lint/test commands.
- Create `AGENTS.md`.
- Copy this plan into repo as `PROJECT_PLAN.md`.

Acceptance:

```bash
make test
make lint
```

work.

## Phase 1 — MiniMax capability spike

Goal: prove M3 can perform the core semantic task before building infrastructure.

Create 5 fixtures manually:

- 3 true semantic conflicts
- 2 safe controls

For each provide M3:

- BASE context
- A edit
- B edit

Do not reveal labels.

Acceptance:

- At least 4/5 correctly classified.
- M3 returns valid structured output.
- Safe controls are not both falsely flagged.

If <3/5:

STOP and inspect prompt/model capability before continuing.

Do not hide poor results by moving on.

## Phase 2 — Media preprocessing

Goal: normalize videos and produce shot/sentence representations.

Implement:

- ffprobe metadata
- normalization
- shot detection
- keyframe extraction
- audio extraction
- timestamped transcript
- fixture tests

Acceptance:

For a controlled 60-second BASE video:

- detected shots are sensible
- transcript timestamps approximately align
- keyframes exist
- rerunning produces stable output

## Phase 3 — BASE ↔ branch alignment

Goal: map A and B content back to BASE.

Implement:

- shot fingerprints
- transcript similarity
- visual similarity
- weighted matcher
- alignment confidence
- operation inference

Acceptance:

On controlled fixtures correctly identify:

- one deletion
- one replacement
- unchanged segments

Aim for high reliability on the demo fixture, not universal generality.

## Phase 4 — Semantic analyzer

Goal: turn mechanical edits into M3 semantic merge analysis.

Implement:

- context packaging
- M3 client
- Pydantic response schema
- prompt v1
- retry/validation behavior
- evidence extraction
- conflict graph

Acceptance:

The hardware tutorial fixture returns:

```text
A safe alone: true
B safe alone: true
Combined safe: false
Type: prerequisite_loss
```

with correct timestamp evidence.

Also pass safe-control fixture.

## Phase 5 — Benchmark

Goal: know whether the project actually works.

Before the full UI, create at least 20 labeled cases.

Run blind evaluation.

Store results.

Acceptance target:

- Accuracy >= 80%
- Precision >= 0.75
- Recall >= 0.75

These are project targets, not numbers to fabricate.

If performance is lower:

- inspect failure categories
- refine context packaging/prompt
- rerun
- keep all final reported metrics truthful

## Phase 6 — Merge planner and renderer

Goal: actually produce final merged video.

Implement:

- safe edit collection
- conflict resolution input
- segment source-selection plan
- FFmpeg render
- final MP4 output

Acceptance:

Demo fixture:

- A and B safe edits appear in final video
- selected conflict resolution appears
- output plays cleanly

## Phase 7 — Independent M3 verifier

Goal: close the loop.

Use a fresh request.

Acceptance:

- Detect unresolved conflict in intentionally broken merged fixture.
- Pass correctly resolved merged fixture.
- Return timestamped evidence.

## Phase 8 — Frontend

Goal: make the project understandable without explanation.

Build:

- Upload
- Processing state
- Aligned timeline
- Edit summary
- Conflict cards
- Evidence viewer
- Resolution controls
- Render
- Final-video viewer
- Verification report

Acceptance:

A new user can understand the demo fixture in less than one minute.

## Phase 9 — Polish and submission

Tasks:

- Clean README
- Architecture diagram
- Evaluation results
- Limitations
- Screenshots/GIF
- Public repo
- Reproducible setup
- 3-minute demo
- X post
- Submission form
- Final smoke test from clean clone

Do not add new features in this phase.

---

# 30. Calendar

Assuming serious work begins August 30:

## August 30

- Repo bootstrap
- MiniMax spike
- Controlled semantic fixtures
- Decide go/no-go

## August 31

- Media preprocessing
- Scene detection
- Transcript/keyframe extraction

## September 1

- Alignment
- Mechanical diff
- First end-to-end BASE/A/B analysis

## September 2

- Semantic analyzer
- Prompt iteration
- Benchmark dataset creation

## September 3

- Benchmark run
- Merge planner
- FFmpeg renderer
- Watch relevant MiniMax/GMI M3 session if useful

## September 4

- Final verifier
- Frontend
- Fix correctness issues

## September 5

- Freeze features
- README
- Evaluation tables
- Demo recording
- Public repo cleanup
- Submission preparation

## September 6

- Buffer only
- Final submission
- No major feature work

If the official submission deadline is updated, adapt dates, but keep a one-day buffer.

---

# 31. Priority Ladder

If time gets short, preserve features in this order.

## P0 — Must exist

1. BASE/A/B input
2. Controlled alignment
3. Mechanical change summary
4. M3 semantic conflict detection
5. Timestamp evidence
6. Safe controls
7. Benchmark
8. Clean demo

## P1 — Strongly desired

9. Conflict resolution
10. Final MP4 rendering
11. Independent final M3 verifier
12. Polished timeline UI

## P2 — Stretch

13. Reordering support
14. Insert support
15. Speech 2.8 narration repair
16. Cloud deployment
17. More sophisticated visual alignment

Never sacrifice P0 to build P2.

---

# 32. Failure Modes and Responses

## M3 direct video upload unavailable through GMI

Fallback:

- keyframes + timestamped transcript
- clip-level media preprocessing

Continue.

## Alignment is unreliable

Reduce scope:

- require edits to respect shot/sentence boundaries
- use controlled fixtures
- expose low-confidence alignment
- allow demo fixture operation maps if necessary for competition demo, but do not misrepresent automatic capability

## M3 over-flags conflicts

Add:

- safe-control examples
- explicit instruction that related edits are not automatically conflicts
- require a specific combined meaning failure
- optionally run a second challenge pass

## M3 misses long-range dependency

Expand surrounding context window.

Include:

- preceding/following transcript
- extracted base claims
- relevant keyframes

Do not immediately send entire video.

## Rendering is too fragile

Restrict merge operations to whole segments.

Competition value comes primarily from semantic detection.

## Cloud deployment becomes expensive/slow

Demo locally and provide a public repo plus recorded demo.

Do not let deployment consume core build time.

---

# 33. Optional M2.7 Adversarial Review

Only add this after M3 pipeline works.

Workflow:

```text
M3 initial verdict
        |
        v
M2.7 challenge:
“Find the strongest evidence this is NOT a semantic conflict.”
        |
        v
M3 final adjudication
```

Then evaluate:

Does adversarial review reduce false positives without hurting recall?

If yes, include it.

If no, remove it.

Do not add multi-agent theater without measured benefit.

---

# 34. Optional Speech 2.8 Feature

Only after core P0/P1 work is stable.

Use case:

The user chooses:

`Rewrite this sentence while preserving the prerequisite.`

M3 creates a constrained replacement sentence.

M3 checks semantic equivalence.

Speech 2.8 synthesizes narration.

FFmpeg replaces the narration region.

This gives Speech 2.8 a genuine product role rather than a decorative API call.

Do not use Music 3.0 in MergeCut unless a real need emerges.

---

# 35. Competition Submission Narrative

The submission should communicate four points.

## 1. The hidden problem

Two video edits can touch different timestamps and merge perfectly but jointly change what the video says.

## 2. Why existing merge logic misses it

Traditional version control sees editing structure, not audiovisual meaning.

## 3. Why M3 matters

M3 understands the actual video context and reasons across distant edits.

## 4. Closed-loop proof

MergeCut does not stop at a warning.

It:

- detects
- explains
- resolves
- renders
- re-watches
- verifies

---

# 36. Resume-Quality Engineering Requirements

Even though this is a competition project, build it so it remains defensible afterward.

Must be able to explain:

- Why shot-level alignment was selected
- Similarity-scoring strategy
- How edit operations are inferred
- Why model reasoning is separated from deterministic video processing
- How structured outputs are validated
- How hallucination/false positives are evaluated
- Why a fresh verifier is independent
- FFmpeg merge strategy
- Evaluation methodology
- Known limitations
- What would need to change for production scale

Do not claim production readiness.

Do not claim perfect semantic correctness.

Do not claim novel research.

Claim what was actually built and measured.

---

# 37. Definition of Done

MergeCut v0.1 is done when:

```text
[ ] Clean public repository
[ ] BASE/A/B upload works
[ ] Video preprocessing works
[ ] Shot/sentence alignment works on controlled fixtures
[ ] Mechanical edits are displayed
[ ] M3 semantic conflict analysis works
[ ] Safe controls exist
[ ] Timestamp evidence exists
[ ] At least 20 blind benchmark cases run
[ ] Metrics are reported truthfully
[ ] User can resolve a conflict
[ ] Supported final merge renders to MP4
[ ] Fresh M3 verifier runs on final output
[ ] README explains differentiation
[ ] Limitations are documented
[ ] 3-minute demo recorded
[ ] Demo shared publicly on X
[ ] MiniMax tagged in X demo post
[ ] GMI Cloud tagged in X demo post
[ ] X handle ready for submission form
[ ] Public repository link ready
[ ] Demo-video link ready
[ ] MiniMax models used accurately declared
[ ] Submission requirements satisfied
```

---

# 38. MiniMax Coding-Agent Working Rules

Whichever coding interface is used must run MiniMax M3 or M2.7 through GMI Cloud and follow these rules while implementing the project.

1. Read this entire file before making architectural changes.
2. Maintain an `AGENTS.md` with project-specific commands and conventions.
3. Work phase-by-phase in the order above.
4. Do not build later phases before earlier acceptance criteria pass.
5. Keep changes small and testable.
6. Run relevant tests after every meaningful implementation step.
7. Never invent GMI API fields or model identifiers. Keep them configurable until verified from current docs/account.
8. Never fabricate evaluation metrics.
9. Never silently replace MiniMax M3 with another model for the competition's core reasoning.
10. Do not use a non-MiniMax model to generate, edit, refactor, debug, or review competition code. If Codex CLI, Claude Code, Cursor, or another coding shell is used, verify that its provider points to GMI Cloud and its selected model is MiniMax M3 or M2.7 before starting work.
11. Log the coding model/provider used for major implementation sessions in `docs/build-log.md` so the MiniMax/GMI usage story is easy to demonstrate.
12. Supporting libraries and non-generative infrastructure are allowed only where the competition rules permit them.
13. Prefer deterministic code for media processing and M3 for semantic reasoning.
14. Do not add infrastructure without a demonstrated need.
15. Do not create a generic video editor.
16. Do not change the product into project-file version control.
17. Preserve the core differentiator: cross-edit content-level semantic conflict detection.
18. Record important architectural decisions in `docs/architecture.md`.
19. Record limitations rather than hiding them.
20. When a task fails, fix the root cause before layering more abstraction on top.
21. Keep UI focused on demo clarity.
22. Before every phase, state what acceptance criterion will prove it is complete.

---


# 39. First MiniMax Coding-Agent Task

After reading this file, the MiniMax-powered coding agent should begin with Phase 0 and Phase 1 only.

Initial instruction:

```text
Read PROJECT_PLAN.md completely.

Before doing any coding, verify that this coding session is using MiniMax M3 or M2.7 through GMI Cloud. Record the active provider/model in docs/build-log.md.

Do not build the full product yet.

First:
1. Inspect the repository.
2. Create or update AGENTS.md with setup/test conventions.
3. Bootstrap the minimal FastAPI + Next.js structure if it does not already exist.
4. Add environment configuration for GMI/MiniMax without hard-coding undocumented endpoint details.
5. Add an FFmpeg prerequisite check.
6. Build a minimal MiniMax M3 client abstraction.
7. Create five controlled semantic merge fixtures: three true cross-edit conflicts and two safe controls.
8. Implement the strict Pydantic schema for M3 semantic analysis.
9. Create a CLI/script that runs the five fixtures against M3 through GMI and stores the results.
10. Do not start video alignment or UI work until the semantic spike is evaluated.

At the end:
- run tests
- report which acceptance criteria passed
- show the five classifications
- identify any failures
- recommend GO, INVESTIGATE, or STOP for MergeCut
- do not proceed to Phase 2 without an explicit next instruction
```

This first gate is intentional.

The project should earn the right to become large by proving its core semantic behavior first.
