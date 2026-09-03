"""Phase 3.5 — provenance-aware BASE-anchored cross-branch composition.

Root problem (from the user's brief):

  The previous combined reconstruction iterated the *branch's*
  reconstructed timeline. When a branch deletes an earlier shot,
  every later shot's branch position shifts by one. Cross-branch
  composition that used the *current* branch position as BASE
  identity therefore mis-keyed later edits (delete in A shifts B's
  current position, so the composition pairs B's shot N with
  BASE's shot N+1 — the canonical prerequisite-loss case becomes
  a different edit).

Phase 3.5 fix:

  Every aligned branch edit is recorded with its **immutable BASE
  provenance** — `base_shot_id`, `base_index`, `base_start`,
  `base_end`, plus the branch's current position. The combined
  pipeline iterates **BASE**, never the branch's reconstructed
  timeline, and looks up EditSet A and EditSet B by BASE identity.
  Current sequence position in the branch is metadata, never the
  lookup key.

The composition rules (verbatim from the brief):

  1. The combined content is built by iterating BASE.
  2. For each BASE position, look up EditSet A and EditSet B keyed
     by `base_index`. Never use current branch position.
  3. One-sided behaviors compose with required semantics:
       - unchanged    : take BASE text.
       - delete       : absent (no content).
       - replace      : take the branch's actual retained /
                        replacement text (read from the
                        ``branch_shot`` provenance).
       - trim         : take the branch's trimmed text.
  4. Independent edits to different BASE positions compose.
  5. Incompatible dual edits to the SAME BASE position return
     an explicit ``unresolved`` result — never invent a winner.
  6. Insert edits, when encountered, must have a stable BASE
     anchor. If the anchor cannot be resolved deterministically
     the position is returned as ``unresolved``.

The module is **strictly deterministic** and **does not call
M3**. It is the Phase 3 / 6 determinism boundary, the same way
``interact.py`` is the Phase 4 determinism boundary.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.alignment import AlignmentMatch, AlignmentResult, EditOperationType

# ---------------------------------------------------------------------------
# Edit operation kinds (the merge-side taxonomy, distinct from the
# Phase 3 alignment operation literal).
# ---------------------------------------------------------------------------


class EditKind(StrEnum):
    """The merge-side taxonomy of one-sided edit behaviors.

    Kept separate from ``EditOperationType`` so the Phase 3
    alignment taxonomy (which carries ``uncertain`` and ``insert``
    as placeholders) does not contaminate the composition rules.
    """

    UNCHANGED = "unchanged"
    DELETE = "delete"
    REPLACE = "replace"
    TRIM = "trim"


# Verdict for the combined composition at a single BASE position.
# ``unresolved`` is the explicit "no invented winner" verdict when
# two branches both edit the same BASE position incompatibly.
CompositionVerdict = Literal[
    "preserved",
    "replaced",
    "trimmed",
    "deleted",
    "unresolved",
]


# ---------------------------------------------------------------------------
# Provenance models.
# ---------------------------------------------------------------------------


class ShotRange(BaseModel):
    """A half-open start/end interval in seconds."""

    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class BranchShotProvenance(BaseModel):
    """Immutable provenance for a single aligned branch unit.

    Distinct fields:

    - ``base_shot_id`` / ``base_index`` : BASE identity (stable).
    - ``branch_shot_id``               : branch identity (current).
    - ``branch_sequence_position``     : the branch shot's current
                                        sequence position. This
                                        changes when earlier shots
                                        are deleted. It is a
                                        diagnostic, **never** the
                                        lookup key.
    - ``base_range``                   : BASE start/end in seconds.
    - ``branch_range``                 : branch start/end in seconds.
    - ``operation``                    : inferred one-sided edit.
    - ``retained_text`` / ``replacement_text`` : the actual content
                                                the branch kept /
                                                inserted.
    - ``confidence``                   : confidence in the inferred
                                        operation.
    """

    model_config = ConfigDict(extra="forbid")

    base_shot_id: str
    base_index: int = Field(
        ge=-1,
        description=(
            "BASE identity (stable). -1 for unanchored inserts "
            "preserved on EditSet.unanchored_inserts; >= 0 for "
            "BASE-anchored units."
        ),
    )
    base_range: ShotRange
    branch: str
    branch_shot_id: str | None = None
    branch_sequence_position: int | None = Field(
        default=None,
        ge=0,
        description=(
            "The branch shot's current position in the branch "
            "timeline. Diagnostic only; NOT the lookup key."
        ),
    )
    branch_range: ShotRange | None = None
    operation: EditKind
    retained_text: str = Field(
        default="",
        description=(
            "For UNCHANGED / TRIM: the actual text the branch kept "
            "(the verbatim ASR transcript at the branch position). "
            "Empty for DELETE."
        ),
    )
    replacement_text: str = Field(
        default="",
        description=(
            "For REPLACE: the actual replacement text the branch "
            "inserted (verbatim ASR transcript of the branch shot). "
            "Empty otherwise."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0)


class EditUnit(BaseModel):
    """A single one-sided branch edit with full provenance.

    An ``EditUnit`` is the merge-side analogue of an
    ``AlignmentMatch``. It carries BASE identity (stable), branch
    identity (current), the actual retained / replacement content,
    and the inferred one-sided edit kind.

    A single ``EditUnit`` per (branch, base_index) — the union of
    two ``EditUnit``s for the same base_index is a cross-branch
    interaction, not a single ``EditUnit``.
    """

    model_config = ConfigDict(extra="forbid")

    provenance: BranchShotProvenance

    @property
    def base_index(self) -> int:
        return self.provenance.base_index

    @property
    def base_shot_id(self) -> str:
        return self.provenance.base_shot_id

    @property
    def branch(self) -> str:
        return self.provenance.branch

    @property
    def operation(self) -> EditKind:
        return self.provenance.operation

    @property
    def retained_text(self) -> str:
        return self.provenance.retained_text

    @property
    def replacement_text(self) -> str:
        return self.provenance.replacement_text

    @property
    def confidence(self) -> float:
        return self.provenance.confidence


class InsertUnit(BaseModel):
    """A branch insert that has no stable BASE anchor.

    Inserts are preserved separately on ``EditSet.unanchored_inserts``
    (NOT keyed by ``base_index``). They cannot be composed into
    the BASE-anchored ``CombinedTimeline`` by definition — there is
    no BASE position to attach them to — so the composer surfaces
    them as explicit ``unresolved`` mechanical conflicts and
    records their full provenance on the corresponding slice for
    downstream resolution.
    """

    model_config = ConfigDict(extra="forbid")

    provenance: BranchShotProvenance = Field(
        description=(
            "Full provenance for the insert (branch identity + "
            "branch_sequence_position + branch_range + branch "
            "ASR transcript + confidence). The BASE fields on the "
            "provenance are placeholders (base_index = -1, "
            "base_shot_id = '')."
        ),
    )


class EditSet(BaseModel):
    """One branch's edits, indexed by BASE identity.

    ``units`` is the set of aligned units for the branch —
    every BASE position the alignment visited. ``unanchored_inserts``
    is the set of branch inserts that have no BASE anchor; they
    are preserved separately so the composer can surface an
    explicit mechanical unresolved conflict for each (rather than
    silently dropping them).

    The set is keyed by ``base_index`` at construction time
    (``by_base_index``); consumers MUST NOT key by
    ``branch_sequence_position`` because that position shifts when
    earlier units are deleted.
    """

    model_config = ConfigDict(extra="forbid")

    branch: str
    units: list[EditUnit] = Field(default_factory=list)
    unanchored_inserts: list[InsertUnit] = Field(default_factory=list)

    def by_base_index(self) -> dict[int, EditUnit]:
        """Index by BASE identity — the ONLY supported lookup key.

        Raises ``ValueError`` if the same ``base_index`` appears
        twice in the set. This is a programming error, not a
        composition outcome.
        """
        out: dict[int, EditUnit] = {}
        for unit in self.units:
            if unit.base_index in out:
                raise ValueError(
                    f"Duplicate base_index={unit.base_index} in EditSet for branch {self.branch!r}"
                )
            out[unit.base_index] = unit
        return out

    def ordered_by_base_index(self) -> list[EditUnit]:
        """Return units sorted by base_index (stable order)."""
        return sorted(self.units, key=lambda u: u.base_index)


# ---------------------------------------------------------------------------
# Combined position result.
# ---------------------------------------------------------------------------


class ProvenanceSlice(BaseModel):
    """One BASE position in the combined timeline.

    Records the BASE identity, the per-branch ``EditUnit`` (or
    None when the branch had no aligned unit at this position),
    any unanchored inserts encountered at this BASE position
    (carried as forensic data, never mixed into the candidate
    text), the resulting combined text (or empty when deleted /
    unresolved), the verdict, and any provenance carried forward
    from the contributing units.
    """

    model_config = ConfigDict(extra="forbid")

    base_index: int = Field(ge=0)
    base_shot_id: str
    base_range: ShotRange
    base_text: str
    unit_a: EditUnit | None = None
    unit_b: EditUnit | None = None
    unanchored_inserts: list[InsertUnit] = Field(
        default_factory=list,
        description=(
            "Unanchored inserts encountered at this BASE position. "
            "Carried as forensic data; never mixed into "
            "combined_text. Each entry triggers a "
            "verdict='unresolved' slice with empty combined_text."
        ),
    )
    verdict: CompositionVerdict
    combined_text: str = Field(
        default="",
        description=(
            "The actual text the viewer hears in the combined video "
            "at this BASE position. Empty when the position is "
            "deleted or unresolved."
        ),
    )
    reason: str = Field(
        default="",
        description=(
            "Short human-readable explanation of the verdict. "
            "Includes the BASE identity, the contributing unit "
            "operations, and (for unresolved) the conflict that "
            "prevented composition."
        ),
    )


class CombinedTimeline(BaseModel):
    """The combined BASE-anchored timeline.

    Iterating ``slices`` in order yields the combined content
    position-by-position in BASE order. Slices whose ``verdict``
    is ``deleted`` or ``unresolved`` carry empty
    ``combined_text``; the forensic consumer skips them.

    ``unresolved_inserts`` collects every unanchored insert from
    EditSet A and EditSet B that did not fit a BASE position. The
    timeline is built exclusively by iterating BASE and resolving
    EditSet A / EditSet B keyed by BASE identity; it never merges
    reconstructed branch timelines.
    """

    model_config = ConfigDict(extra="forbid")

    slices: list[ProvenanceSlice] = Field(default_factory=list)
    unresolved_inserts: list[InsertUnit] = Field(default_factory=list)

    def text_lines(self) -> list[str]:
        """Return the combined transcript lines (verbatim, in BASE order).

        Empty slices (deleted / unresolved) are omitted so the
        output is the same shape as the per-branch ``text_lines``
        view in ``represent.py``.
        """
        return [s.combined_text for s in self.slices if s.combined_text.strip()]

    def non_empty_slices(self) -> list[ProvenanceSlice]:
        return [s for s in self.slices if s.combined_text.strip()]


# ---------------------------------------------------------------------------
# EditSet construction.
# ---------------------------------------------------------------------------


def _to_edit_kind(operation: EditOperationType | str) -> EditKind | None:
    """Map a Phase 3 alignment operation to a merge-side EditKind.

    Returns ``None`` for ``insert``, ``move``, and ``uncertain``
    — the merge-side composition does not include insert
    semantics (inserts have no stable BASE anchor and are
    preserved separately on ``EditSet.unanchored_inserts``).
    For ``uncertain`` matches with a BASE anchor, the
    EditSet builder conservatively records them as
    ``EditKind.UNCHANGED`` so the BASE text is retained.
    """
    if operation == "unchanged":
        return EditKind.UNCHANGED
    if operation == "delete":
        return EditKind.DELETE
    if operation == "replace":
        return EditKind.REPLACE
    if operation == "trim":
        return EditKind.TRIM
    return None


def build_edit_set(
    *,
    branch: str,
    alignment: AlignmentResult,
) -> EditSet:
    """Construct an ``EditSet`` for one branch from an ``AlignmentResult``.

    The construction is the single source of truth for the
    provenance payload (requirement 1 of the user's brief). It:

      - Iterates ``alignment.matches`` and emits one
        ``BranchShotProvenance`` per match.
      - Records both BASE identity (stable) and branch identity
        (current), with the branch's *current* sequence position
        recorded as a diagnostic that is NEVER used as a lookup
        key in the composer.
      - Preserves inserts on ``EditSet.unanchored_inserts``
        (they have no BASE anchor and cannot be composed by
        ``compose_combined``; the composer surfaces them as
        explicit mechanical ``unresolved`` conflicts).
      - Records ``uncertain`` matches as ``EditKind.UNCHANGED``
        while retaining the aligned branch shot's actual transcript.
      - Validates that every recorded unit has a unique
        ``base_index`` (defensive; the DP does not duplicate
        BASE positions).

    The function is deterministic and has no M3 dependency.
    """
    units: list[EditUnit] = []
    inserts: list[InsertUnit] = []
    seen_indices: set[int] = set()

    for match in alignment.matches:
        if match.base_shot is None:
            insert = _match_to_insert(branch=branch, match=match)
            if insert is not None:
                inserts.append(insert)
            continue
        unit = _match_to_unit(branch=branch, match=match)
        if unit is None:
            continue
        if unit.base_index in seen_indices:
            raise ValueError(
                f"Alignment for branch {branch!r} produced duplicate base_index="
                f"{unit.base_index}; the DP must not duplicate BASE positions."
            )
        seen_indices.add(unit.base_index)
        units.append(unit)

    return EditSet(branch=branch, units=units, unanchored_inserts=inserts)


def _match_to_unit(*, branch: str, match: AlignmentMatch) -> EditUnit | None:
    """Convert one ``AlignmentMatch`` to one ``EditUnit`` (or None).

    Returns ``None`` only when the match cannot be represented
    as a BASE-anchored unit (currently never — inserts are
    handled separately by ``_match_to_insert``).
    """
    base = match.base_shot
    branch_shot = match.branch_shot
    if base is None:
        return None
    kind = _to_edit_kind(match.operation)

    base_range = ShotRange(start=base.start, end=base.end)
    branch_range: ShotRange | None = None
    if branch_shot is not None:
        branch_range = ShotRange(start=branch_shot.start, end=branch_shot.end)

    retained_text = ""
    replacement_text = ""
    if kind == EditKind.REPLACE:
        # The actual replacement content is the branch shot's
        # verbatim ASR transcript. Reading from the branch shot
        # is critical — the BASE shot's transcript at this index
        # is the OLD wording, never the replacement.
        replacement_text = branch_shot.normalized_transcript if branch_shot is not None else ""
    elif kind == EditKind.TRIM:
        retained_text = branch_shot.normalized_transcript if branch_shot is not None else ""
    elif kind == EditKind.UNCHANGED:
        # Provenance describes the candidate branch, not the BASE
        # reference. Even an unchanged alignment must retain the
        # transcript actually observed on the aligned branch shot.
        retained_text = (
            branch_shot.normalized_transcript
            if branch_shot is not None
            else base.normalized_transcript
        )

    provenance = BranchShotProvenance(
        base_shot_id=base.shot_id,
        base_index=base.sequence_index,
        base_range=base_range,
        branch=branch,
        branch_shot_id=branch_shot.shot_id if branch_shot is not None else None,
        branch_sequence_position=(branch_shot.sequence_index if branch_shot is not None else None),
        branch_range=branch_range,
        operation=kind if kind is not None else EditKind.UNCHANGED,
        retained_text=retained_text,
        replacement_text=replacement_text,
        confidence=match.confidence,
    )
    return EditUnit(provenance=provenance)


def _match_to_insert(*, branch: str, match: AlignmentMatch) -> InsertUnit | None:
    """Convert an insert ``AlignmentMatch`` (``base_shot is None``)
    to an ``InsertUnit``.

    The BASE-side provenance fields on the returned
    ``BranchShotProvenance`` are placeholders (``base_shot_id=''``,
    ``base_index=-1``) so consumers can detect "no BASE anchor" by
    inspecting the field rather than parsing a separate type.
    """
    branch_shot = match.branch_shot
    if branch_shot is None:
        return None
    provenance = BranchShotProvenance(
        base_shot_id="",
        base_index=-1,
        base_range=ShotRange(start=0.0, end=0.0),
        branch=branch,
        branch_shot_id=branch_shot.shot_id,
        branch_sequence_position=branch_shot.sequence_index,
        branch_range=ShotRange(start=branch_shot.start, end=branch_shot.end),
        operation=EditKind.UNCHANGED,
        retained_text=branch_shot.normalized_transcript,
        replacement_text="",
        confidence=match.confidence,
    )
    return InsertUnit(provenance=provenance)


# ---------------------------------------------------------------------------
# Combined composition.
# ---------------------------------------------------------------------------


class BaseShotRecord(BaseModel):
    """The BASE-side provenance consumed by the composer.

    Carries the immutable BASE identity (shot id + index) plus
    the BASE text the composer falls back to when both branches
    leave a position unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    base_index: int = Field(ge=0)
    base_shot_id: str
    base_range: ShotRange
    base_text: str


def compose_combined(
    *,
    base_units: list[BaseShotRecord],
    set_a: EditSet,
    set_b: EditSet,
) -> CombinedTimeline:
    """Compose the combined timeline by iterating BASE only.

    Algorithm (verbatim from the brief):

      1. Build a ``base_index → EditUnit`` dict for each EditSet
         via ``EditSet.by_base_index()``. The composer MUST NOT
         use ``branch_sequence_position`` as a lookup key; that
         is the defect the Phase 3.5 repair is fixing.
      2. For every BASE position:
           - If neither branch has a unit at this position, the
             position is preserved (BASE text).
           - If exactly one branch has a unit, apply its
             one-sided behavior (the touching branch's edit
             wins; the other branch is recorded in ``reason``
             but does not modify the verdict).
           - If both branches have a unit, check compatibility
             via the rule table below and either compose the
             result or return ``unresolved`` (no invented
             winner). Unanchored inserts from either EditSet
             that attach to this BASE position (by their
             branch_sequence_position's adjacency at composer
             time) trigger an explicit ``unresolved`` verdict
             carrying the insert provenance as forensic data.
      3. Compatibility rules for two branches touching the same
         BASE position. "Compatible" means the two edits agree
         exactly; "incompatible" means they disagree in a way
         the brief forbids resolving:

         | A op      | B op      | Verdict                       |
         | --------- | --------- | ----------------------------- |
         | unchanged | unchanged | preserved (BASE text)         |
         | delete    | unchanged | deleted (A wins)              |
         | unchanged | delete    | deleted (B wins)              |
         | delete    | delete    | deleted (both agree)          |
         | replace   | unchanged | replaced (A's text)           |
         | unchanged | replace   | replaced (B's text)           |
         | trim      | unchanged | trimmed (A's text)            |
         | unchanged | trim      | trimmed (B's text)            |
         | replace   | replace   | replaced with identical text  |
         |           |           | (same text)                   |
         | replace   | replace   | UNRESOLVED (different text)   |
         | replace   | delete    | UNRESOLVED (delete+replace)   |
         | delete    | replace   | UNRESOLVED (delete+replace)   |
         | replace   | trim      | UNRESOLVED                    |
         | trim      | replace   | UNRESOLVED                    |
         | delete    | trim      | UNRESOLVED                    |
         | trim      | delete    | UNRESOLVED                    |
         | trim      | trim      | trimmed with identical text   |
         | trim      | trim      | UNRESOLVED (different text)   |

    Per the brief, the following same-base pairs are
    **incompatible** and the composer MUST NOT choose a winner:

      - delete + replace / replace + delete
      - delete + trim / trim + delete
      - replace + trim / trim + replace
      - differing trims (trim + trim with different text)
      - differing replaces (replace + replace with different text)

    Compatible same-base pairs MAY resolve only when their
    effects agree (delete+delete; identical replacement text;
    identical trim text). Edit + unchanged always follows the
    rule table above (the touching branch's edit wins).

    The composer is deterministic; same inputs always produce
    the same output. The composer never calls M3 and never
    touches the branch's reconstructed timeline.
    """
    index_a = set_a.by_base_index()
    index_b = set_b.by_base_index()

    # Unanchored inserts: stable BASE anchor means a
    # branch_sequence_position mapping onto a real BASE index.
    # We treat an insert that lives between BASE positions N and
    # N+1 as attached to BASE[N] for forensic disposition. The
    # composer surfaces an explicit unresolved verdict per
    # attached insert.
    inserts_a = _attach_inserts_to_base(set_a.unanchored_inserts, base_units)
    inserts_b = _attach_inserts_to_base(set_b.unanchored_inserts, base_units)

    unresolved_orphan_inserts: list[InsertUnit] = []
    slices: list[ProvenanceSlice] = []
    for base in sorted(base_units, key=lambda r: r.base_index):
        unit_a = index_a.get(base.base_index)
        unit_b = index_b.get(base.base_index)
        slice_inserts: list[InsertUnit] = []
        slice_inserts.extend(inserts_a.get(base.base_index, []))
        slice_inserts.extend(inserts_b.get(base.base_index, []))
        slice_ = _compose_position(
            base=base,
            unit_a=unit_a,
            unit_b=unit_b,
            slice_inserts=slice_inserts,
        )
        slices.append(slice_)

    # Collect inserts that did not attach to any BASE position
    # (e.g. all inserts come after the last BASE shot).
    seen_anchors = {b.base_index for b in base_units}
    for inserts in (inserts_a, inserts_b):
        for bi, items in inserts.items():
            if bi in seen_anchors:
                continue
            unresolved_orphan_inserts.extend(items)

    return CombinedTimeline(
        slices=slices,
        unresolved_inserts=unresolved_orphan_inserts,
    )


def _attach_inserts_to_base(
    inserts: list[InsertUnit],
    base_units: list[BaseShotRecord],
) -> dict[int, list[InsertUnit]]:
    """Attach each unanchored insert to the nearest preceding BASE position.

    A stable BASE anchor here is the BASE position whose
    ``base_range.end`` is closest to (and <=) the insert's
    ``branch_range.start``. If the insert precedes all BASE shots
    it is attached to the first BASE position (the user said
    "stable BASE anchor if represented"; we use the closest
    preceding BASE shot as that anchor). The mapping is
    deterministic and never crosses BASE identity.
    """
    if not inserts:
        return {}
    sorted_base = sorted(base_units, key=lambda r: r.base_index)
    out: dict[int, list[InsertUnit]] = {}
    for ins in inserts:
        if ins.provenance.branch_range is None:
            continue
        ins_start = ins.provenance.branch_range.start
        anchor = sorted_base[0]
        for base in sorted_base:
            if base.base_range.end <= ins_start:
                anchor = base
            else:
                break
        out.setdefault(anchor.base_index, []).append(ins)
    return out


def _compose_position(
    *,
    base: BaseShotRecord,
    unit_a: EditUnit | None,
    unit_b: EditUnit | None,
    slice_inserts: list[InsertUnit] | None = None,
) -> ProvenanceSlice:
    """Compose one BASE position. Pure function; deterministic."""
    slice_inserts = slice_inserts or []
    # Case 0: no branch touched the position.
    if unit_a is None and unit_b is None and not slice_inserts:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=None,
            unit_b=None,
            unanchored_inserts=[],
            verdict="preserved",
            combined_text=base.base_text,
            reason="neither branch touched this BASE position",
        )

    # Case 1: only one branch touched the position.
    if unit_a is not None and unit_b is None and not slice_inserts:
        return _apply_one_sided(
            base=base,
            unit=unit_a,
            other_branch_label="branch_b",
        )
    if unit_b is not None and unit_a is None and not slice_inserts:
        return _apply_one_sided(
            base=base,
            unit=unit_b,
            other_branch_label="branch_a",
        )

    # Case 2: unanchored insert present. Always unresolved.
    if slice_inserts:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=slice_inserts,
            verdict="unresolved",
            combined_text="",
            reason=(
                f"unanchored insert(s) at BASE position "
                f"{base.base_shot_id}: "
                + ", ".join(
                    f"{i.provenance.branch}@{i.provenance.branch_sequence_position}"
                    for i in slice_inserts
                )
            ),
        )

    # Case 3: both branches touched the position.
    assert unit_a is not None and unit_b is not None
    return _resolve_dual(
        base=base,
        unit_a=unit_a,
        unit_b=unit_b,
    )


def _apply_one_sided(
    *,
    base: BaseShotRecord,
    unit: EditUnit,
    other_branch_label: str,
) -> ProvenanceSlice:
    """Apply one branch's one-sided behavior to a BASE position.

    The other branch is recorded in ``reason`` for diagnostics.
    """
    op = unit.operation
    if op == EditKind.DELETE:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit if unit.branch == "branch_a" else None,
            unit_b=unit if unit.branch == "branch_b" else None,
            unanchored_inserts=[],
            verdict="deleted",
            combined_text="",
            reason=(
                f"{unit.branch} deleted this BASE position "
                f"({unit.base_shot_id}); {other_branch_label} did not touch it"
            ),
        )
    if op == EditKind.REPLACE:
        text = unit.replacement_text
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit if unit.branch == "branch_a" else None,
            unit_b=unit if unit.branch == "branch_b" else None,
            unanchored_inserts=[],
            verdict="replaced",
            combined_text=text,
            reason=(
                f"{unit.branch} replaced this BASE position "
                f"({unit.base_shot_id}); {other_branch_label} did not touch it"
            ),
        )
    if op == EditKind.TRIM:
        text = unit.retained_text
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit if unit.branch == "branch_a" else None,
            unit_b=unit if unit.branch == "branch_b" else None,
            unanchored_inserts=[],
            verdict="trimmed",
            combined_text=text,
            reason=(
                f"{unit.branch} trimmed this BASE position "
                f"({unit.base_shot_id}); {other_branch_label} did not touch it"
            ),
        )
    # unchanged
    return ProvenanceSlice(
        base_index=base.base_index,
        base_shot_id=base.base_shot_id,
        base_range=base.base_range,
        base_text=base.base_text,
        unit_a=unit if unit.branch == "branch_a" else None,
        unit_b=unit if unit.branch == "branch_b" else None,
        unanchored_inserts=[],
        verdict="preserved",
        combined_text=base.base_text,
        reason=(
            f"{unit.branch} left this BASE position unchanged "
            f"({unit.base_shot_id}); {other_branch_label} did not touch it"
        ),
    )


def _resolve_dual(
    *,
    base: BaseShotRecord,
    unit_a: EditUnit,
    unit_b: EditUnit,
) -> ProvenanceSlice:
    """Resolve two-branch behavior at one BASE position.

    Returns ``unresolved`` (with empty combined_text) when the
    two edits are incompatible per the brief: any incompatible
    dual modification of the SAME BASE unit must be unresolved
    (no invented winner). Compatible pairs where the effects
    agree exactly (delete+delete, identical replacement text,
    identical trim text) MAY resolve. Edit + unchanged always
    follows the rule table (the touching branch's edit wins).
    """
    op_a = unit_a.operation
    op_b = unit_b.operation

    # ------------------------------------------------------------------
    # INCOMPATIBLE same-base pairs — the brief forbids choosing a
    # winner. Surface as explicit unresolved.
    # ------------------------------------------------------------------
    incompatible: list[tuple[EditKind, EditKind]] = [
        (EditKind.DELETE, EditKind.REPLACE),
        (EditKind.REPLACE, EditKind.DELETE),
        (EditKind.DELETE, EditKind.TRIM),
        (EditKind.TRIM, EditKind.DELETE),
        (EditKind.REPLACE, EditKind.TRIM),
        (EditKind.TRIM, EditKind.REPLACE),
    ]
    if (op_a, op_b) in incompatible:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="unresolved",
            combined_text="",
            reason=(
                f"incompatible same-base dual edits "
                f"({unit_a.base_shot_id}): branch_a={op_a.value}, "
                f"branch_b={op_b.value}; no deterministic winner"
            ),
        )

    # ------------------------------------------------------------------
    # Compatible same-base pairs.
    # ------------------------------------------------------------------

    # delete + delete  → deleted (both agree).
    if op_a == EditKind.DELETE and op_b == EditKind.DELETE:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="deleted",
            combined_text="",
            reason=(f"both branches deleted this BASE position ({unit_a.base_shot_id})"),
        )

    # delete + unchanged  → deleted (the touching branch's delete wins).
    if op_a == EditKind.DELETE and op_b == EditKind.UNCHANGED:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="deleted",
            combined_text="",
            reason=(
                f"branch_a deleted; branch_b left unchanged ({unit_a.base_shot_id}); delete wins"
            ),
        )

    if op_b == EditKind.DELETE and op_a == EditKind.UNCHANGED:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="deleted",
            combined_text="",
            reason=(
                f"branch_a left unchanged; branch_b deleted ({unit_a.base_shot_id}); delete wins"
            ),
        )

    # replace + unchanged  → replaced (the touching branch wins).
    if op_a == EditKind.REPLACE and op_b == EditKind.UNCHANGED:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="replaced",
            combined_text=unit_a.replacement_text,
            reason=(
                f"branch_a replaced; branch_b left unchanged "
                f"({unit_a.base_shot_id}); A's replacement wins"
            ),
        )

    if op_b == EditKind.REPLACE and op_a == EditKind.UNCHANGED:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="replaced",
            combined_text=unit_b.replacement_text,
            reason=(
                f"branch_a left unchanged; branch_b replaced "
                f"({unit_a.base_shot_id}); B's replacement wins"
            ),
        )

    # trim + unchanged  → trimmed.
    if op_a == EditKind.TRIM and op_b == EditKind.UNCHANGED:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="trimmed",
            combined_text=unit_a.retained_text,
            reason=(
                f"branch_a trimmed; branch_b left unchanged ({unit_a.base_shot_id}); trim wins"
            ),
        )

    if op_a == EditKind.UNCHANGED and op_b == EditKind.TRIM:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="trimmed",
            combined_text=unit_b.retained_text,
            reason=(
                f"branch_a left unchanged; branch_b trimmed ({unit_a.base_shot_id}); B's trim wins"
            ),
        )

    # replace + replace — same text wins, different text is unresolved.
    if op_a == EditKind.REPLACE and op_b == EditKind.REPLACE:
        text_a = unit_a.replacement_text
        text_b = unit_b.replacement_text
        if text_a == text_b:
            return ProvenanceSlice(
                base_index=base.base_index,
                base_shot_id=base.base_shot_id,
                base_range=base.base_range,
                base_text=base.base_text,
                unit_a=unit_a,
                unit_b=unit_b,
                unanchored_inserts=[],
                verdict="replaced",
                combined_text=text_a,
                reason=(
                    f"both branches replaced this BASE position "
                    f"({unit_a.base_shot_id}) with identical text"
                ),
            )
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="unresolved",
            combined_text="",
            reason=(
                f"both branches replaced this BASE position "
                f"({unit_a.base_shot_id}) with DIFFERENT text; "
                f"no deterministic winner"
            ),
        )

    # trim + trim — same text wins, different text is unresolved.
    if op_a == EditKind.TRIM and op_b == EditKind.TRIM:
        text_a = unit_a.retained_text
        text_b = unit_b.retained_text
        if text_a == text_b:
            return ProvenanceSlice(
                base_index=base.base_index,
                base_shot_id=base.base_shot_id,
                base_range=base.base_range,
                base_text=base.base_text,
                unit_a=unit_a,
                unit_b=unit_b,
                unanchored_inserts=[],
                verdict="trimmed",
                combined_text=text_a,
                reason=(
                    f"both branches trimmed this BASE position "
                    f"({unit_a.base_shot_id}) with identical text"
                ),
            )
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="unresolved",
            combined_text="",
            reason=(
                f"both branches trimmed this BASE position "
                f"({unit_a.base_shot_id}) with DIFFERENT text; "
                f"no deterministic winner"
            ),
        )

    # unchanged + unchanged: preserved.
    if op_a == EditKind.UNCHANGED and op_b == EditKind.UNCHANGED:
        return ProvenanceSlice(
            base_index=base.base_index,
            base_shot_id=base.base_shot_id,
            base_range=base.base_range,
            base_text=base.base_text,
            unit_a=unit_a,
            unit_b=unit_b,
            unanchored_inserts=[],
            verdict="preserved",
            combined_text=base.base_text,
            reason=(f"both branches left this BASE position unchanged ({unit_a.base_shot_id})"),
        )

    # ------------------------------------------------------------------
    # Anything else: unresolved (defensive — the rule table above
    # already covers every valid pair, so this is a true
    # incompatible case if it fires).
    # ------------------------------------------------------------------
    return ProvenanceSlice(
        base_index=base.base_index,
        base_shot_id=base.base_shot_id,
        base_range=base.base_range,
        base_text=base.base_text,
        unit_a=unit_a,
        unit_b=unit_b,
        unanchored_inserts=[],
        verdict="unresolved",
        combined_text="",
        reason=(
            f"incompatible dual edits at BASE position "
            f"{unit_a.base_shot_id}: branch_a={op_a.value}, "
            f"branch_b={op_b.value}"
        ),
    )


__all__ = [
    "BaseShotRecord",
    "BranchShotProvenance",
    "CombinedTimeline",
    "CompositionVerdict",
    "EditKind",
    "EditSet",
    "EditUnit",
    "InsertUnit",
    "ProvenanceSlice",
    "ShotRange",
    "build_edit_set",
    "compose_combined",
]
