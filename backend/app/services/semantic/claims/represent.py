"""Actual-content reconstruction for Phase 4 claim-centric analysis.

Root defect being fixed (Phase 4 representation-correctness pass):

  The previous helpers (``_build_branch_evaluation_lines`` /
  ``_build_combined_evaluation_lines`` inside
  ``app.services.semantic.claims.orchestrate``) interleaved BASE
  transcript text next to ``[DELETED]`` / ``[REPLACED]`` /
  ``[TRIMMED]`` markers. M3 read those lines as the per-branch
  "rendered content" and saw the BASE words alongside the edit
  markers, which (a) leaked BASE wording into deleted / replaced
  positions, (b) made "deleted" shots look like BASE content with
  an annotation, and (c) made "replaced" shots look like BASE
  content with a replacement annotation rather than the actual
  replacement.

What the user wants (this module):

  The per-branch and per-combined content handed to M3 must
  contain ONLY the actual content the viewer hears:

    - delete       : the BASE shot's text is GONE. Nothing about
                      it (not the BASE words, not a marker) appears
                      in the rendered branch content.
    - replace      : the actual replacement ASR transcript from the
                      branch shot the alignment paired with the
                      BASE shot. The BASE words do NOT appear.
    - trim         : the actual branch shot's ASR transcript (the
                      trimmed wording the viewer hears). BASE text
                      does NOT appear unless the branch kept it.
    - unchanged    : the aligned branch shot's actual transcript.

  Combined rendering applies BOTH alignments over BASE positions:

    - A deleted the shot AND B deleted the shot → gone.
    - A deleted the shot AND B left it       → A wins (gone).
      (We follow A-then-B application order; the canonical
      Phase 4 product principle already accommodates both
      deletion orders via ``reconstruct_combined_claims``.)
    - A replaced the shot AND B left it      → the A replacement.
    - A left it       AND B replaced the shot → the B replacement.
    - A replaced the shot AND B replaced it   → the A replacement
      (A-first application order; the deterministic product
      principle treats the two symmetrically for the per-claim
      verdict, so which one wins the textual rendering is
      unimportant for the verdict).
    - Unrelated edits (different BASE positions) compose.

  Lines are 1:1 with BASE shots in BASE order. Delete/trim
  positions may have an empty string (no content line at all,
  or an empty slot); the orchestrator's per-claim prompts read
  the lines verbatim and M3 is told to infer "broken" when the
  evidence region has no surviving transcript text.

  Edit metadata (which BASE position was deleted / replaced /
  trimmed, with confidence) lives in a separate Pydantic model
  (``EditMetadata``) on the same ``ReconstructedActualContent``
  so the orchestrator / forensic dump / harness can audit the
  edits without ever mixing them into the candidate content.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.models.alignment import AlignmentResult, EditOperationType
from app.models.media import VideoRepresentation
from app.services.merge.provenance import (
    BaseShotRecord,
    CombinedTimeline,
    ShotRange,
    build_edit_set,
    compose_combined,
)

# ---------------------------------------------------------------------------
# Models.
# ---------------------------------------------------------------------------


class EditMetadataEntry(BaseModel):
    """One BASE-position edit, recorded for forensics only.

    This is metadata, NOT candidate content. The orchestrator
    stores every edit the Phase 3 alignment produced in the
    metadata side of the reconstruction so the eval harness and
    the forensic report can audit them, but they are never
    inlined into the per-branch / per-combined actual-content
    lines that M3 sees in STEP 3.
    """

    model_config = ConfigDict(extra="forbid")

    branch: str = Field(description="'branch_a', 'branch_b', or 'combined'.")
    base_sequence_index: int = Field(ge=0)
    operation: EditOperationType
    confidence: float = Field(ge=0.0, le=1.0)
    branch_shot_sequence_index: int | None = Field(
        default=None,
        description="Branch shot the alignment paired with this BASE shot. None for deletes.",
    )


class EditMetadata(BaseModel):
    """All edits the alignment applied to produce this
    reconstruction.

    ``entries`` are 1:1 with the BASE positions touched by the
    branch (or both branches, in the combined case). Unchanged /
    ``uncertain`` positions are NOT recorded here — only the
    decisions M3 needs to know about (delete / replace / trim).
    """

    model_config = ConfigDict(extra="forbid")

    entries: list[EditMetadataEntry] = Field(default_factory=list)


class ActualContentLine(BaseModel):
    """One BASE-position line of the actual-content reconstruction.

    The rendered ``text`` is the verbatim transcript the viewer
    hears at this BASE position in the branch (or in the combined
    view). Empty ``text`` means the position has no surviving
    content (deleted, or a trim with no transcript). ``base_text``
    carries the BASE shot's transcript purely for forensic
    inspection and is NEVER included in the per-claim M3 payload
    — it lives only on this data structure so the diagnostic
    writer can surface it in the JSON report.
    """

    model_config = ConfigDict(extra="forbid")

    base_sequence_index: int = Field(ge=0)
    base_text: str = Field(
        description="The BASE shot's verbatim transcript. Diagnostic only; never sent to M3.",
    )
    text: str = Field(
        description=(
            "The actual transcript the viewer hears in the branch "
            "(or combined) view at this BASE position. Empty when "
            "the position has no surviving content (delete, or "
            "trim that removed all speech)."
        )
    )
    operation: EditOperationType = Field(
        description=(
            "The operation that produced this line's text. 'unchanged' "
            "for BASE survivors; 'replace' / 'trim' for branch-shot "
            "ASR text; 'delete' for empty slots; 'uncertain' for "
            "low-confidence alignments (using aligned branch text when available)."
        )
    )
    deleted: bool = Field(
        description="True iff the position is absent from the branch (delete in this branch).",
    )


class ReconstructedActualContent(BaseModel):
    """The actual-content reconstruction of one branch (or combined).

    Three fields:

    - ``lines``             : ordered 1:1 with BASE shots. Each
                              ``ActualContentLine`` carries the
                              actual transcript the viewer hears
                              at that BASE position. M3 reads
                              these verbatim in the STEP 3
                              evaluation prompt.
    - ``edit_metadata``     : a separate audit of every edit the
                              alignment applied. M3 never sees
                              this.
    - ``combined_timeline`` : present only on the ``combined`` view;
                              carries the explicit
                              ``CombinedTimeline`` from the
                              BASE-anchored composer so forensic
                              consumers can inspect unresolved
                              slices and unanchored inserts
                              without contaminating the candidate
                              text M3 reads.

    The ``text_lines`` helper returns just the ``text`` values in
    order; that's what the orchestrator hands to M3 as
    ``branch_reconstructed_lines``.
    """

    model_config = ConfigDict(extra="forbid")

    branch: str = Field(description="'branch_a', 'branch_b', or 'combined'.")
    lines: list[ActualContentLine] = Field(default_factory=list)
    edit_metadata: EditMetadata = Field(default_factory=EditMetadata)
    combined_timeline: CombinedTimeline | None = Field(
        default=None,
        description=(
            "Explicit mechanical-conflict data on the combined view. "
            "Includes every slice verdict (preserved / replaced / "
            "trimmed / deleted / unresolved), the per-BASE-position "
            "unit_a / unit_b provenance, and any unanchored inserts "
            "preserved separately. ``None`` on branch_a / branch_b "
            "reconstructions (they are not combined). Forensic only; "
            "never mixed into M3's candidate text."
        ),
    )

    def text_lines(self) -> list[str]:
        """Return the per-position actual transcripts in BASE order.

        These are the lines M3 sees in STEP 3. No BASE words for
        deleted / replaced positions. No edit-marker prose.
        """
        return [line.text for line in self.lines if line.text.strip()]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _branch_shot_for_base(alignment: AlignmentResult, base_seq: int) -> tuple[str, int | None]:
    """Return (operation, branch_sequence_index) for a BASE position.

    The operation is the most-severe edit the alignment applied
    to this BASE position (delete > replace > trim > unchanged).
    Unmatched BASE positions return ``("unchanged", None)``.

    The branch_sequence_index is the branch shot the alignment
    paired with the BASE position (``None`` for delete).
    """
    op: str = "unchanged"
    bseq: int | None = None
    for m in alignment.matches:
        if m.base_shot is None or m.base_shot.sequence_index != base_seq:
            continue
        # Priority: delete > replace > trim > (unchanged/uncertain).
        # A position can appear in multiple matches only via
        # `insert` semantics, which are out of scope here.
        cur = m.operation
        if cur == "delete":
            return ("delete", None)
        if cur == "replace":
            if op in {"delete", "replace"}:
                continue
            op = "replace"
            bseq = m.branch_shot.sequence_index if m.branch_shot is not None else None
            continue
        if cur == "trim":
            if op in {"delete", "replace", "trim"}:
                continue
            op = "trim"
            bseq = m.branch_shot.sequence_index if m.branch_shot is not None else None
            continue
        if cur in {"unchanged", "uncertain"} and bseq is None:
            op = cur
            bseq = m.branch_shot.sequence_index if m.branch_shot is not None else None
    return (op, bseq)


def _branch_shot_text(branch: VideoRepresentation, branch_seq: int | None) -> str:
    """Return the ASR transcript for a branch shot by its sequence index.

    Empty string when the index is ``None`` or out of range.

    Note: this is a FALLBACK that should NOT be used when an
    ``AlignmentMatch`` is available — the alignment's
    ``branch_shot`` carries the canonical ASR transcript for the
    paired branch shot (which may have been re-encoded /
    re-transcribed by the alignment layer). The orchestrator
    uses ``_alignment_branch_shot_text`` instead, which reads
    directly from the alignment match.
    """
    if branch_seq is None:
        return ""
    if branch_seq < 0 or branch_seq >= len(branch.shots):
        return ""
    return branch.shots[branch_seq].transcript or ""


def _alignment_branch_shot_text(
    alignment: AlignmentResult,
    base_seq: int,
    operation: str,
) -> str:
    """Read the canonical replacement / trimmed transcript from
    the alignment match for ``(base_seq, operation)``.

    The Phase 3 alignment layer copies the branch shot's ASR
    transcript onto the alignment's ``branch_shot.normalized_transcript``
    (and the orchestrator's synthetic fixtures use the same
    field). Reading from there — instead of from
    ``branch_video.shots[branch_seq]`` — guarantees the actual
    wording the branch's TTS / ASR produced is what the
    reconstruction surfaces, regardless of how the branch
    video's own shot list is ordered.
    """
    for m in alignment.matches:
        if (
            m.base_shot is not None
            and m.base_shot.sequence_index == base_seq
            and m.operation == operation
            and m.branch_shot is not None
        ):
            return m.branch_shot.normalized_transcript or ""
    return ""


def _metadata_for_branch(branch_name: str, alignment: AlignmentResult) -> list[EditMetadataEntry]:
    """Build the metadata entries for one branch's alignment.

    Records every ``delete`` / ``replace`` / ``trim`` match. We do
    not record ``unchanged`` / ``uncertain`` because they carry
    no decision M3 needs to know about — M3 reads the actual
    content lines and decides preserved/degraded/broken from the
    text alone.
    """
    out: list[EditMetadataEntry] = []
    for m in alignment.matches:
        if m.base_shot is None:
            continue
        if m.operation not in {"delete", "replace", "trim"}:
            continue
        out.append(
            EditMetadataEntry(
                branch=branch_name,  # type: ignore[arg-type]
                base_sequence_index=m.base_shot.sequence_index,
                operation=m.operation,
                confidence=m.confidence,
                branch_shot_sequence_index=(
                    m.branch_shot.sequence_index if m.branch_shot is not None else None
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Per-branch reconstruction.
# ---------------------------------------------------------------------------


def reconstruct_branch_actual_content(
    *,
    branch_name: str,
    base: VideoRepresentation,
    branch_alignment: AlignmentResult,
    branch_video: VideoRepresentation,
) -> ReconstructedActualContent:
    """Reconstruct one branch's actual candidate content.

    Same algorithm as the helper above but accepts BASE so the
    unchanged / uncertain positions use the aligned branch shot's
    transcript. BASE text is only a fallback for a position that
    has no aligned branch shot at all.
    """
    lines: list[ActualContentLine] = []
    for idx, base_shot in enumerate(base.shots):
        base_text = base_shot.transcript or ""
        op, _bseq = _branch_shot_for_base(branch_alignment, idx)
        if op == "delete":
            text = ""
            deleted = True
        elif op == "replace":
            text = _alignment_branch_shot_text(branch_alignment, idx, "replace")
            deleted = False
        elif op == "trim":
            text = _alignment_branch_shot_text(branch_alignment, idx, "trim")
            deleted = False
        else:  # unchanged / uncertain / no match
            text = _alignment_branch_shot_text(branch_alignment, idx, op)
            if not text:
                text = _branch_shot_text(branch_video, _bseq) or base_text
            deleted = False
        lines.append(
            ActualContentLine(
                base_sequence_index=idx,
                base_text=base_text,
                text=text,
                operation=op,  # type: ignore[arg-type]
                deleted=deleted,
            )
        )
    metadata = EditMetadata(entries=_metadata_for_branch(branch_name, branch_alignment))
    return ReconstructedActualContent(branch=branch_name, lines=lines, edit_metadata=metadata)


# ---------------------------------------------------------------------------
# Combined reconstruction.
# ---------------------------------------------------------------------------


def reconstruct_combined_actual_content(
    *,
    a_alignment: AlignmentResult,
    b_alignment: AlignmentResult,
    branch_a: VideoRepresentation,
    branch_b: VideoRepresentation,
    base: VideoRepresentation,
) -> ReconstructedActualContent:
    """Reconstruct the A+B combined actual content.

    Public API unchanged. The internal path is now
    BASE-anchored: this helper builds one ``BaseShotRecord``
    per BASE shot and one ``EditSet`` per branch from the
    Phase 3 alignments, then delegates to
    ``compose_combined`` from ``app.services.merge.provenance``.
    The composer iterates BASE only and looks up edits by
    BASE identity, never by current branch sequence position.

    The returned ``ReconstructedActualContent`` carries:

      - ``lines`` (1:1 with BASE shots): the actual content the
        viewer hears at each BASE position in the combined
        video. ``combined_text`` is empty for ``deleted`` /
        ``unresolved`` slices; those slots are not contaminated
        with BASE wording or edit markers.
      - ``edit_metadata``: a forensic audit of every edit the
        combined rendering applied (delete / replace / trim).
        M3 never sees this; it lives on the side.
      - ``combined_timeline``: the explicit ``CombinedTimeline``
        from the composer so forensic consumers can inspect
        every slice verdict (``preserved`` / ``replaced`` /
        ``trimmed`` / ``deleted`` / ``unresolved``), the
        per-position ``unit_a`` / ``unit_b`` provenance, and
        any ``unresolved_inserts`` (unanchored inserts preserved
        separately on ``EditSet`` and surfaced here for
        mechanical-conflict disposition).

    Composition rules (verbatim from the user's brief):

      - One-sided (one branch touched the position): the
        touching branch's edit wins. ``Edit + unchanged`` always
        follows the rule table.
      - Same-base compatible pairs (``delete+delete``, identical
        replacement text, identical trim text) MAY compose.
      - Same-base incompatible pairs (``delete+replace``,
        ``replace+trim``, ``trim+replace``, ``delete+trim``,
        ``trim+delete``, and differing trims / replaces) return
        explicit ``unresolved`` with empty candidate text. No
        invented winner.
      - Unanchored inserts return ``unresolved`` with the insert
        provenance carried on the slice.
    """
    del branch_a  # branch_video is unused here; the canonical
    del branch_b  # transcript source is the alignment's branch_shot.
    base_units, base_text_for = _build_base_records(base)

    # Build EditSets from the Phase 3 alignments. The composer
    # keys by BASE identity; current branch sequence position is
    # recorded on the unit but never used as a lookup key.
    set_a = build_edit_set(branch="branch_a", alignment=a_alignment)
    set_b = build_edit_set(branch="branch_b", alignment=b_alignment)
    combined_timeline = compose_combined(
        base_units=base_units,
        set_a=set_a,
        set_b=set_b,
    )

    lines: list[ActualContentLine] = []
    entries: list[EditMetadataEntry] = []
    for slice_ in combined_timeline.slices:
        idx = slice_.base_index
        base_text = base_text_for[idx]
        if slice_.verdict == "preserved":
            lines.append(
                ActualContentLine(
                    base_sequence_index=idx,
                    base_text=base_text,
                    text=slice_.combined_text,
                    operation="unchanged",
                    deleted=False,
                )
            )
        elif slice_.verdict == "replaced":
            unit = slice_.unit_a or slice_.unit_b
            bseq = unit.provenance.branch_sequence_position if unit is not None else None
            conf = unit.provenance.confidence if unit is not None else 0.0
            entries.append(
                EditMetadataEntry(
                    branch="combined",
                    base_sequence_index=idx,
                    operation="replace",
                    confidence=conf,
                    branch_shot_sequence_index=bseq,
                )
            )
            lines.append(
                ActualContentLine(
                    base_sequence_index=idx,
                    base_text=base_text,
                    text=slice_.combined_text,
                    operation="replace",
                    deleted=False,
                )
            )
        elif slice_.verdict == "trimmed":
            unit = slice_.unit_a or slice_.unit_b
            bseq = unit.provenance.branch_sequence_position if unit is not None else None
            conf = unit.provenance.confidence if unit is not None else 0.0
            entries.append(
                EditMetadataEntry(
                    branch="combined",
                    base_sequence_index=idx,
                    operation="trim",
                    confidence=conf,
                    branch_shot_sequence_index=bseq,
                )
            )
            lines.append(
                ActualContentLine(
                    base_sequence_index=idx,
                    base_text=base_text,
                    text=slice_.combined_text,
                    operation="trim",
                    deleted=False,
                )
            )
        elif slice_.verdict == "deleted":
            entries.append(
                EditMetadataEntry(
                    branch="combined",
                    base_sequence_index=idx,
                    operation="delete",
                    confidence=(
                        max(
                            slice_.unit_a.provenance.confidence
                            if slice_.unit_a is not None
                            else 0.0,
                            slice_.unit_b.provenance.confidence
                            if slice_.unit_b is not None
                            else 0.0,
                        )
                    ),
                    branch_shot_sequence_index=None,
                )
            )
            lines.append(
                ActualContentLine(
                    base_sequence_index=idx,
                    base_text=base_text,
                    text="",
                    operation="delete",
                    deleted=True,
                )
            )
        else:  # unresolved
            # No metadata entry; no candidate text. The forensic
            # data lives on the combined_timeline for downstream
            # consumers.
            lines.append(
                ActualContentLine(
                    base_sequence_index=idx,
                    base_text=base_text,
                    text="",
                    operation="uncertain",
                    deleted=False,
                )
            )
    metadata = EditMetadata(entries=entries)
    return ReconstructedActualContent(
        branch="combined",
        lines=lines,
        edit_metadata=metadata,
        combined_timeline=combined_timeline,
    )


def _build_base_records(
    base: VideoRepresentation,
) -> tuple[list[BaseShotRecord], dict[int, str]]:
    """Convert a BASE ``VideoRepresentation`` into the
    ``BaseShotRecord`` list the composer consumes.

    Also returns a ``base_index → base_text`` map for fast
    forensic lookup; the composer itself only needs the
    ``BaseShotRecord`` shape (BASE shot id + range + text).
    """
    records: list[BaseShotRecord] = []
    texts: dict[int, str] = {}
    for idx, shot in enumerate(base.shots):
        text = shot.transcript or ""
        records.append(
            BaseShotRecord(
                base_index=idx,
                base_shot_id=shot.shot_id,
                base_range=ShotRange(start=shot.start, end=shot.end),
                base_text=text,
            )
        )
        texts[idx] = text
    return records, texts


def _alignment_matches_confidence(alignment: AlignmentResult, base_seq: int) -> float:
    """Return the confidence of the matching ``delete`` /
    ``replace`` / ``trim`` match for a BASE position.

    Falls back to 0.0 when the alignment has no such match (defensive).
    """
    best = 0.0
    for m in alignment.matches:
        if m.base_shot is None or m.base_shot.sequence_index != base_seq:
            continue
        if m.operation in {"delete", "replace", "trim"}:
            if m.confidence > best:
                best = m.confidence
    return best


# ---------------------------------------------------------------------------
# BASE actual content (the BASE itself, in shot order).
# ---------------------------------------------------------------------------


def reconstruct_base_actual_content(
    base: VideoRepresentation,
) -> ReconstructedActualContent:
    """The BASE actual content (1:1 with ``base.shots``).

    Provided so the diagnostic JSON can emit the four snapshots
    (BASE, branch_a, branch_b, combined) from a single helper.
    No edits applied; every line is ``operation='unchanged'``,
    ``deleted=False``, ``text == base_text``.
    """
    lines = [
        ActualContentLine(
            base_sequence_index=i,
            base_text=shot.transcript or "",
            text=shot.transcript or "",
            operation="unchanged",
            deleted=False,
        )
        for i, shot in enumerate(base.shots)
    ]
    return ReconstructedActualContent(branch="base", lines=lines, edit_metadata=EditMetadata())


# ---------------------------------------------------------------------------
# Diagnostic JSON writer.
# ---------------------------------------------------------------------------


def write_representation_diagnostics(
    *,
    base: VideoRepresentation,
    branch_a: VideoRepresentation,
    branch_b: VideoRepresentation,
    a_alignment: AlignmentResult,
    b_alignment: AlignmentResult,
    out_path: Path,
    base_label: str = "BASE",
    branch_a_label: str = "branch_a",
    branch_b_label: str = "branch_b",
) -> Path:
    """Write the four actual-content snapshots (BASE / A / B / combined).

    Each snapshot has:

      - ``branch``         : one of base / branch_a / branch_b / combined.
      - ``lines``          : ordered list of
        ``{base_sequence_index, base_text, text, operation, deleted}``.
      - ``text_lines``     : just the ``text`` values in order.
      - ``edit_metadata``  : the edits applied (empty for BASE).

    The writer is deterministic: same inputs always produce the
    same JSON. The function returns ``out_path`` so callers can
    chain.
    """
    base_content = reconstruct_base_actual_content(base)
    a_content = reconstruct_branch_actual_content(
        branch_name="branch_a",
        base=base,
        branch_alignment=a_alignment,
        branch_video=branch_a,
    )
    b_content = reconstruct_branch_actual_content(
        branch_name="branch_b",
        base=base,
        branch_alignment=b_alignment,
        branch_video=branch_b,
    )
    c_content = reconstruct_combined_actual_content(
        a_alignment=a_alignment,
        b_alignment=b_alignment,
        branch_a=branch_a,
        branch_b=branch_b,
        base=base,
    )

    payload = {
        base_label: _snapshot_to_dict(base_content, base_label),
        branch_a_label: _snapshot_to_dict(a_content, branch_a_label),
        branch_b_label: _snapshot_to_dict(b_content, branch_b_label),
        "combined": _snapshot_to_dict(c_content, "combined"),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return out_path


def _snapshot_to_dict(content: ReconstructedActualContent, label: str) -> dict[str, object]:
    """Render one ``ReconstructedActualContent`` as JSON-safe dict."""
    snapshot: dict[str, object] = {
        "label": label,
        "branch": content.branch,
        "lines": [
            {
                "base_sequence_index": line.base_sequence_index,
                "text": line.text,
                "operation": line.operation,
                "deleted": line.deleted,
            }
            for line in content.lines
            if line.text.strip()
        ],
        "text_lines": content.text_lines(),
        "edit_metadata": [e.model_dump() for e in content.edit_metadata.entries],
    }
    if content.combined_timeline is not None:
        snapshot["combined_timeline"] = content.combined_timeline.model_dump(mode="json")
    return snapshot


__all__ = [
    "ActualContentLine",
    "EditMetadataEntry",
    "EditMetadata",
    "ReconstructedActualContent",
    "reconstruct_base_actual_content",
    "reconstruct_branch_actual_content",
    "reconstruct_combined_actual_content",
    "write_representation_diagnostics",
]
