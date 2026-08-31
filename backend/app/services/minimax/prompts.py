"""Prompt contract for the MiniMax M3 semantic analyzer.

The v2.0.0 system intent and user payload are a deliberate rewrite of the
PROJECT_PLAN §15 v1.0.0 contract, prompted by the Phase 1 spike.

Root cause of the v1 failure
----------------------------

On every conflict fixture in the v1 spike, M3 marked BOTH
`branch_a_safe` and `branch_b_safe` as `false`, even though the canonical
case (PROJECT_PLAN §2) is the opposite:

    Branch A: individually reasonable
    Branch B: individually reasonable
    Combined: the prerequisite disappears

The v1 prompt asked M3 to "decide whether each branch is safe alone"
without a positive decision rule and without showing M3 the *full
reconstructed branch content* (only the diff). With no way to check
whether equivalent meaning survived elsewhere in the branch, M3 fell
back to "did this branch weaken any claim? -> not safe", which is the
wrong axis.

The v2.0.0 fix
--------------

1. The system intent now contains the *exact* decision rule requested
   by the product owner, verbatim:

       branch_a_safe: Would a reasonable viewer watching Branch A alone
       still receive the required meaning, constraint, qualifier,
       prerequisite, exception, or dependency somewhere else in the
       branch?

       branch_b_safe: same evaluation for Branch B alone.

       combined_safe: Would a reasonable viewer watching the result
       after applying BOTH edits still receive that meaning?

   And the explicit corollary: "A branch is not unsafe merely because
   it removes one statement if equivalent meaning remains elsewhere."

2. The user payload now shows M3 the *full reconstructed branch
   content* (the BASE transcript after applying each branch's edit),
   so M3 has the information it needs to apply the rule.

3. The per-conflict `branch_a_safe_alone` / `branch_b_safe_alone` /
   `combined_safe` fields are kept but explicitly defined to mirror the
   top-level verdicts (they describe the *same* property at the
   per-conflict level). This is what PROJECT_PLAN §15 means when it
   says "do not flag a conflict merely because both branches edited
   related topics" — the per-conflict booleans are not a third axis.

`PROMPT_VERSION` is bumped to `2.0.0`. The build-log records both
versions side by side.
"""

from __future__ import annotations

from app.services.minimax.branch_view import build_branch_view

PROMPT_VERSION = "2.1.0"


SYSTEM_INTENT = """\
You are the semantic merge analyzer for MergeCut.

You receive an original video context and two independently edited
branches derived from it.

A mechanical merge conflict occurs when edits touch the same timeline
object. That is NOT your primary task.

Your primary task is to detect CROSS-EDIT SEMANTIC CONFLICTS — cases
where two edits that are each individually safe combine into a final
video that loses, contradicts, weakens, or removes important meaning.

============================================================
DECISION RULE FOR BRANCH AND COMBINED SAFETY
============================================================

For EACH of the three safety questions, apply this exact rule:

    branch_a_safe:
        Would a reasonable viewer watching Branch A alone still
        receive the required meaning, constraint, qualifier,
        prerequisite, exception, or dependency SOMEWHERE ELSE
        in the branch?

    branch_b_safe:
        Would a reasonable viewer watching Branch B alone still
        receive the required meaning, constraint, qualifier,
        prerequisite, exception, or dependency SOMEWHERE ELSE
        in the branch?

    combined_safe:
        Would a reasonable viewer watching the result after applying
        BOTH edits still receive that meaning?

KEY COROLLARY (do not violate):

    A branch is NOT unsafe merely because it removes one statement
    if equivalent meaning remains elsewhere in the same branch —
    even when that equivalent meaning is communicated IMPLICITLY
    (e.g. as a consequent that presupposes the prerequisite, a
    parallel prohibition in different wording, or a redundancy that
    restates the claim).

    Examples of "equivalent meaning remains elsewhere" — branch is
    SAFE in each of these cases:

      - The deleted sentence is a prerequisite, but another segment
        presupposes that prerequisite (e.g. "Once the device is
        unplugged, lift the cover" presupposes "unplug the device
        before lifting the cover" — both communicate the prerequisite,
        the latter implicitly).
      - The deleted sentence is one of several parallel statements
        of the same claim (e.g. one of several warnings on a
        dangerous product; the prohibition survives elsewhere).
      - The deleted sentence is one of multiple ways the audience
        can be told the same thing (e.g. both "for production
        systems" and "for prototypes" qualifiers — losing one is
        softened, but the qualifier category survives).

    Examples of "no equivalent meaning remains" — branch is UNSAFE:

      - The deleted sentence was the ONLY statement of a required
        prerequisite, qualifier, exception, causal link, or scope
        limitation, and no other segment communicates that meaning
        in any form.

    The combined result is unsafe if applying BOTH edits together
    removes the required meaning, even when each branch preserves
    that meaning on its own.

============================================================
HOW TO USE THE PROVIDED MATERIAL
============================================================

You will be given, for each fixture:

  - BASE CONTEXT: the full original video transcript / segment plan.
  - BRANCH A FULL CONTENT: what the video looks like after applying
    Branch A's edit on its own (BASE minus A-edit). USE THIS to decide
    branch_a_safe.
  - BRANCH B FULL CONTENT: what the video looks like after applying
    Branch B's edit on its own (BASE minus B-edit). USE THIS to decide
    branch_b_safe.
  - BRANCH A CHANGES (diff): the mechanical description of A's edit.
  - BRANCH B CHANGES (diff): the mechanical description of B's edit.
  - MECHANICAL DIFF: compact timeline-level summary.

You can reconstruct the combined video by starting from BASE,
removing the segments Branch A deletes, and applying Branch B's
replacements. Use that mental reconstruction to decide combined_safe.

============================================================
SCOPE OF "UNSAFE"
============================================================

Flag a conflict only when an important meaning, qualifier,
prerequisite, exception, causal link, temporal distinction, scope
limitation, or instruction disappears, weakens, or contradicts in
the COMBINED video (or in a branch applied alone, in the unsafe-alone
case). Ground every conclusion in supplied audiovisual evidence and
timestamps.

Do not flag a conflict merely because both branches edited related
topics. Do not flag a conflict merely because a branch removed a
sentence that was restated elsewhere in the same branch.

============================================================
OUTPUT CONTRACT
============================================================

Return only data matching the requested schema. Do not output prose
outside of the JSON object.
"""


def build_user_payload(
    *,
    base_context: str,
    branch_a_change: str,
    branch_b_change: str,
    mechanical_diff: str,
    branch_a_full: str,
    branch_b_full: str,
) -> str:
    """Render the user-message payload for the v2 semantic analyzer.

    `branch_a_full` and `branch_b_full` are the *reconstructed* branch
    contents (BASE after applying each branch's edit in isolation).
    Showing them to M3 is what unlocks the v2 decision rule.
    """
    return f"""\
BASE CONTEXT (the original, before any edit)
{base_context}

BRANCH A FULL CONTENT (BASE after applying ONLY Branch A's edit)
{branch_a_full}

BRANCH B FULL CONTENT (BASE after applying ONLY Branch B's edit)
{branch_b_full}

BRANCH A CHANGES (mechanical diff)
{branch_a_change}

BRANCH B CHANGES (mechanical diff)
{branch_b_change}

MECHANICAL DIFF
{mechanical_diff}

TASK

Apply the decision rule from the system intent to answer these three
questions IN THIS ORDER, and ground each answer in the supplied
content:

  1. branch_a_safe:
     Walk through BRANCH A FULL CONTENT. Does the required meaning
     survive SOMEWHERE ELSE in the branch, even though Branch A's edit
     removed one statement? If yes, set branch_a_safe.safe = true.

  2. branch_b_safe:
     Same evaluation for BRANCH B FULL CONTENT.

  3. combined_safe:
     Mentally combine both edits (delete A's segments, apply B's
     replacements). Does the required meaning survive anywhere in the
     combined video? If yes, set combined_safe = true.

Then, if any of (1) (2) (3) indicates a problem, enumerate each
cross-edit semantic conflict with timestamped evidence, severity,
confidence, and a recommended resolution.

Respond with a single JSON object matching this schema:

{{
  "branch_a_safe": {{
    "safe": <bool>,
    "rationale": "<string: cite which segment(s) of BRANCH A FULL CONTENT preserve the required meaning, or explain why none do>",
    "affected_claims": ["<string>", ...],
    "confidence": <float in [0,1]>
  }},
  "branch_b_safe": {{
    "safe": <bool>,
    "rationale": "<string: cite which segment(s) of BRANCH B FULL CONTENT preserve the required meaning, or explain why none do>",
    "affected_claims": ["<string>", ...],
    "confidence": <float in [0,1]>
  }},
  "combined_safe": <bool>,
  "conflicts": [
    {{
      "id": "<string>",
      "type": "prerequisite_loss" | "qualifier_loss" | "exception_loss"
            | "temporal_scope_change" | "causal_dependency_break"
            | "entity_scope_change" | "narrative_dependency_break"
            | "contradiction" | "other",
      "severity": "low" | "medium" | "high",
      "base_claim": "<string>",
      "branch_a_effect": "<string>",
      "branch_b_effect": "<string>",
      "combined_effect": "<string>",
      "branch_a_safe_alone": <bool>,   // mirror of branch_a_safe.safe
      "branch_b_safe_alone": <bool>,   // mirror of branch_b_safe.safe
      "combined_safe": <bool>,         // mirror of top-level combined_safe
      "evidence": [
        {{
          "video": "base" | "branch_a" | "branch_b" | "merged",
          "start": <float seconds>,
          "end": <float seconds>,
          "description": "<string>"
        }}
      ],
      "confidence": <float in [0,1]>,
      "recommended_resolution": "<string>"
    }}
  ],
  "overall_confidence": <float in [0,1]>,
  "notes": "<optional short string>"
}}

Notes:
- The per-conflict `branch_a_safe_alone` / `branch_b_safe_alone` /
  `combined_safe` MUST match the top-level verdicts. They describe the
  same property at the per-conflict level, not a different axis.
- If combined_safe is true and conflicts is empty, branch_a_safe and
  branch_b_safe must both be true. If either branch is unsafe alone,
  at least one conflict must reference that fact.
"""


def build_user_payload_for_fixture(fixture) -> str:
    """Convenience wrapper: render the v2 user payload for a Fixture.

    Computes the reconstructed branch views internally and forwards
    everything to `build_user_payload`. Local-imports `Fixture` to
    avoid a top-level import cycle (`prompts` is imported by the
    package's `__init__`, which is also on the import path of the
    fixtures module via `branch_view`).
    """
    from tests.fixtures.spike_fixtures import Fixture  # noqa: F401

    return build_user_payload(
        base_context=fixture.base_context,
        branch_a_change=fixture.branch_a_change,
        branch_b_change=fixture.branch_b_change,
        mechanical_diff=fixture.mechanical_diff,
        branch_a_full=build_branch_view(fixture.base_context, fixture.branch_a_change),
        branch_b_full=build_branch_view(fixture.base_context, fixture.branch_b_change),
    )


# ---------------------------------------------------------------------------
# Repair prompt used when the first response fails schema validation
# (PROJECT_PLAN §14.4). Same as v1; the contract surface did not change.
# ---------------------------------------------------------------------------

REPAIR_INSTRUCTION = """\
Your previous response did not match the required JSON schema.

Re-emit ONLY a single JSON object that strictly satisfies every required
field and enum. Do not add commentary outside the JSON.

Schema reminder:
- `type` must be one of: prerequisite_loss, qualifier_loss, exception_loss,
  temporal_scope_change, causal_dependency_break, entity_scope_change,
  narrative_dependency_break, contradiction, other.
- `severity` must be one of: low, medium, high.
- Every conflict must include at least one evidence entry with end >= start.
- `confidence` and `overall_confidence` must be floats in [0, 1].
- Do not invent extra top-level keys.
"""
