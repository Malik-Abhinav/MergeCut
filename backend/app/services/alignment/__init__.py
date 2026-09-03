"""MergeCut Phase 3 — BASE ↔ branch alignment package.

Submodules:

- `fingerprints` : build `ShotFingerprint`s from a Phase 2
                   `VideoRepresentation` (visual pHash + transcript
                   tokens).
- `similarity`   : the four component similarity functions and the
                   weighted blend (with missing-modality
                   re-normalization).
- `align`        : monotonic DP-based shot alignment.
- `edit_ops`     : infer UNCHANGED / DELETE / REPLACE / TRIM
                   (Phase 4+ will add INSERT / MOVE).
- `run`          : top-level `align_branch_to_base()` orchestrator.

No MiniMax / no embeddings / no vector DB. Everything here is
deterministic so a re-run on the same inputs produces the same
matches.
"""
