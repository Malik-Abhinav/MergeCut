"""M3 prompts for the Phase 4 claim-centric pipeline.

Two prompts:

  EXTRACTION_SYSTEM_INTENT + EXTRACTION_USER_TEMPLATE
      M3 is asked to extract the important semantic claims
      from BASE.

  EVALUATION_SYSTEM_INTENT + build_evaluation_user_payload
      M3 is asked to evaluate ONE claim in ONE branch
      (preserved / degraded / broken) and return the
      surviving evidence regions. The orchestrator calls
      this prompt THREE TIMES per claim (once for A, once
      for B, once for combined).

Both prompts are designed so M3 never decides the
cross-edit interaction (STEP 4 is deterministic code) and
the interaction is never part of the M3 response schema.
"""

from __future__ import annotations

from app.models.claims import (
    ClaimEvaluationRequest,
)

EXTRACTION_PROMPT_VERSION = "4.1.0"
EVALUATION_PROMPT_VERSION = "4.1.0"


# ---------------------------------------------------------------------------
# STEP 1 — Claim extraction.
# ---------------------------------------------------------------------------


EXTRACTION_SYSTEM_INTENT = """\
You are the BASE-claim extractor for MergeCut.

You receive a single video's transcript + the timeline of
shots (sequence_index, start, end). Your job is to extract
the IMPORTANT semantic claims the video communicates.

A "claim" is one load-bearing unit of meaning the viewer
walks away with. Not every sentence is a claim — decorative
phrases, scene descriptions, and greetings are not.

Claim taxonomy:

  - prerequisite         : a precondition ("before X, do Y")
  - qualifier            : a narrowing/limiting condition
  - exception            : a "unless..." condition
  - temporal_scope       : a time/duration constraint
  - causal_dependency    : a cause/effect relationship
  - entity_scope         : a who/what constraint
  - instruction          : a directive ("do X")
  - prohibition          : a "do not..." directive
  - narrative_dependency : a context the meaning depends on
  - other                : everything else

For each claim you must return:

  - claim_id            : a stable id (C1, C2, ...).
  - meaning             : the natural-language meaning of the
                          claim. Write it so that "the meaning
                          is preserved in branch X" is testable
                          against this string.
  - claim_type          : one of the taxonomy values.
  - importance          : critical / high / medium / low.
                          critical = load-bearing (prerequisite,
                          safety threshold, exception).
                          high = primary directive.
                          medium = supporting detail.
                          low = decorative.
  - evidence_regions    : the (start, end) spans in BASE
                          where the claim is stated.
  - equivalents         : OTHER spans in BASE that express the
                          SAME meaning (paraphrases, parallel
                          prohibitions, restatements). Empty
                          when there is no redundant statement.

REQUIRED:
- At least one claim. Aim for 2-5 important claims per
  short video; fewer is fine for very simple content.
- Every claim must have at least one evidence_region.
- Set `importance="critical"` for the load-bearing claim in
  each scene (e.g. the prerequisite). It is acceptable to
  have multiple critical claims.

DO NOT:
- Do not include decorative or trivial sentences as claims.
- Do not include meta-claims about the video itself.
- Do not invent extra top-level keys.
- Do not return evidence_regions outside the BASE timeline.

Respond with a single JSON object matching this schema:

{
  "claims": [
    {
      "claim_id": "C1",
      "meaning": "...",
      "claim_type": "prerequisite" | "qualifier" | "exception" | "temporal_scope" | "causal_dependency" | "entity_scope" | "instruction" | "prohibition" | "narrative_dependency" | "other",
      "importance": "critical" | "high" | "medium" | "low",
      "evidence_regions": [
        {"start": <float seconds>, "end": <float seconds>, "description": "..."}
      ],
      "equivalents": [
        {"start": <float seconds>, "end": <float seconds>, "description": "..."}
      ]
    }
  ]
}
"""


EXTRACTION_REPAIR_INSTRUCTION = """\
Your previous response did not match the required JSON schema.

Re-emit ONLY a single JSON object that strictly satisfies every required
field. Do not add commentary outside the JSON.

Schema reminder:
- Top-level: `{"claims": [...]}`.
- `claim_type` must be one of: prerequisite, qualifier, exception,
  temporal_scope, causal_dependency, entity_scope, instruction,
  prohibition, narrative_dependency, other.
- `importance` must be one of: critical, high, medium, low.
- Every claim must have ≥1 `evidence_regions` entry with end >= start.
- Do not invent extra top-level keys.
- Do not return any field other than `claims`.
"""


def build_extraction_user_payload(
    *,
    video_id: str,
    shot_lines: list[tuple[int, str, float, float]],
) -> str:
    """Render the user payload for one claim-extraction call.

    `shot_lines` is a list of `(sequence_index, transcript,
    start, end)` tuples. The orchestrator can also pass a
    smaller subset when the BASE is very long.
    """
    out: list[str] = []
    out.append(f"EXTRACTION_PROMPT_VERSION: {EXTRACTION_PROMPT_VERSION}")
    out.append(f"BASE video_id: {video_id}")
    out.append("")
    out.append("BASE SHOT TIMELINE (sequence_index, [start, end], transcript):")
    out.append("=" * 60)
    for idx, transcript, start, end in shot_lines:
        out.append(f"  shot_{idx:04d} [{start:.3f}-{end:.3f}]  {transcript or '(no transcript)'}")
    out.append("")
    out.append(
        "Apply the extraction system intent to this BASE. Return the "
        "JSON object exactly as specified."
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# STEP 3 — Per-claim, per-branch preservation evaluation.
# ---------------------------------------------------------------------------


EVALUATION_SYSTEM_INTENT = """\
You are the per-claim preservation evaluator for MergeCut.

For ONE specific claim in ONE specific branch of a video,
you return:

  - claim_id            : the claim id (echoed from the request).
  - status              : preserved / degraded / broken.
  - surviving_evidence  : the (start, end) spans in the BRANCH
                          where the claim's meaning is still
                          carried. Empty when status=broken.
  - rationale           : short justification.
  - confidence          : your confidence in [0, 1].

Definitions:

  - preserved : the claim's meaning is communicated in the
                 branch — either at the original evidence
                 region OR at an equivalent span OR in a
                 new restatement.
  - degraded  : the claim's meaning is communicated in the
                 branch but weakened (qualifier narrowed,
                 scope tightened, hedge dropped, exception
                 removed).
  - broken    : the claim has been dropped or contradicted
                 in the branch. The original evidence
                 region AND every equivalent are gone, AND
                 no new restatement of the meaning exists.

You are given:

  - the claim's meaning and type,
  - the claim's BASE evidence regions + equivalents,
  - the branch's reconstructed content (BASE with the
    branch's edit applied, with DELETED / REPLACED /
    TRIMMED markers around the relevant shots).

Decide whether the meaning survives in the branch.

DO NOT:
- Do not decide whether a cross-edit interaction exists. The
  downstream Python code derives that from your per-claim
  verdicts.
- Do not write a `combined` branch verdict for A. (Combined
  is a separate call with `branch_name = "combined"`.)
- Do not invent extra top-level keys.

Respond with a single JSON object:

{
  "claim_id": "C1",
  "status": "preserved" | "degraded" | "broken",
  "surviving_evidence": [
    {"start": <float seconds>, "end": <float seconds>, "description": "..."}
  ],
  "rationale": "...",
  "confidence": <float in [0,1]>
}
"""


EVALUATION_REPAIR_INSTRUCTION = """\
Your previous response did not match the required JSON schema.

Re-emit ONLY a single JSON object that strictly satisfies every
required field. Do not add commentary outside the JSON.

Schema reminder:
- `claim_id` must echo the claim id from the request.
- `status` must be one of: preserved, degraded, broken.
- `surviving_evidence` may be empty (for status=broken).
- `confidence` must be a float in [0, 1].
- Do not invent extra top-level keys.
"""


def build_evaluation_user_payload(req: ClaimEvaluationRequest) -> str:
    """Render the user payload for one (claim, branch) evaluation call.

    The orchestrator calls this once per claim per branch
    (i.e. 3N calls for N claims).
    """
    out: list[str] = []
    out.append(f"EVALUATION_PROMPT_VERSION: {EVALUATION_PROMPT_VERSION}")
    out.append(f"branch: {req.branch_name}")
    out.append(f"claim_id: {req.claim_id}")
    out.append(f"claim_type: {req.claim_type.value}")
    out.append(f"importance: {req.importance.value}")
    out.append(f"meaning: {req.meaning}")
    out.append("")
    out.append("BASE EVIDENCE REGIONS:")
    for er in req.base_evidence_regions:
        out.append(f"  [{er.start:.3f}-{er.end:.3f}]  {er.description}")
    if req.base_equivalents:
        out.append("")
        out.append("BASE EQUIVALENTS (other BASE spans carrying the same meaning):")
        for er in req.base_equivalents:
            out.append(f"  [{er.start:.3f}-{er.end:.3f}]  {er.description}")
    out.append("")
    out.append(
        f"{req.branch_name.upper()} RECONSTRUCTED CONTENT "
        "(BASE after applying ONLY this branch's edit):"
    )
    out.append("=" * 60)
    for line in req.branch_reconstructed_lines:
        out.append(f"  {line}")
    out.append("")
    out.append(
        "Apply the evaluation system intent and return the JSON object exactly as specified."
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# STEP 5 — M3 explanation (prose only; does not control classification).
# ---------------------------------------------------------------------------


EXPLANATION_SYSTEM_INTENT = """\
You are the human-readable explainer for a MergeCut cross-edit
interaction. The interaction has ALREADY been classified by
deterministic code. Your job is to write a short paragraph
(1-3 sentences) explaining WHY the interaction was classified
that way, citing the per-claim evidence the deterministic
code used.

You may also suggest a recommended_resolution (1 sentence).

DO NOT change the interaction classification. The interaction
field is fixed; you only write the prose.

Respond with:

{
  "explanation": "...",
  "recommended_resolution": "..."
}
"""


def build_explanation_user_payload(
    *,
    claim_id: str,
    claim_meaning: str,
    interaction: str,
    derivation_reason: str,
    branch_a_status: str,
    branch_b_status: str,
    combined_status: str,
) -> str:
    return (
        f"claim_id: {claim_id}\n"
        f"claim_meaning: {claim_meaning}\n"
        f"interaction: {interaction}\n"
        f"derivation_reason: {derivation_reason}\n"
        f"branch_a_status: {branch_a_status}\n"
        f"branch_b_status: {branch_b_status}\n"
        f"combined_status: {combined_status}\n"
        f"\n"
        f"Write the 1-3 sentence explanation and the recommended "
        f"resolution."
    )


__all__ = [
    "EXTRACTION_PROMPT_VERSION",
    "EVALUATION_PROMPT_VERSION",
    "EXTRACTION_SYSTEM_INTENT",
    "EXTRACTION_REPAIR_INSTRUCTION",
    "build_extraction_user_payload",
    "EVALUATION_SYSTEM_INTENT",
    "EVALUATION_REPAIR_INSTRUCTION",
    "build_evaluation_user_payload",
    "EXPLANATION_SYSTEM_INTENT",
    "build_explanation_user_payload",
]
