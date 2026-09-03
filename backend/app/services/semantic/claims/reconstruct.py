"""STEP 2 — Reconstruct the claim lists for A, B, and combined.

Given:

  - the BASE `BaseClaim` list (from STEP 1),
  - the A-vs-BASE `AlignmentResult` (from Phase 3),
  - the B-vs-BASE `AlignmentResult` (from Phase 3),

produce a *reconstructed claim list* for:

  - A      : the BASE claims with the A-side Phase 3 edits applied.
             A claim's evidence region is kept iff some
             non-`delete`/`replace` Phase 3 alignment on that
             region (in A) still carries the meaning; otherwise
             the evidence region is dropped from A's list.
  - B      : symmetric.
  - A+B    : the intersection of A's preserved evidence AND B's
             preserved evidence. If the A+B list is empty for a
             claim, the claim is "broken" in combined (this is
             the deterministic proxy the orchestrator hands to
             M3 for confirmation).

THIS MODULE IS PURE DETERMINISTIC PYTHON. NO M3 CALLS.

The reconstruction is conservative: it only drops evidence
regions that the Phase 3 alignment has flagged as `delete` or
`replace` for that branch. A `replace` edit drops the BASE
evidence region (the original meaning is gone in the branch);
the orchestrator may add a new evidence region from the
replacement text in STEP 3 if M3 decides the meaning survives
in the new wording. Until then, the region is dropped.

For each claim, the orchestrator also computes a list of
"surviving BASE evidence regions" in each branch by
intersecting the claim's BASE evidence with the branch's
preserved shots. A claim is **automatically preserved** in
the branch if it has at least one surviving evidence region
OR at least one equivalent surviving — M3's STEP 3 call
still has the final say, but this gives M3 a clear hint and
saves tokens (M3 can confirm "preserved" without reasoning
from scratch).
"""

from __future__ import annotations

from app.models.alignment import AlignmentResult
from app.models.claims import (
    BaseClaim,
    ClaimEvidenceRegion,
    ClaimStatus,
)
from app.models.media import VideoRepresentation


def _shot_timeline(rep: VideoRepresentation) -> list[tuple[float, float]]:
    """Return (start, end) for every shot in the rep, in order."""
    return [(shot.start, shot.end) for shot in rep.shots]


def _alignment_deletes_replaces(
    alignment: AlignmentResult,
) -> list[tuple[float, float]]:
    """Return the (start, end) ranges of BASE shots that the
    branch's Phase 3 alignment flags as `delete` or `replace`.

    These are the ranges we drop from the BASE claim list when
    reconstructing the branch's claim list.
    """
    out: list[tuple[float, float]] = []
    for m in alignment.matches:
        if m.operation in {"delete", "replace"} and m.base_shot is not None:
            out.append((m.base_shot.start, m.base_shot.end))
    return out


def _range_overlaps(span: tuple[float, float], cuts: list[tuple[float, float]]) -> bool:
    """True if `span` overlaps with any range in `cuts`."""
    s, e = span
    for cs, ce in cuts:
        if e <= cs or s >= ce:
            continue
        return True
    return False


def _filter_regions(
    regions: list[ClaimEvidenceRegion],
    cuts: list[tuple[float, float]],
) -> list[ClaimEvidenceRegion]:
    """Return the regions that do NOT overlap with any `delete`/`replace` cut."""
    return [r for r in regions if not _range_overlaps((r.start, r.end), cuts)]


# ---------------------------------------------------------------------------
# Per-branch reconstruction.
# ---------------------------------------------------------------------------


def reconstruct_branch_claims(
    base_claims: list[BaseClaim],
    branch_alignment: AlignmentResult,
) -> list[BaseClaim]:
    """Apply the branch's Phase 3 alignment to the BASE claim list.

    For each claim:
      - drop `evidence_regions` that overlap a `delete`/`replace`,
      - drop `equivalents` that overlap a `delete`/`replace`,
      - if BOTH evidence_regions and equivalents are empty after
        the cut, the claim is *definitively broken* in the
        branch (no surviving span). We keep the claim in the
        list but with empty regions so M3 sees it as a hint
        to return `broken`.

    Returns a new list of `BaseClaim` (one per BASE claim; the
    returned claims are *projections* of the BASE claims into
    the branch, NOT new claims).
    """
    cuts = _alignment_deletes_replaces(branch_alignment)
    out: list[BaseClaim] = []
    for c in base_claims:
        surviving = _filter_regions(c.evidence_regions, cuts)
        surviving_eqs = _filter_regions(c.equivalents, cuts)
        out.append(
            BaseClaim(
                claim_id=c.claim_id,
                meaning=c.meaning,
                claim_type=c.claim_type,
                importance=c.importance,
                evidence_regions=surviving,
                equivalents=surviving_eqs,
            )
        )
    return out


def reconstruct_combined_claims(
    branch_a_claims: list[BaseClaim],
    branch_b_claims: list[BaseClaim],
) -> list[BaseClaim]:
    """Reconstruct the combined (A+B) claim list.

    The combined claim list is the intersection of A's surviving
    evidence and B's surviving evidence. A claim survives in
    combined only if it survives in BOTH branches.

    If a claim is in BASE but absent from A's surviving list
    (because A dropped all evidence + equivalents) AND absent
    from B's surviving list (because B dropped all evidence +
    equivalents), it is broken in combined. We surface that
    case by emptying all evidence_regions + equivalents in
    the returned claim.
    """
    by_id_a = {c.claim_id: c for c in branch_a_claims}
    by_id_b = {c.claim_id: c for c in branch_b_claims}
    all_ids = list(dict.fromkeys(c.claim_id for c in branch_a_claims))
    out: list[BaseClaim] = []
    for cid in all_ids:
        a = by_id_a.get(cid)
        b = by_id_b.get(cid)
        if a is None or b is None:
            # Should not happen in practice (both branches
            # start from the same BASE claim list). Be
            # defensive: treat the missing side as broken.
            base = a or b
            assert base is not None
            out.append(
                BaseClaim(
                    claim_id=cid,
                    meaning=base.meaning,
                    claim_type=base.claim_type,
                    importance=base.importance,
                    evidence_regions=[],
                    equivalents=[],
                )
            )
            continue
        # The combined claim is the BASE claim with evidence
        # regions that survive in BOTH A and B.
        base = a  # use A's metadata (equal to B's by construction)
        surviving = [r for r in a.evidence_regions if r in b.evidence_regions]
        surviving_eqs = [r for r in a.equivalents if r in b.equivalents]
        out.append(
            BaseClaim(
                claim_id=cid,
                meaning=base.meaning,
                claim_type=base.claim_type,
                importance=base.importance,
                evidence_regions=surviving,
                equivalents=surviving_eqs,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Surrogate verdicts (deterministic hints M3 sees in STEP 3).
# ---------------------------------------------------------------------------


def deterministic_surrogate_status(
    reconstructed: list[BaseClaim],
) -> dict[str, ClaimStatus]:
    """For each claim, compute a *surrogate* status from the
    reconstructed evidence regions alone (no M3).

    Used as a *hint* in the STEP 3 user payload so M3 can
    confirm/override. NOT used to derive the interaction.

    Rules:
      - any surviving evidence_region OR equivalent → preserved
      - else → broken

    The orchestrator's STEP 3 still asks M3 for the actual
    verdict; M3 can override (e.g. flag degraded when an
    equivalent is weakened, even though regions are non-empty).
    """
    out: dict[str, ClaimStatus] = {}
    for c in reconstructed:
        if c.evidence_regions or c.equivalents:
            out[c.claim_id] = ClaimStatus.PRESERVED
        else:
            out[c.claim_id] = ClaimStatus.BROKEN
    return out


__all__ = [
    "reconstruct_branch_claims",
    "reconstruct_combined_claims",
    "deterministic_surrogate_status",
]
