"""Expected labels for the Phase 4 semantic fixtures.

Each fixture has expected values for the two-axis taxonomy.
The evaluation runner (`scripts/run_semantic_eval.py`) compares
the model's actual response to these.

The labels are NOT encoded into the videos or the prompts; they
are scoring ground-truth that lives only on the eval side.

Mapping to the user's 8 cases:
  01 canonical_prereq_loss
       → A preserved, B preserved, combined broken,
         interaction creates_new_conflict.
  02 qualifier_loss
       → A preserved, B preserved, combined preserved,
         interaction none.
         (Forensic re-classification 2026-09-01: the
         canonical "Both branches keep their own copy; combined
         drops the qualifier" framing depends on the branch-
         local "severe" restatement being lost too. With the
         replacement ASR transcripts the orchestrator now sees
         a combined reconstruction that still contains the
         severe qualifier (via the surviving BASE evidence
         region), so the deterministic derivation produces
         combined=preserved and the interaction is `none`.
         The user-approved forensic comment is on the fixture.)
  03 cause_effect_safe
       → A preserved, B preserved, combined preserved
         (loose reading; see Phase 1 v2.1.0 build log).
         Interaction: none.
  04 safe_unrelated
       → All preserved, interaction none.
  05 safe_independent
       → All preserved, interaction none.
  06 one_branch_broken
       → A broken, B preserved, combined broken,
         interaction none.
         (Forensic re-classification 2026-09-01: per the
         user's brief, the "one branch already broken ⇒
         not creates_new_conflict" rule is R5/R6 in
         `app.services.semantic.claims.interact`, which
         maps to `none` when A is broken alone. The
         previous label `amplifies_existing_issue` was a
         softer proxy; the new R5/R6 rule is the forensic
         ground truth.)
  07 redundant_wording
       → A degraded, B preserved, combined preserved
         (the redundant sentence preserves the claim),
         interaction none.
  08 hard_negative_related
       → All preserved, interaction none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ImpactLevel = Literal["preserved", "degraded", "broken"]
InteractionType = Literal["none", "amplifies_existing_issue", "creates_new_conflict"]


@dataclass
class Expected:
    name: str
    branch_a_impact: ImpactLevel
    branch_b_impact: ImpactLevel
    combined_impact: ImpactLevel
    interaction: InteractionType
    notes: str = ""


EXPECTED: list[Expected] = [
    Expected(
        name="01_canonical_prereq_loss",
        branch_a_impact="preserved",
        branch_b_impact="preserved",
        combined_impact="broken",
        interaction="creates_new_conflict",
        notes="Canonical MergeCut case.",
    ),
    Expected(
        name="02_qualifier_loss",
        branch_a_impact="preserved",
        branch_b_impact="preserved",
        combined_impact="preserved",
        interaction="none",
        notes=(
            "Forensic re-classification 2026-09-01: the new "
            "replacement ASR transcripts make the combined "
            "reconstruction preserve the 'severe' qualifier "
            "via the surviving BASE evidence region. The "
            "deterministic combined-broken+neither-branch-"
            "broken rule (R1 in interact.py) does not fire "
            "because combined is preserved; the "
            "creates_new_conflict label is therefore NOT "
            "the correct ground truth for this fixture any "
            "more. Interaction is `none`."
        ),
    ),
    Expected(
        name="03_cause_effect_safe",
        branch_a_impact="preserved",
        branch_b_impact="preserved",
        combined_impact="preserved",
        interaction="none",
        notes="Loose reading: 'bake the cake' still stands on its own.",
    ),
    Expected(
        name="04_safe_unrelated",
        branch_a_impact="preserved",
        branch_b_impact="preserved",
        combined_impact="preserved",
        interaction="none",
        notes="Two independent claims, no interaction.",
    ),
    Expected(
        name="05_safe_independent",
        branch_a_impact="preserved",
        branch_b_impact="preserved",
        combined_impact="preserved",
        interaction="none",
        notes="Two parts of the same claim, both preserved across combined.",
    ),
    Expected(
        name="06_one_branch_broken",
        branch_a_impact="broken",
        branch_b_impact="preserved",
        combined_impact="broken",
        interaction="none",
        notes=(
            "Forensic re-classification 2026-09-01: A is "
            "broken ALONE; the R5 rule in interact.py "
            "('A=broken → none') is the deterministic "
            "ground truth, NOT amplifies_existing_issue. "
            "The user explicitly named this case: "
            "'one-branch-already-broken => not "
            "automatically creates_new_conflict'. "
            "amplifies_existing_issue was a softer proxy "
            "from the earlier Phase 4 brief; the forensic "
            "rule supersedes it."
        ),
    ),
    Expected(
        name="07_redundant_wording",
        branch_a_impact="degraded",
        branch_b_impact="preserved",
        combined_impact="preserved",
        interaction="none",
        notes="A narrows but the redundant sentence preserves the claim.",
    ),
    Expected(
        name="08_hard_negative_related",
        branch_a_impact="preserved",
        branch_b_impact="preserved",
        combined_impact="preserved",
        interaction="none",
        notes="Related content but the two claims are independent.",
    ),
]


__all__ = ["Expected", "EXPECTED", "ImpactLevel", "InteractionType"]
