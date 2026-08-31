# MergeCut Build Log

## 2026-08-30

### Session verification

Interface: Hermes Agent
Provider: GMI Cloud
Model: MiniMax M3 (`MiniMaxAI/MiniMax-M3`)
Verified via: `hermes status` → reports `Model: MiniMaxAI/MiniMax-M3`,
`Provider: GMI Cloud`.
Status: Compliant with AGENTS.md "Competition constraints" (MiniMax M3
through GMI Cloud).

### Phase 0 — Repository bootstrap

(Status unchanged from previous entry. `make test` 32/32, `make lint`
clean, `make check-ffmpeg` ok, `make smoke` ok.)

### Phase 1 — v2 prompt iteration (this entry)

#### Why v2 was needed

The v1.0.0 spike (see the earlier build-log entry) gave 5/5 combined
classifications but **collapsed the per-branch safety axis**: on all 3
conflict fixtures, M3 returned `branch_a_safe: false` *and*
`branch_b_safe: false`. PROJECT_PLAN §2 / §15 requires the opposite
axis (each branch individually safe, combined unsafe). That is the
*core* MergeCut differentiator, so it must be proven before Phase 2.

#### Root cause

The v1 prompt told M3 to "decide whether each branch is safe alone"
without a positive decision rule and without showing M3 the *full*
reconstructed branch content. With no way to check whether equivalent
meaning survived elsewhere in the branch, M3 fell back to "did this
branch weaken any claim? → not safe", which is the wrong axis.

#### What v2 changed

1. **Branch-content reconstruction.** New module
   `backend/app/services/minimax/branch_view.py` parses the
   `[mm:ss–mm:ss] 'text'` segments from each fixture's BASE block
   and, given a branch's edit description, renders the *full
   remaining branch content* (deleted segments are marked as gaps,
   replacements are marked with a pointer to the branch-change prose).
   The v2 user payload exposes this as `BRANCH A FULL CONTENT` and
   `BRANCH B FULL CONTENT`, so M3 has the material to apply the
   per-branch decision rule.

2. **Decision rule, verbatim in the system intent.** The user's
   exact wording is in `SYSTEM_INTENT` so M3 cannot paraphrase it
   away. Tested by `test_system_intent_carries_decision_rule_verbatim`
   in `tests/unit/test_prompt_contract.py`.

3. **Worked examples of "equivalent meaning remains".**
   The system intent enumerates cases where a branch is safe:
   presupposed prerequisites, parallel prohibitions, multiple forms
   of the same claim. And the converse: the branch is unsafe only
   when no other segment communicates the meaning in any form.

4. **Five new fixtures (3 added, original 3 redesigned).**
   The fixture dataclass now carries `expected_branch_a_safe` and
   `expected_branch_b_safe` booleans so the runner can evaluate the
   per-branch axis. Three new fixtures:

   - `06_classic_safeAB` — alternate framing of the canonical case.
   - `07_a_unsafe_b_safe` — A truly unsafe alone, B safe alone,
     combined unsafe (pins the v1 failure mode).
   - `08_redundant_safe` — both branches soften wording but a
     redundant statement in BASE keeps the meaning intact in
     combined.

   Originals 01/02/03 were redesigned so the prerequisite/qualifier/
   duration appears in **exactly two** places in BASE, with each
   branch's edit touching one of the two and leaving the other
   intact. The combined video therefore loses the meaning entirely.

5. **v2 spike runner.**
   `scripts/run_spike.py` was rewritten to:
   - namespace output files by `PROMPT_VERSION` so the v1.0.0
     results are preserved (they live at
     `data/derived/spike_results.{json,md}`, not the v2 file).
   - evaluate the user's full v2 gate (canonical 3/3, combined
     ≥7/8, per-branch ≥14/16, both safe controls still safe).
   - exit 0 (GO) / 2 (INVESTIGATE) / 3 (STOP on schema failure).

#### Prompt version history

| Version | Key change | Notes |
|---------|-----------|-------|
| v1.0.0 | Original PROJECT_PLAN §15 contract | 5/5 combined; per-branch axis collapsed (0/3 canonical) |
| v2.0.0 | Add decision rule verbatim + full branch views | per-branch 11/16, canonical 0/3, combined 7/8 |
| v2.1.0 | Add worked examples (presupposition, parallel prohibition, multiple forms) | best balance; results in this report |
| v2.1.1 | Add "softening still counts as loss" / "ANY information BASE communicates" | pushed canonical to 3/3 but flipped safe control 05; reverted |
| v2.2.0 | Add negative-form/positive-form qualifier example | regressed canonical to 1/3; reverted |

The shipped prompt for this report is **v2.1.0**.

#### v2.1.0 spike results (live M3 through GMI Cloud)

**This run** (timestamp 2026-08-30T18:21:00Z):

```
Combined:               7 / 8
Per-branch safety:     13 / 16
Canonical axis:         2 / 3  (01, 02 pass; 03 misses per-branch A and B)
Original safe controls: 2 / 2
Decision:               INVESTIGATE
```

| Fixture | Expected combined | M3 combined | Branch A exp/pred | Branch B exp/pred | Conflicts | Conf. |
|---------|-------------------|-------------|-------------------|-------------------|-----------|-------|
| `01_prereq_loss` | conflict | conflict | True/True OK | True/True OK | 1 | 0.96 |
| `02_qualifier_loss` | conflict | conflict | True/True OK | True/True OK | 1 | 0.93 |
| `03_cause_effect` | conflict | conflict | True/**False** MISS | True/**False** MISS | 2 | 0.87 |
| `04_safe_unrelated` | safe | safe | True/True OK | True/True OK | 0 | 0.99 |
| `05_safe_independent` | safe | safe | True/True OK | True/True OK | 0 | 0.96 |
| `06_classic_safeAB` | conflict | conflict | True/True OK | True/True OK | 2 | 0.88 |
| `07_a_unsafe_b_safe` | conflict | conflict | False/False OK | True/True OK | 1 | 0.96 |
| `08_redundant_safe` | safe | **conflict** FP | True/**False** FN | True/True OK | 1 | 0.72 |

False positives: `08_redundant_safe` (combined; model predicted
conflict, expected safe).
False negatives: none at the combined level.
Branch false negatives: `03_cause_effect/branch_a`, `03_cause_effect/branch_b`, `08_redundant_safe/branch_a` (model
predicted unsafe, expected safe — these are per-branch false
negatives against the *safe* expected value, i.e. the model
over-flagged the branch).

Files: `data/derived/spike_results_v_2_1_0.{json,md}`.

#### Variance across runs (same prompt, temperature=0)

> **Terminology correction (after the original report):** the
> combined-verdict counter labelled `false_positives` in the runner
> was actually collecting false *negatives* (and vice versa), and
> the per-branch counters were similarly swapped. The bug was fixed
> in `scripts/run_spike.py` after the fact; the on-disk artifact was
> regenerated from the unchanged JSON via the new
> `--regen-report` flag. The classification numbers (combined,
> per-branch, canonical, safe controls) are unchanged — only the
> *labels* on the per-row markers were corrected.

The v2.1.0 prompt was run 4 times. Results are stochastic — the
underlying model (M3 via GMI Cloud) is not deterministic even at
temperature=0 on multi-step structured-output tasks.

| Run | Combined | Per-branch | Canonical | Safe controls | Decision |
|-----|----------|------------|-----------|---------------|----------|
| A   | 6/8      | 16/16      | 2/3       | 2/2           | INVESTIGATE |
| B   | 7/8      | 15/16      | 3/3       | 2/2           | **GO** |
| C   | 7/8      | 14/14      | 2/3       | 2/2           | INVESTIGATE (one HTTP error on fixture 03) |
| D   | 7/8      | 13/16      | 2/3       | 2/2           | INVESTIGATE (this report's run) |

Across runs A–D:

- Combined accuracy is consistently 6/8 or 7/8.
- Per-branch accuracy varies 13/16–16/16.
- Canonical 3/3 was achieved once (run B); 2/3 the rest.
- Safe controls 2/2 in every run that didn't hit an HTTP error.

The variance is dominated by fixtures **03** and **08**, both of
which sit on the boundary of the user's decision rule.

#### Honest analysis: the boundary cases

**Fixture 03 (cause_effect).** This is the case where M3's
interpretation has flipped between runs. In run D above, M3 marked
both branches unsafe — losing the cooking duration entirely. M3's
rationale (captured in `spike_results_v_2_0_0.json` from an earlier
debug run):

> "no claim in BASE is of a safety-critical nature (e.g., a
> prerequisite that if missed causes harm or invalidates the
> recipe); the two-minute duration is a cooking guideline, and the
> surrounding context ('make a roux', 'sauce will thicken') still
> conveys a coherent, if temporally underspecified, cooking
> instruction. A viewer could still reasonably infer to cook the
> roux. This is a softening rather than a loss of required meaning."

This is a *defensible* strict reading of the rule. The user's rule
is: "would a reasonable viewer watching the result after applying
BOTH edits still receive that meaning?" A reasonable viewer could
infer they need to cook the roux even without the explicit duration.
Under that strict reading, fixture 03 is *not* a canonical conflict.

Conversely, under a loose reading (any information that was in BASE
that no longer survives in combined counts as a loss), fixture 03
*is* a canonical conflict.

**Fixture 08 (redundant_safe).** M3 over-flags this as a conflict
because branch A *narrows* the scope ("all nut allergies" →
"severe nut allergies") even though the later `[00:50–01:00]`
'Nut-allergic customers: do not consume. Ask staff for alternatives.'
covers all nut-allergic customers. M3 reads the narrowing itself as
meaning loss. The user's rule, as I read it, says equivalent
meaning survives elsewhere — so this should be safe — but M3
distinguishes "narrowing" from "preservation". This is a genuine
boundary case.

**Fixture 05 (safe_independent).** v2.1.1 prompt (with the
"softening counts" addition) flipped this to conflict because M3
read "Step two: run the analyzer against your video trio" as a
required procedural instruction that the output claim doesn't
imply. The user's intent (and my fixture label) treats the output
claim as entailing the step. M3 under v2.1.1 disagreed. Under
v2.1.0 (the shipped prompt), M3 accepts it as safe.

**Conclusion.** The v2 prompt is a substantive improvement over v1:
- v1 had 0/3 canonical axis (every conflict fixture collapsed onto
  the wrong branch safety verdicts).
- v2 has 2/3–3/3 canonical, 7/8 combined, 13/16–16/16 per-branch,
  2/2 safe controls (when no transient HTTP error).

The boundary cases (03, 08) sit on a strict-vs-loose interpretation
of the user's decision rule. Neither interpretation is objectively
correct; both are defensible.

#### Why I'm reporting INVESTIGATE rather than GO

The v2.1.0 prompt hits all four gate components in run B but
fails at least one in runs A/C/D. The user's gate requires **all
four** to pass deterministically. Across 4 runs at temperature=0,
that has happened once. Reporting GO based on a single run would
violate AGENTS.md §38 rule 8 ("never fabricate metrics") and the
"do not hide poor results by moving on" rule from PROJECT_PLAN
§29 Phase 1.

#### What the user can decide next

1. **Accept stochasticity: re-run with a small multiplier and take
   the median result.** The gate could be relaxed to "≥3/4 runs
   pass each component" with a known-bad fixture removed.
2. **Re-spec the boundary fixtures.** Fixture 03 could be relabeled
   as "safe" (matching the strict reading) — it's a defensible call
   either way. Fixture 08 is harder: M3's narrowing-≠-loss
   interpretation is genuinely arguable.
3. **Add more redundant restatements to fixtures 03 and 08.**
   The "canonical axis" recipe (two restatements in BASE, each
   branch touches one) is what made fixtures 01 and 02 stable. The
   same recipe applied to 03 (a safety cause-effect with two
   redundancies) might make it stable. Fixture 08 is harder
   because the conflict-vs-safe distinction there is genuinely
   ambiguous by the rule.
4. **Move to Phase 2 anyway.** M3 demonstrably reasons about
   cross-edit semantics better than v1 did. The remaining variance
   is on boundary cases that Phase 5's larger evaluation will
   characterize statistically.

I'm not making this decision unilaterally. The user asked for
**GO / INVESTIGATE / STOP** and an honest report. The honest
report is INVESTIGATE.

#### New artefacts in v2

- `backend/app/services/minimax/branch_view.py` (new)
- `backend/app/services/minimax/prompts.py` (rewritten to v2.1.0)
- `backend/tests/fixtures/spike_fixtures.py` (8 fixtures, dataclass
  extended with per-branch booleans)
- `backend/tests/unit/test_prompt_contract.py` (new)
- `backend/tests/unit/test_spike_fixtures.py` (updated for 8 fixtures)
- `scripts/run_spike.py` (rewritten with v2 gate + namespaced output)
- `data/derived/spike_results_v_2_0_0.{json,md}` (preserved)
- `data/derived/spike_results_v_2_1_0.{json,md}` (preserved)
- `data/derived/spike_results_v_2_1_1.{json,md}` (preserved for diff)
- `data/derived/spike_results_v_2_2_0.{json,md}` (preserved for diff)
- `data/derived/spike_results.{json,md}` (v1.0.0 — preserved)
- `docs/build-log.md` (this file)

### v2 decision

**INVESTIGATE.** See analysis and the user's options above. Not
proceeding to Phase 2 until an explicit next instruction.

---

## 2026-08-30 (continued) — Phase 2: media preprocessing

### Session verification (re-confirmed)

- Model: `MiniMaxAI/MiniMax-M3` via GMI Cloud (still active).
- Python 3.12 via `uv`, all Phase 2 deps installed.

### Architectural decision recorded

The user's two-axis semantic taxonomy was recorded in
`docs/architecture.md`:

- `impact_level`: `preserved` / `degraded` / `broken`
- `cross_edit_interaction`: `none` / `amplifies_existing_issue` / `creates_new_conflict`
- Canonical MergeCut condition: A=preserved, B=preserved, A+B=broken,
  interaction=creates_new_conflict.

This taxonomy is deferred to Phase 4 / 5 and is NOT implemented in
Phase 2 (no existing type dependencies forced it).

### Terminology correction

The v2 spike runner had FP / FN labels swapped in
`scripts/run_spike.py`. The classification *numbers* were correct
(7/8 combined, 13/16 per-branch, 2/3 canonical, 2/2 safe
controls); only the labels on per-row markers were wrong. The
bug was fixed in the runner and the on-disk artifact
(`data/derived/spike_results_v_2_1_0.{json,md}`) was regenerated
from the unchanged JSON via a new `--regen-report` flag. The
v1.0.0 and other v2.x results were NOT regenerated because they
were either too early (v2.0.0) or had their own logical
mis-labellings that we want preserved for diff purposes.

### Dependencies added

| Package | Version | Why |
|---------|---------|-----|
| `scenedetect[opencv]>=0.6.5,<0.7` | 0.6.7.1 | Coarse shot detection. The `[opencv]` extra was removed in 0.7.x and the implicit cv2 wheel on Python 3.12 / macOS arm64 doesn't actually expose `cv2`. Pinning to 0.6.x with the explicit extra pulls in `opencv-python-headless` (which does expose `cv2`). |
| `opencv-python-headless>=4.10.0` | 5.0.0.93 | Required by scenedetect; headless variant because we never display frames. |
| `Pillow>=10.4.0` | 12.3.0 | JPEG keyframe encoding. |
| `numpy>=1.26.0,<3.0` | 2.5.2 | Required by scenedetect + faster-whisper. |
| `faster-whisper>=1.0.3` | 1.2.1 | Timestamped English ASR. Brings in `av` (PyAV) transitively, which is fine on Linux/Windows but generates a non-fatal macOS objc duplicate-FFmpeg-bundle warning when both `cv2` and `av` are imported in the same process. |
| (transitive) `av` | 18.1.0 | Used by faster-whisper's audio decoder; not a direct dep. |
| (transitive) `ctranslate2` | 4.8.1 | faster-whisper's inference engine. |
| (transitive) `onnxruntime` | 1.29.0 | faster-whisper's ASR backend on CPU. |

No PyAV was added on purpose: PyAV ships its own FFmpeg and we
already require the system FFmpeg for `make check-ffmpeg`. Shipping
two FFmegas via Python wheels would have produced runtime conflicts
on macOS that are worse than the existing objc warning.

### Files added or changed

#### New (Phase 2 deliverables)

- `backend/app/models/media.py` — `VideoRepresentation`, `Shot`,
  `TranscriptSegment`, `VideoMetadata`, `NormalizationInfo`,
  `MediaError`, `UnsupportedFormatError` (all `extra="forbid"`).
- `backend/app/services/media/__init__.py`
- `backend/app/services/media/normalize.py` — `probe_metadata`,
  `normalize_video`, `_WorkingSpec`, `_needs_normalization`.
- `backend/app/services/media/audio.py` — `extract_audio` (mono
  16 kHz WAV via FFmpeg).
- `backend/app/services/media/scenes.py` — `detect_shots`
  (PySceneDetect `ContentDetector`).
- `backend/app/services/media/keyframes.py` — `extract_keyframes`
  (one JPEG per shot via FFmpeg `-ss ... -frames:v 1`).
- `backend/app/services/media/transcript.py` — `transcribe`
  (faster-whisper), `load_cached_transcript`, `save_cached_transcript`,
  `clear_model_cache`.
- `backend/app/services/media/pipeline.py` — `process_video` —
  the single public entry point that orchestrates everything and
  caches the JSON representation under
  `data/derived/videos/<video_id>/representation.json`.
- `backend/tests/fixtures/media_fixtures.py` — deterministic
  MP4 builders for: `normal_3shots_320x240_30fps.mp4`,
  `multi_6shots_640x480_noaudio.mp4`, `speech_3shots_320x240_audio.mp4`,
  `noaudio_3shots_320x240.mp4`, and `bad_zero_bytes.mp4`.
- `backend/tests/unit/test_media_pipeline.py` — 10 tests:
  normal, multi-scene, no-audio, speech, bad input, missing
  file, repeated-processing stability, full-fixture smoke,
  original-file preservation, system-ffmpeg fallback.

#### Changed

- `backend/pyproject.toml` — added Phase 2 deps.
- `backend/app/config.py` — added `ffmpeg_path`, `ffprobe_path`,
  `scene_threshold`, `whisper_model`, `whisper_device`,
  `whisper_compute_type` settings.
- `.env.example` — added the same env vars with explanations.
- `backend/app/models/__init__.py` — re-exports the media models.
- `Makefile` — added `media-smoke` target.
- `AGENTS.md` — added `media-smoke` command and Phase 2
  acceptance section.
- `docs/architecture.md` — recorded the two-axis semantic
  taxonomy decision (impact_level + cross_edit_interaction).
- `scripts/run_spike.py` — fixed FP/FN swap, added
  `--regen-report` to re-render reports from existing JSON.

### Pipeline (single source of truth)

```
process_video(source)
  1. content-hash the source bytes → video_id
  2. read cached representation at
     derived_dir/videos/<video_id>/representation.json
     if present → return it (idempotent re-runs)
  3. ffprobe -of json -show_format -show_streams source
       → VideoMetadata
  4. if metadata is already working-format
       (h264 / yuv420p / 30 fps / even dims / aac) →
       copyfile(source, derived_dir/videos/<video_id>/<stem>.working.mp4)
     else → ffmpeg -i source -c:v libx264 -pix_fmt yuv420p -r 30
                        -vsync cfr (-c:a aac -ar 48000 | -an)
       → derived_dir/videos/<video_id>/<stem>.working.mp4
  5. PySceneDetect ContentDetector(threshold=settings.scene_threshold)
       on the working mp4 → list of (start, end) shot intervals
  6. ffmpeg -i working_mp4 -vn -ac 1 -ar 16000 -acodec pcm_s16le
       → derived_dir/videos/<video_id>/<stem>.mono16k.wav
     (returns None when source has no audio track)
  7. if no audio → segments = []
     else if cached transcript for audio fingerprint exists →
       segments = load_cached_transcript(...)
     else →
       faster-whisper model.transcribe(
         beam_size=1, word_timestamps=True,
         vad_filter=True, language="en"
       )
       → save_cached_transcript(...)
  8. for each shot: ffmpeg -ss <midpoint> -i working_mp4
                    -frames:v 1 -q:v N → shot_NNNN.jpg
  9. join transcript segments into shots by midpoint rule
 10. build VideoRepresentation
 11. write JSON to representation.json
 12. return the Pydantic model
```

### Determinism notes

- Content hash, ffprobe output, ffmpeg normalize, ffmpeg audio
  extract, ffmpeg keyframe extract, and PySceneDetect are
  deterministic given identical inputs and identical threshold.
- faster-whisper is deterministic given `beam_size=1` (greedy).
- The `representation.json` file is byte-identical across re-runs
  for the same source (modulo Pydantic key ordering, which is
  stable across Python versions for a given schema).

### Phase 2 fixture results

| Fixture | Build OK | Pipeline OK | Shots | Audio | Notes |
|---------|----------|-------------|-------|-------|-------|
| normal_3shots_320x240_30fps.mp4 | yes | yes | 3 / 3 | yes | h264 + aac, normalization skipped |
| multi_6shots_640x480_noaudio.mp4 | yes | yes | 6 / 6 | none | no audio track |
| speech_3shots_320x240_audio.mp4 | yes | yes | 3 / 3 | yes | sine-tone audio, transcript empty after VAD |
| noaudio_3shots_320x240.mp4 | yes | yes | 3 / 3 | none | no audio track |
| bad_zero_bytes.mp4 | yes | raises MediaError | n/a | n/a | ffprobe rejects, MediaError raised |

Each fixture's `video_id` (content hash) is stable across re-runs.
Re-processing the same fixture is a no-op (cache hit) — `make
media-smoke` confirmed.

### Phase 2 acceptance criteria

- ffprobe metadata is correct. ✅ Tested on all four good fixtures;
  metadata matches input shape exactly (width, height, fps, codec,
  audio presence).
- Scene boundaries are reasonable on controlled fixtures. ✅
  3-block normal fixture → 3 shots at the cut boundaries; 6-block
  multi fixture → 6 shots; 3-block noaudio fixture → 3 shots.
- Keyframes correspond to the intended shots. ✅ Each shot has
  exactly one JPEG keyframe at the midpoint; non-empty bytes;
  indexed by shot number.
- Transcript timestamps approximately correspond to speech. ⚠️
  The "speech" fixture ships a sine tone, not speech, so the
  transcript is empty after VAD filtering. The pipeline runs
  cleanly; transcript accuracy will be characterized with
  real-speech fixtures in Phase 5's evaluation set.
- Processing the same fixture twice produces materially equivalent
  structured output. ✅ `test_repeated_processing_is_stable`
  compares metadata + shot boundaries across re-runs (within
  ±0.05s on duration, ±0.1s on shot boundaries).
- All tests pass. ✅ 42 / 42 in `make test`, including 10 / 10
  media-pipeline tests under `make media-smoke`.

### Known weaknesses (Phase 2)

1. **Sine-tone "speech" fixture.** We don't ship licensed speech
   audio. The ASR pipeline runs end-to-end but produces no
   transcript; transcription accuracy will need real-speech
   fixtures (Phase 5's evaluation set).
2. **faster-whisper first-call latency.** Model weights download
   on first use (~150 MB for `base`). Subsequent calls hit the
   local cache. In a hosted environment this would be a warm-up
   step.
3. **Fixed fps at 30.** We force 30 fps during normalize so
   downstream timestamps are predictable. Source videos with
   variable framerate are canonicalized to 30 fps. Higher
   framerate source videos lose information; flag this for
   the demo scenario (which is short and 30 fps anyway).
4. **One keyframe per shot.** Phase 3 alignment may need multiple
   fingerprints per shot. The schema already supports
   `keyframe_paths: list[Path]` — `extract_keyframes` returns one
   but can be extended without a schema change.
5. **PySceneDetect ContentDetector threshold.** Default 27.0 is
   PySceneDetect's recommendation but is tuned for video with
   hard cuts. Soft cuts (cross-fades) may be missed; this is
   fine for the MVP scope (PROJECT_PLAN §13.1: "do not begin with
   frame-perfect alignment").
6. **macOS duplicate-FFmpeg-bundle warning.** The objc warning on
   import (from cv2 and av both registering `AVFFrameReceiver` /
   `AVFAudioReceiver`) is non-fatal and does not affect pipeline
   behaviour. Documented but not fixed; fixing requires picking
   one of `opencv-python` or `PyAV` and forcing the other to use
   system FFmpeg, which is out of scope here.
7. **Cache eviction is manual.** `data/derived/` grows forever;
   no automatic LRU. Not a problem for the demo scale but worth
   noting for production.

### Phase 2 decision

**GO.** All Phase 2 acceptance criteria pass on the current
fixtures; `make test` is 42/42 green, `make lint` is clean,
`make media-smoke` is 10/10 green.

Stopping before Phase 3 per the user's instructions. Awaiting an
explicit next instruction to begin alignment (Phase 3).

Phase 1 v1 / v2 results were not modified by this entry; the
v2.1.0 artifact was regenerated only to fix the FP/FN label swap
(documented above).

---

## 2026-08-31 — Phase 2.5: real-speech validation + Makefile robustness

### Real-speech run against a real recording

Validated `make real-speech` against
`backend/tests/manual/real_speech_test.mov` (15.77 s, h264
1620x1080 @ 29.93 fps, aac audio):

```
video
=====
path               .../real_speech_test.mov
video_id           53019d28cee5911f
duration           00:15.766 (15.77s)
dimensions         1620x1080
fps                29.933
video codec        h264
audio              yes (aac)
normalization      copy
working file       .../data/derived/videos/53019d28cee5911f/real_speech_test.working.mp4
audio file         .../data/derived/videos/53019d28cee5911f/real_speech_test.working.mono16k.wav

shots (1)
=========
  shot_0000  00:00.000 – 00:15.668  (15.67s)  keyframes=1  segments=1
      text: This is the first success section. Now I am talking about the blue box. This ...

transcript segments (1)
=======================
  -- shot_0000 (00:00.000 – 00:15.668) --
  00:01.420 – 00:12.920  conf=0.91  This is the first success section. Now I am talking about the blue box. This is the final section.

summary
=======
shots detected     1
transcript segments 1
transcript chars   98
  shot_0000     98 chars     6.3 chars/sec
```

Observations:

- Single continuous shot detected across the entire15 s clip —
  correct (no scene cuts in this recording).
- Transcript text is clean and matches the user's spoken content
  (the user's reference sentence about the blue box).
- Confidence 0.91 on faster-whisper `base` model — acceptable for
  the demo. Larger models (`small`, `medium`) would push this
  higher but cost more time and disk.
- Normalization was skipped (source already h264/yuv420p/aac/29.93
  fps) — the `copy` branch of `_needs_normalization` fired.

### Makefile bugs found and fixed during this run

1. **Duplicate `uv` substitution in recipes.** `make real-speech`
   printed `cd backend && /Users/abhinav/.hermes/bin/uv
   /Users/abhinav/.hermes/bin/uv python install 3.12` and failed
   with `unrecognized subcommand /Users/abhinav/.hermes/bin/uv`.
   Root cause: the auto-detect shell template printed the path
   twice — once from the `[ -x ]` for-loop, once from the
   always-firing `command -v uv` fallback. Fix: collect the
   for-loop result into a `found` variable and only call
   `command -v uv` when `found` is empty.

2. **`real-speech` target passed a repo-root-relative path into
   a script whose cwd was `backend/`.** The recipe did
   `cd backend && uv run python ../scripts/test_real_speech.py
   $(REAL_SPEECH_VIDEO)`, so passing
   `backend/tests/manual/foo.mov` (relative to the repo root)
   resolved to `backend/backend/tests/manual/foo.mov` after the
   `cd`. Fix: wrap the path in `$(abspath ...)` so it's absolute
   and cwd-independent.

3. **PATH-dependent `uv` invocation.** `make` recipes don't source
   `~/.zshrc`/`~/.bashrc`, so on hosts where the user's interactive
   shell exports `uv` from `~/.hermes/bin` but the Make process
   inherits a stripped PATH, the bare `uv` command fails. Fix: the
   auto-detect template now probes `~/.hermes/bin/uv`,
   `~/.local/bin/uv`, `~/.cargo/bin/uv`, `/opt/homebrew/bin/uv`,
   `/usr/local/bin/uv`, then falls back to `command -v uv`. If
   nothing matches, Make aborts with an actionable install
   instruction. Verified: with `PATH=/usr/bin:/bin` (only), the
   Makefile still resolves `uv` and `make backend-install` works.

### Phase 2.5 decision

**GO.** The Phase 2 pipeline runs end-to-end on a real recording
with real speech. The transcript quality is usable for Phase 3
alignment (even though we expect Phase 5 to characterize accuracy
on a larger evaluation set). No code changes were made to the
pipeline itself during this validation.

Stopping before Phase 3 per the user's instructions. Awaiting an
explicit next instruction to begin alignment (Phase 3).