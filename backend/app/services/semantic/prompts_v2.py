"""Phase 4 v3.0 system intent + user payload for the two-axis taxonomy.

The Phase 1 v2.1.0 contract (in `app.services.minimax.prompts`)
is text-only and uses a binary safe/unsafe model. Phase 4
replaces that contract with a richer schema that distinguishes:

  Axis 1 — `impact_level` ∈ {preserved, degraded, broken}
            (per branch, AND for the combined video)
  Axis 2 — `cross_edit_interaction` ∈
            {none, amplifies_existing_issue, creates_new_conflict}

The system intent tells M3 to evaluate the two axes
independently. The user payload is the rendered
`SemanticContext` (see `app.services.semantic.context`).

`PROMPT_VERSION` is bumped to `3.0.0` so the build log can
identify the contract this analysis was produced under.
"""

from __future__ import annotations

from app.services.semantic.context import SemanticContext, render_context_for_prompt

PROMPT_VERSION = "3.0.0"


SYSTEM_INTENT = """\
You are the Phase 4 semantic merge analyzer for MergeCut.

You receive the mechanical-edit context from the Phase 3 alignment
layer for one BASE / BRANCH_A / BRANCH_B triple. Your job is to
decide, for that triple, the two-axis semantic verdict defined
in `docs/architecture.md`.

The two axes are INDEPENDENT. Do not collapse them.

============================================================
AXIS 1 — impact_level
============================================================

For each of the THREE videos (Branch A, Branch B, Combined),
classify the impact on BASE's meaning as exactly ONE of:

  preserved — the result communicates everything BASE
              communicated. Soft wording changes, pacing
              changes, and pure restylings are NOT loss of
              meaning.

  degraded  — the result communicates most of BASE's meaning
              but at least one claim has been weakened
              (qualifier narrowed, scope tightened, a
              procedure made less precise, a hedge dropped).

  broken    — the result has dropped or contradicted at least
              one REQUIRED claim. A "required" claim is a
              prerequisite, exception, causal link, scope
              limitation, or safety instruction that no
              longer reaches the viewer in any form.

For each impact, name the specific claim(s) in BASE that the
verdict turns on.

============================================================
AXIS 2 — cross_edit_interaction
============================================================

Classify the cross-edit interaction between Branch A and Branch B
as exactly ONE of:

  none                       — applying both branches together
                                yields the same impact as the
                                worse of the two branches
                                alone. There is NO new
                                interaction-induced loss.

  amplifies_existing_issue   — both branches independently
                                degrade or break the same
                                aspect of BASE. The combined
                                result is materially WORSE
                                than either alone, but the
                                underlying claim was already
                                broken by one branch
                                individually.

  creates_new_conflict       — both branches individually
                                preserve meaning, but
                                combined they break it. This
                                is the canonical MergeCut
                                scenario.

When you return interaction_type ≠ "none", you MUST also return
a `conflict_type` from the Phase 1 taxonomy:
prerequisite_loss, qualifier_loss, exception_loss,
temporal_scope_change, causal_dependency_break,
entity_scope_change, narrative_dependency_break, contradiction,
other.

============================================================
CRITICAL MERGECUT CONDITION
============================================================

The most important case to detect:

  Branch A: preserved
  Branch B: preserved
  Combined: broken
  cross_edit_interaction: creates_new_conflict

When this is the case, you MUST return at least one
CrossEditInteraction with `interaction_type = "creates_new_conflict"`.

When the combined impact is `broken` but the cross_edit_interaction
is `none` or `amplifies_existing_issue`, name the branch that
individually caused the break in the `branch_a_effect` /
`branch_b_effect` fields and explain why the interaction itself
is not `creates_new_conflict`.

============================================================
EVIDENCE
============================================================

Every claim in your response MUST be backed by at least one
`TimestampedEvidence` pointer with:

  - `video` ∈ {base, branch_a, branch_b, merged}
  - `start`, `end` (seconds, end >= start)
  - `description` (what this evidence shows)

The Phase 3 alignment has already given you the mechanical
edit list with timestamps. Use those timestamps as your
evidence start/end. Do not invent timestamps.

============================================================
MECHANICAL FACTS ARE LOAD-BEARING
============================================================

Do not contradict the mechanical-edit list. If the alignment
says Branch A deleted BASE shot 1, do not claim Branch A
preserved shot 1. If you disagree with the alignment, you may
flag the disagreement in `notes` and lower your confidence, but
the mechanical facts themselves are not up for re-interpretation.

============================================================
PROHIBITED
============================================================

- Do not return the legacy Phase 1 binary safe/unsafe fields
  (`branch_a_safe`, `branch_b_safe`, `combined_safe`). The new
  schema replaces them.
- Do not return `conflicts` (the Phase 1 list). The new schema
  uses `interactions` instead.
- Do not invent extra top-level keys.
- Do not add commentary outside the JSON object.

============================================================
RESPONSE SCHEMA (Phase 4 v3.0)
============================================================

Respond with a single JSON object matching this schema:

{{
  "branch_a_impact": {{
    "branch": "branch_a",
    "impact_level": "preserved" | "degraded" | "broken",
    "affected_claims": ["<string>", ...],
    "preserved_equivalents": ["<string>", ...],
    "evidence": [
      {{
        "video": "base" | "branch_a" | "branch_b" | "merged",
        "start": <float seconds>,
        "end": <float seconds>,
        "description": "<string>"
      }}
    ],
    "confidence": <float in [0,1]>,
    "rationale": "<string>"
  }},
  "branch_b_impact": {{
    "branch": "branch_b",
    "impact_level": "preserved" | "degraded" | "broken",
    "affected_claims": ["<string>", ...],
    "preserved_equivalents": ["<string>", ...],
    "evidence": [ ... ],
    "confidence": <float in [0,1]>,
    "rationale": "<string>"
  }},
  "combined_impact": "preserved" | "degraded" | "broken",
  "interactions": [
    {{
      "branch_a_edit_ids": ["<string>", ...],
      "branch_b_edit_ids": ["<string>", ...],
      "combined_impact": "preserved" | "degraded" | "broken",
      "interaction_type": "none" | "amplifies_existing_issue" | "creates_new_conflict",
      "conflict_type": "prerequisite_loss" | "qualifier_loss" | "exception_loss" | "temporal_scope_change" | "causal_dependency_break" | "entity_scope_change" | "narrative_dependency_break" | "contradiction" | "other" | null,
      "base_claim": "<string>",
      "branch_a_effect": "<string>",
      "branch_b_effect": "<string>",
      "combined_effect": "<string>",
      "evidence": [ ... ],
      "confidence": <float in [0,1]>,
      "recommended_resolution": "<string>"
    }}
  ],
  "overall_confidence": <float in [0,1]>,
  "notes": "<optional short string>"
}}
"""


REPAIR_INSTRUCTION = """\
Your previous response did not match the required JSON schema.

Re-emit ONLY a single JSON object that strictly satisfies every required
field and enum. Do not add commentary outside the JSON.

Schema reminder:
- `impact_level` must be one of: preserved, degraded, broken.
- `cross_edit_interaction` is a string for `interaction_type`
  ∈ {none, amplifies_existing_issue, creates_new_conflict}.
- `combined_impact` (top-level AND inside every interaction) must
  be one of: preserved, degraded, broken.
- `conflict_type` must be one of: prerequisite_loss, qualifier_loss,
  exception_loss, temporal_scope_change, causal_dependency_break,
  entity_scope_change, narrative_dependency_break, contradiction,
  other, or null.
- `video` inside `evidence` must be one of: base, branch_a,
  branch_b, merged.
- Every `evidence` entry must have end >= start.
- `confidence` and `overall_confidence` must be floats in [0, 1].
- `interactions` must contain at least one entry.
- Do not invent extra top-level keys.
- Do not return the Phase 1 `conflicts` field. Use `interactions`.
- Do not return the Phase 1 `branch_a_safe` / `branch_b_safe` /
  `combined_safe` fields. Use the `*_impact` fields instead.
"""


def build_user_payload(ctx: SemanticContext) -> str:
    """Render the user payload for one Phase 4 analysis call.

    The payload is the rendered `SemanticContext` (BASE timeline
    + A-edits + B-edits + reconstructed A/B + candidate pairs).
    The system intent instructs M3 how to interpret it.
    """
    body = render_context_for_prompt(ctx)
    return (
        f"PROMPT_VERSION: {PROMPT_VERSION}\n\n"
        f"{body}\n\n"
        f"Apply the two-axis decision rule from the system intent "
        f"to this context and return the JSON object exactly as specified."
    )


__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_INTENT",
    "REPAIR_INSTRUCTION",
    "build_user_payload",
]
