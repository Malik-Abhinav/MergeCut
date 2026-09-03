"""Phase 3.5 — merge-side services.

Currently exposes:

- ``provenance``: BASE-anchored provenance-aware cross-branch
  composition. Builds an ``EditSet`` per branch from a Phase 3
  ``AlignmentResult`` and composes the combined timeline by
  iterating BASE (never the branch's reconstructed timeline).
  This is the deterministic Phase 3 / 6 boundary; it does not
  call M3.
"""

from __future__ import annotations

from app.services.merge.provenance import (  # noqa: F401
    BaseShotRecord,
    BranchShotProvenance,
    CombinedTimeline,
    CompositionVerdict,
    EditKind,
    EditSet,
    EditUnit,
    ProvenanceSlice,
    ShotRange,
    build_edit_set,
    compose_combined,
)
