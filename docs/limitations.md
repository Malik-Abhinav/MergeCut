# MergeCut Limitations (Phase 0 / Phase 1)

- **No video pipeline yet.** The Phase 1 spike uses text-only fixtures
  (transcripts + edit descriptions). Audio extraction, shot detection, and
  keyframe generation land in Phase 2.
- **No real frontend.** The Next.js shell is a single placeholder page.
  Upload / timeline / conflict UI arrives in Phase 8.
- **Spike scale is small.** Five hand-built fixtures are not a benchmark.
  PROJECT_PLAN §9 requires ≥20 labeled scenarios for MVP; that suite is
  built in Phase 5.
- **No verifier pass.** The independent M3 verifier on the final render
  (PROJECT_PLAN §19) is a Phase 7 deliverable.
- **No rendering.** FFmpeg is verified to be present but no merge pipeline
  exists yet.
- **Model capability is unproven.** Until `make spike` exits 0, we do not
  know whether M3 through GMI Cloud can consistently classify cross-edit
  semantic interactions at the level of detail required.

If any of these limits block Phase 2, surface it in `docs/build-log.md`
before continuing.