"""Controlled MergeCut semantic-merge fixtures (Phase 1 spike, v2).

Per PROJECT_PLAN §29 Phase 1:

    Create 5 fixtures manually:
    - 3 true semantic conflicts
    - 2 safe controls
    For each provide M3:
    - BASE context
    - A edit
    - B edit
    Do not reveal labels.

These fixtures are deliberately text-only (transcripts + edit descriptions).
The full audio/video pipeline arrives in Phase 2; the spike's job is to
prove that *given* well-formed context, M3 through GMI Cloud can
classify cross-edit semantic interactions at the level of detail we need.

In v2 we extend the fixture shape so the runner can evaluate the
*individual* branch safety verdicts as well as the combined verdict.
PROJECT_PLAN §15 requires M3 to distinguish:

    1. branch A safety in isolation
    2. branch B safety in isolation
    3. combined semantic safety

True labels (for evaluation only — never sent to the model).

Original 5 (preserved from v1, with v2.1.0 restatements added so the
canonical axis is unambiguous):

    01_prereq_loss        conflict   A=true   B=true   combined=false   prerequisite_loss
                            + a recap segment at [01:00\u201301:05] restating the prerequisite
    02_qualifier_loss     conflict   A=true   B=true   combined=false   qualifier_loss
                            (BASE keeps both "not for production" and "for prototypes")
    03_cause_effect       conflict   A=true   B=true   combined=false   causal_dependency_break
                            + a recap segment at [00:36\u201300:50] restating the cooking duration
    04_safe_unrelated     safe       A=true   B=true   combined=true
    05_safe_independent   safe       A=true   B=true   combined=true

New 3 (added for v2):

    06_classic_safeAB     conflict   A=true   B=true   combined=false   prerequisite_loss
       (Alternate framing of the canonical case; ensures the prompt change
        generalises rather than memorising the exact sentences of 01.)

    07_a_unsafe_b_safe    conflict   A=false  B=true   combined=false   prerequisite_loss
       (A genuinely destroys the only safety instruction; B is unrelated.
        This is the case v1 collapsed incorrectly with the canonical case.)

    08_redundant_safe     safe       A=true   B=true   combined=true
       (Both edits weaken wording in different places, but a third
        redundant statement in BASE still survives both edits and keeps
        the meaning intact in the combined video.)

The runner also now receives the *full reconstructed branch content*
(see `build_branch_view`) so M3 can verify whether equivalent meaning
survives elsewhere in the branch, instead of being asked to score a
branch only against the sentence it deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.minimax.branch_view import (
    build_branch_view,
    parse_base_segments,
)

SpikeLabel = Literal["conflict", "safe"]


@dataclass(frozen=True)
class Fixture:
    """One text-based semantic-merge fixture.

    Attributes
    ----------
    id:
        Stable identifier used by the runner for record-keeping.
    base_context:
        The full original video transcript / segment plan. Sent to M3.
    branch_a_change:
        Textual description of the A-side edit. Sent to M3.
    branch_b_change:
        Textual description of the B-side edit. Sent to M3.
    mechanical_diff:
        Compact mechanical-diff summary. Sent to M3.
    expected_label:
        The expected combined verdict. Evaluation-only.
    expected_conflict_type:
        The expected conflict category for the *primary* conflict.
        Only set when `expected_label == "conflict"`. Evaluation-only.
    expected_branch_a_safe:
        Whether branch A is genuinely safe to apply to BASE on its own.
        Evaluation-only.
    expected_branch_b_safe:
        Same, for branch B. Evaluation-only.
    """

    id: str
    base_context: str
    branch_a_change: str
    branch_b_change: str
    mechanical_diff: str
    # Internal — NEVER included in the payload sent to M3.
    expected_label: SpikeLabel
    expected_conflict_type: str | None = None
    expected_branch_a_safe: bool = True  # default for the historical safe controls
    expected_branch_b_safe: bool = True


# ---------------------------------------------------------------------------
# Helpers used both by the fixture definitions and by the runner.
# They live in `app.services.minimax.branch_view` and are re-exported
# from this module for test convenience.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fixture set: v1 (preserved verbatim) + v2 (3 new).
# ---------------------------------------------------------------------------


FIXTURES: list[Fixture] = [
    # =======================================================================
    # Original v1 fixtures (kept bit-identical to the v1.0.0 spike).
    # =======================================================================
    # ----- 1. Prerequisite loss (classic PROJECT_PLAN §2 example) ---------
    # v2.1.0: The prerequisite ("unplug before opening") is stated in
    # TWO places in BASE: the upfront statement at [00:18\u201300:24]
    # (deleted by Branch A) and the callback at [00:41\u201300:46] (replaced
    # by Branch B). Each branch's edit touches one of the two
    # statements and leaves the other intact, so each branch still
    # communicates the prerequisite; but the COMBINED video has both
    # statements weakened/removed.
    Fixture(
        id="01_prereq_loss",
        base_context=(
            "BASE video (instructional, ~50s):\n"
            "[00:00\u201300:18] 'Welcome. In this tutorial we will show how to "
            "safely replace the battery in your device.'\n"
            "[00:18\u201300:24] 'Before opening the device, unplug it from the wall.'\n"
            "[00:24\u201300:41] 'Place the device face-down on a soft surface and "
            "remove the four rear screws.'\n"
            "[00:41\u201300:46] 'Once the device is unplugged, lift the cover.'\n"
            "[00:46\u201301:00] 'You can now access the battery compartment.'"
        ),
        branch_a_change=(
            "BRANCH A edit:\n"
            "  [00:18\u201300:24] 'Before opening the device, unplug it from the wall.'\n"
            "  --> DELETED entirely from the cut.\n"
            "  All other segments preserved verbatim."
        ),
        branch_b_change=(
            "BRANCH B edit:\n"
            "  [00:41\u201300:46] 'Once the device is unplugged, lift the cover.'\n"
            "  --> REPLACED with: 'Lift the cover.'\n"
            "  All other segments preserved verbatim."
        ),
        mechanical_diff=(
            "Mechanical diff:\n"
            "  A: delete segment base_07 (00:18\u201300:24)\n"
            "  B: replace segment base_13 (00:41\u201300:46) with shorter wording\n"
            "No overlapping timeline edits. Both diffs look clean in isolation."
        ),
        expected_label="conflict",
        expected_conflict_type="prerequisite_loss",
        expected_branch_a_safe=True,
        expected_branch_b_safe=True,
    ),
    # ----- 2. Qualifier loss ------------------------------------------------
    # v2.1.0: The production-scope qualifier appears in EXACTLY TWO
    # places in BASE: the upfront disclaimer at [00:12\u201300:24] (deleted
    # by Branch A) and the closing recap at [00:45\u201300:55] (replaced
    # by Branch B). No positive-scope segment survives that would
    # imply the production-scope restriction by contrapositive. Each
    # branch leaves the other restatement intact; combined loses
    # both.
    Fixture(
        id="02_qualifier_loss",
        base_context=(
            "BASE video (~55s, product review):\n"
            "[00:00\u201300:12] 'Today we are looking at the AcmeStream encoder.'\n"
            "[00:12\u201300:24] 'I would not recommend this for production systems.'\n"
            "[00:24\u201300:36] 'The latency is too unpredictable under sustained load.'\n"
            "[00:36\u201300:45] 'It is also fairly easy to set up.'\n"
            "[00:45\u201300:55] 'Bottom line: this encoder is designed for prototyping, not for production.'"
        ),
        branch_a_change=(
            "BRANCH A edit:\n"
            "  [00:12\u201300:24] 'I would not recommend this for production systems.'\n"
            "  --> DELETED.\n"
            "  All other segments preserved verbatim."
        ),
        branch_b_change=(
            "BRANCH B edit:\n"
            "  [00:45\u201300:55] 'Bottom line: this encoder is designed for prototyping, not for production.'\n"
            "  --> REPLACED with: 'Bottom line: this encoder works well and is easy to set up.'\n"
            "  (the production-scope qualifier is removed from the recap)\n"
            "  All other segments preserved verbatim."
        ),
        mechanical_diff=(
            "Mechanical diff:\n"
            "  A: delete segment base_02 (00:12\u201300:24)\n"
            "  B: replace segment base_05 (00:45\u201300:55), drops production-scope qualifier from recap\n"
            "Disjoint timeline regions. Both edits touch ONE of the TWO restatements\n"
            "of the production-scope qualifier and leave the OTHER intact."
        ),
        expected_label="conflict",
        expected_conflict_type="qualifier_loss",
        expected_branch_a_safe=True,
        expected_branch_b_safe=True,
    ),
    # ----- 3. Cause/effect break -------------------------------------------
    # v2.1.0: The cooking-duration claim appears in TWO places in BASE:
    # the explicit step at [00:22\u201300:36] (replaced by Branch B) and a
    # downstream recap at [00:36\u201300:50] that repeats the duration
    # (deleted by Branch A). Each branch leaves the other duration
    # statement intact; combined loses both.
    Fixture(
        id="03_cause_effect",
        base_context=(
            "BASE video (~55s, cooking tutorial):\n"
            "[00:00\u201300:10] 'To make a roux, start with equal parts butter and flour.'\n"
            "[00:10\u201300:22] 'If you skip the butter, the sauce will not thicken.'\n"
            "[00:22\u201300:36] 'Cook the mixture for two minutes, stirring constantly, "
            "and the sauce will thicken into a smooth roux.'\n"
            "[00:36\u201300:50] 'After two minutes of stirring, you can now add milk for a b\u00e9chamel.'"
        ),
        branch_a_change=(
            "BRANCH A edit:\n"
            "  [00:36\u201300:50] 'After two minutes of stirring, you can now add milk for a b\u00e9chamel.'\n"
            "  --> DELETED.\n"
            "  All other segments preserved verbatim."
        ),
        branch_b_change=(
            "BRANCH B edit:\n"
            "  [00:22\u201300:36] 'Cook the mixture for two minutes, stirring constantly, "
            "and the sauce will thicken into a smooth roux.'\n"
            "  --> REPLACED with: 'The sauce will thicken into a smooth roux.'\n"
            "  (the 'Cook ... stirring constantly' cause clause is removed)\n"
            "  All other segments preserved verbatim."
        ),
        mechanical_diff=(
            "Mechanical diff:\n"
            "  A: delete segment base_04 (00:36\u201300:50)\n"
            "  B: replace segment base_03 (00:22\u201300:36), shorter wording\n"
            "Disjoint timeline regions. Both edits touch ONE statement of the\n"
            "cooking duration and leave the OTHER intact."
        ),
        expected_label="conflict",
        expected_conflict_type="causal_dependency_break",
        expected_branch_a_safe=True,
        expected_branch_b_safe=True,
    ),
    # ----- 4. Safe control: unrelated, semantically independent -------------
    Fixture(
        id="04_safe_unrelated",
        base_context=(
            "BASE video (~40s, hiking vlog):\n"
            "[00:00\u201300:10] 'We started the trail at 7am from the lower parking lot.'\n"
            "[00:10\u201300:20] 'The first two kilometers are easy and shaded.'\n"
            "[00:20\u201300:30] 'After the creek crossing the climb gets steeper.'\n"
            "[00:30\u201300:40] 'We reached the ridge just before noon.'"
        ),
        branch_a_change=(
            "BRANCH A edit:\n"
            "  [00:10\u201300:20] 'The first two kilometers are easy and shaded.'\n"
            "  --> REPLACED with: 'The first two kilometers are easy and well-shaded.'"
        ),
        branch_b_change=(
            "BRANCH B edit:\n"
            "  [00:30\u201300:40] 'We reached the ridge just before noon.'\n"
            "  --> REPLACED with: 'We reached the ridge around noon.'"
        ),
        mechanical_diff=(
            "Mechanical diff:\n"
            "  A: replace base_02 wording (minor wording polish)\n"
            "  B: replace base_04 wording (minor time hedge)\n"
            "Disjoint, both edits are stylistic, no claims or instructions are touched."
        ),
        expected_label="safe",
        expected_branch_a_safe=True,
        expected_branch_b_safe=True,
    ),
    # ----- 5. Safe control: same topic, compatible edits --------------------
    Fixture(
        id="05_safe_independent",
        base_context=(
            "BASE video (~45s, software demo):\n"
            "[00:00\u201300:12] 'In this demo we configure the mergecut service.'\n"
            "[00:12\u201300:24] 'Step one: set the GMI_API_KEY environment variable.'\n"
            "[00:24\u201300:36] 'Step two: run the analyzer against your video trio.'\n"
            "[00:36\u201300:45] 'The tool returns a structured conflict report.'"
        ),
        branch_a_change=(
            "BRANCH A edit:\n"
            "  [00:24\u201300:36] 'Step two: run the analyzer against your video trio.'\n"
            "  --> DELETED. (creator considered the step self-evident from the next line)"
        ),
        branch_b_change=(
            "BRANCH B edit:\n"
            "  [00:36\u201300:45] 'The tool returns a structured conflict report.'\n"
            "  --> REPLACED with: 'The tool returns a structured JSON conflict report.'"
        ),
        mechanical_diff=(
            "Mechanical diff:\n"
            "  A: delete segment base_03 (00:24\u201300:36)\n"
            "  B: replace segment base_04 (00:36\u201300:45), adds the word 'JSON'\n"
            "Both edits shorten the demo without removing any claim or instruction "
            "that the other edit depends on."
        ),
        expected_label="safe",
        expected_branch_a_safe=True,
        expected_branch_b_safe=True,
    ),
    # =======================================================================
    # v2 NEW fixtures.
    # =======================================================================
    # ----- 6. Classic cross-edit conflict (alt framing) --------------------
    # Independent restatement of the 01 case to verify the new prompt
    # generalises and is not just memorising 01's exact sentences.
    Fixture(
        id="06_classic_safeAB",
        base_context=(
            "BASE video (~70s, lab safety training):\n"
            "[00:00\u201300:15] 'This is the chemical-handling orientation for new staff.'\n"
            "[00:15\u201300:25] 'Always wear gloves and safety glasses before handling acids.'\n"
            "[00:25\u201300:40] 'Acids must be neutralised before disposal in the sink.'\n"
            "[00:40\u201300:55] 'Open the fume hood, then pour the acid slowly.'\n"
            "[00:55\u201301:05] 'Confirm the hood is on before you begin pouring.'\n"
            "[01:05\u201301:10] 'Once neutralised, flush with water for two minutes.'"
        ),
        branch_a_change=(
            "BRANCH A edit:\n"
            "  [00:15\u201300:25] 'Always wear gloves and safety glasses before handling acids.'\n"
            "  --> DELETED."
        ),
        branch_b_change=(
            "BRANCH B edit:\n"
            "  [00:40\u201300:55] 'Open the fume hood, then pour the acid slowly.'\n"
            "  --> REPLACED with: 'Pour the acid slowly.'\n"
            "  (the explicit fume-hood step before pouring is removed)"
        ),
        mechanical_diff=(
            "Mechanical diff:\n"
            "  A: delete segment base_02 (00:15\u201300:25)\n"
            "  B: replace segment base_04 (00:40\u201300:55), shorter wording\n"
            "Disjoint timeline regions. Both edits small.\n"
            "NOTE: BASE also contains [00:55\u201301:05] which independently restates\n"
            "the hood-on prerequisite, so each branch retains the meaning on its own."
        ),
        expected_label="conflict",
        expected_conflict_type="prerequisite_loss",
        expected_branch_a_safe=True,
        expected_branch_b_safe=True,
    ),
    # ----- 7. A genuinely unsafe alone, B safe alone, combined unsafe ------
    # A deletes the *only* safety instruction in the video. B is unrelated.
    Fixture(
        id="07_a_unsafe_b_safe",
        base_context=(
            "BASE video (~55s, repair guide):\n"
            "[00:00\u201300:10] 'Today we will drain the hydraulic line on this press.'\n"
            "[00:10\u201300:22] 'Before opening any valve, depressurise the system completely.'\n"
            "[00:22\u201300:35] 'Place the drain pan under the front fitting.'\n"
            "[00:35\u201300:50] 'Open the front valve slowly.'\n"
            "[00:50\u201300:55] 'Wait for the fluid to stop flowing before closing the valve.'"
        ),
        branch_a_change=(
            "BRANCH A edit:\n"
            "  [00:10\u201300:22] 'Before opening any valve, depressurise the system completely.'\n"
            "  --> DELETED.\n"
            "This is the ONLY safety instruction in the video. No other segment\n"
            "restates the depressurise requirement."
        ),
        branch_b_change=(
            "BRANCH B edit:\n"
            "  [00:50\u201300:55] 'Wait for the fluid to stop flowing before closing the valve.'\n"
            "  --> REPLACED with: 'Wait for the fluid to stop, then close the valve.'\n"
            "  (minor wording tweak; safety content preserved)"
        ),
        mechanical_diff=(
            "Mechanical diff:\n"
            "  A: delete segment base_02 (00:10\u201300:22)\n"
            "  B: replace segment base_05 (00:50\u201300:55), minor wording tweak\n"
            "Disjoint timeline regions."
        ),
        expected_label="conflict",
        expected_conflict_type="prerequisite_loss",
        expected_branch_a_safe=False,
        expected_branch_b_safe=True,
    ),
    # ----- 8. Both branches weaken wording, redundant claim survives ------
    # Both A and B soften wording in different places, but a THIRD redundant
    # statement in BASE keeps the meaning intact in the combined video.
    Fixture(
        id="08_redundant_safe",
        base_context=(
            "BASE video (~60s, allergy-warning for a food product):\n"
            "[00:00\u201300:12] 'Our granola contains tree nuts.'\n"
            "[00:12\u201300:24] 'People with nut allergies should not eat this product.'\n"
            "[00:24\u201300:36] 'It may also contain traces of peanuts.'\n"
            "[00:36\u201300:50] 'If you have any allergy, consult a doctor before eating.'\n"
            "[00:50\u201301:00] 'Nut-allergic customers: do not consume. Ask staff for alternatives.'"
        ),
        branch_a_change=(
            "BRANCH A edit:\n"
            "  [00:12\u201300:24] 'People with nut allergies should not eat this product.'\n"
            "  --> REPLACED with: 'People with severe nut allergies should be cautious with this product.'\n"
            "  (softens 'should not eat' to 'should be cautious')"
        ),
        branch_b_change=(
            "BRANCH B edit:\n"
            "  [00:24\u201300:36] 'It may also contain traces of peanuts.'\n"
            "  --> DELETED."
        ),
        mechanical_diff=(
            "Mechanical diff:\n"
            "  A: replace base_02 wording (softens severity)\n"
            "  B: delete segment base_03 (00:24\u201300:36)\n"
            "Disjoint timeline regions.\n"
            "NOTE: BASE also contains [00:36\u201300:50] and [00:50\u201301:00], which\n"
            "independently convey the nut-allergy warning."
        ),
        expected_label="safe",
        expected_branch_a_safe=True,
        expected_branch_b_safe=True,
    ),
]


def get_fixture(id: str) -> Fixture:
    for f in FIXTURES:
        if f.id == id:
            return f
    raise KeyError(f"Unknown fixture id: {id}")


# Alias used by `make spike-dry` (the dry-run unit tests).
__all__ = [
    "FIXTURES",
    "Fixture",
    "SpikeLabel",
    "build_branch_view",
    "get_fixture",
    "parse_base_segments",
]
