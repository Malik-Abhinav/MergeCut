"""Phase 4 claim-centric evaluation runner.

Runs `analyze_claims()` on every Phase 4 fixture N times, scores
against `semantic_expected.EXPECTED`, and reports the per-claim
verdicts, the derived interactions, the variance, and the
canonical/safe-control flags the user named.

Phase 4 ASR gate (Phase 4 / build-log 2026-09-01):
    Before any M3 evaluation runs, every BASE / A / B video is
    passed through `tests.fixtures.asr_gate.validate_fixture`,
    which runs the Phase 2 media pipeline (incl. faster-whisper)
    on each video and flags any per-shot transcript that loses a
    negation / prerequisite / qualifier / exception / entity /
    temporal / causal marker. A complete JSON-serializable
    validation report is written to `<out-dir>/asr_validation.json`.

    If ANY fixture is `eligible_for_evaluation == False`, the
    harness refuses the live M3 evaluation with a clear
    diagnostic and a nonzero exit code. Ineligible fixtures
    are NEVER sent to M3 and NEVER appear in the scored
    results. (See build log 2026-09-01 for the upstream
    `say -v Albert` transcript-quality failure that motivated
    this gate.)

ASR processing is shared between the gate and the M3 eval:
    `process_video` is content-hash-cached on disk via
    `settings.derived_dir`. The gate writes the three
    `representation.json` files per fixture into the shared
    derived directory, and the M3 eval reads them back as
    cache hits — so the three videos are processed exactly
    once per fixture, not 3 × N_runs times.

Phase 4.5 — Reliability + accounting + forensic serialization:
    The harness collects EXACTLY `--runs` SUCCESSFUL `analyze_claims()`
    results per fixture. A provider failure (a transient upstream
    condition the bounded retry layer in `app.services.minimax._retry`
    has already exhausted) does NOT consume a successful slot — the
    harness re-attempts the whole `analyze_claims()` call. A schema
    or semantic failure (deterministic) IS recorded as a failed
    attempt but is NOT re-attempted. Failed whole-run attempts are
    bounded by `--max-failed-attempts` (default 12) so a persistently
    broken upstream cannot hang the eval.

    `MiniMaxClient.stats` counters are snapshotted per fixture and
    globally so the eval artifact reports the successful M3 call
    count, retries, final provider failure count, HTTP 429 count,
    and upstream 503 count consumed by each fixture.

    Failed attempts are persisted with `attempt_index` / `error` /
    `category` separately and do NOT appear in the semantic
    verdict distribution, modal verdict, or variance — those are
    computed only over successful runs.

    Every successful run serializes the FULL forensic detail of
    its `ClaimAnalysisArtifacts` (BaseClaim, branch_a/branch_b/
    combined ClaimSurvival, every ClaimInteraction with its
    deterministic derivation_reason + M3 explanation + M3
    recommended resolution, overall interaction/impact/
    confidence, call counts/timing/retries). The forensic
    artifact is written per fixture; for the user-named
    fixtures (02_qualifier_loss, 06_one_branch_broken,
    08_hard_negative_related) a compact focused-inspection
    file is also written.

Scoring rules (per the user's STEP 6):

  Canonical prerequisite fixture (01):
    >= 3/4 creates_new_conflict

  Other true conflict fixtures (02, 06 if classified as conflict):
    >= 75% modal correctness

  Safe / no-new-conflict fixtures (03, 04, 05, 07, 08):
    >= 75% modal correctness
    (i.e. interaction == "none" for the modal verdict)

  Overall interaction accuracy across all 8 fixtures:
    >= 75%

  Safe-unrelated fixture (04):
    not systematically false-positive

Usage:
    cd backend && uv run python ../scripts/run_claims_eval.py --runs 4

The script reuses the existing Phase 4 fixtures
(`tests.fixtures.semantic_fixtures`) and the live
MiniMax M3 client.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT / "backend"))

logger = logging.getLogger(__name__)

from app.config import get_settings
from app.models.claims import (
    CrossEditInteraction as ClaimCrossEditInteraction,
)
from app.models.media import VideoRepresentation
from app.services.media.pipeline import process_video
from app.services.media.transcript import clear_model_cache
from app.services.minimax.client import MiniMaxClient
from app.services.semantic.claims.orchestrate import analyze_claims
from app.services.semantic.claims.prompts_claims import (
    EVALUATION_PROMPT_VERSION,
    EXTRACTION_PROMPT_VERSION,
)
from tests.fixtures.asr_gate import (
    FixtureValidation,
    validate_fixture,
)
from tests.fixtures.asr_gate import (
    to_dict as asr_to_dict,
)
from tests.fixtures.semantic_expected import EXPECTED
from tests.fixtures.semantic_fixtures import SCRIPTS, build_fixture

# Phase 4.5 reliability helpers (same directory as this harness).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_claims_eval_reliability import (
    DEFAULT_MAX_FAILED_ATTEMPTS_PER_FIXTURE,
    FailedAttempt,
    RetryStatsSnapshot,
    run_attempts_until_success,
    serialize_claim_forensic,
    write_forensic_report,
)

# ---------------------------------------------------------------------------
# Per-run data structures.
# ---------------------------------------------------------------------------


@dataclass
class RunVerdict:
    """One successful run's claim-centric verdict for one fixture.

    `run_index` is the 1-based position of this successful run
    within the fixture's run window (1..N where N == args.runs).
    The harness persists every failed attempt SEPARATELY
    (with its whole-run attempt index, error, and category),
    so the verdict list contains ONLY successful outcomes.
    """

    fixture: str
    run_index: int
    overall_interaction: str
    per_claim_verdicts: dict[str, dict[str, str]] = field(default_factory=dict)
    # The list of (claim_id, branch, status) tuples in
    # `{claim_id: {branch: status}}` form.
    n_calls: int = 0
    n_retries: int = 0
    elapsed_s: float = 0.0
    matches_expected: bool = False


@dataclass
class FixtureReport:
    name: str
    expected_interaction: str
    runs: list[RunVerdict] = field(default_factory=list)
    modal_interaction: str = ""
    n_correct: int = 0  # successful runs where overall_interaction matches expected
    n_runs: int = 0  # successful run count (= len(runs))
    variance: int = 0  # number of distinct verdicts across SUCCESSFUL runs only
    verdict_distribution: dict[str, int] = field(default_factory=dict)
    per_claim_aggregated: dict[str, dict[str, str]] = field(default_factory=dict)
    # Phase 4.5: failed-attempt accounting and stats delta.
    failed_attempts: list[FailedAttempt] = field(default_factory=list)
    stats_delta: RetryStatsSnapshot = field(default_factory=RetryStatsSnapshot)


# ---------------------------------------------------------------------------
# M3 expected interaction mapping.
# ---------------------------------------------------------------------------

# Map the user's expected v2 interaction labels to the claim-centric
# vocabulary. (Same labels; the claim-centric pipeline uses the
# same `CrossEditInteraction` enum.)
EXPECTED_INTERACTION_MAP = {
    "none": ClaimCrossEditInteraction.NONE.value,
    "amplifies_existing_issue": ClaimCrossEditInteraction.AMPLIFIES_EXISTING_ISSUE.value,
    "creates_new_conflict": ClaimCrossEditInteraction.CREATES_NEW_CONFLICT.value,
}


# ---------------------------------------------------------------------------
# Fake M3 client (for dry runs).
# ---------------------------------------------------------------------------


class _DryRunClient:
    """A canned M3 client.

    The dry-run mode returns the *expected* per-claim verdicts
    so the deterministic interaction derivation can fire the
    right rule. This lets us verify the orchestrator end-to-end
    on the deterministic derivation without an M3 call.
    """

    def __init__(self, expected_per_fixture: dict[str, dict]) -> None:
        self._expected = expected_per_fixture
        # Each fixture has a unique transcript fragment we can
        # match against the user payload. We use a list of
        # candidate markers per fixture and accept the first
        # match — this lets us survive the slight transcript
        # variation that macOS `say` introduces between
        # invocations of the same text.
        self._marker_per_fixture: dict[str, list[str]] = {
            "01_canonical_prereq_loss": [
                "unplug it from the wall",
                "open the device",
                "battery compartment",
            ],
            "02_qualifier_loss": [
                "severe nut allergies",
                "Customers with any nut allergy",
            ],
            "03_cause_effect_safe": [
                "preheat the oven",
                "bake the cake",
            ],
            "04_safe_unrelated": [
                "whisk two eggs into the butter",
                "whisk three eggs into the butter",
                "wear protective gloves",
            ],
            "05_safe_independent": [
                "twice daily for seven days",
                "do not improve within a week",
            ],
            "06_one_branch_broken": [
                "recommended dose of this medication",
                "this medication as needed",
            ],
            "07_redundant_wording": [
                "nut allergies: ask staff for alternatives",
                "nut allergies: do not consume",
            ],
            "08_hard_negative_related": [
                "disable secure boot",
                "run the installer as administrator",
            ],
        }
        self._current_fixture: str | None = None
        self.calls: list[dict] = []
        self.model = "DRY-RUN"
        # A live `MiniMaxClient` exposes `self.stats: RetryStats`
        # so the harness can snapshot provider counters per
        # fixture. The dry-run client does no I/O, but it must
        # still expose the same attribute so the snapshot
        # machinery is uniform across both paths.
        from app.services.minimax._retry import RetryStats

        self.stats: RetryStats = RetryStats()

    def _match_fixture(self, user: str) -> str | None:
        # Find the fixture whose markers match the most distinct
        # substrings. This survives `say` transcript variation.
        best: tuple[int, str | None] = (0, None)
        for name, markers in self._marker_per_fixture.items():
            score = sum(1 for m in markers if m in user)
            if score > best[0]:
                best = (score, name)
        return best[1]

    def chat_json_sync(self, *, system: str, user: str, **kwargs) -> str:  # type: ignore[no-untyped-def]
        self.calls.append({"system": system, "user": user})
        # Re-evaluate the marker for every call. The orchestrator
        # calls this client across multiple fixtures in sequence,
        # and we need to detect each fixture's first call fresh
        # (so the dry-run stays per-fixture even when a
        # previous fixture's marker persists in user payloads).
        matched_name = self._match_fixture(user)
        if matched_name is not None:
            self._current_fixture = matched_name
        # If no marker matched at all, fall back to "preserved"
        # (the orchestrator parses an empty JSON as a default
        # `ClaimEvaluation` and gets PRESERVED). This avoids
        # leaking the previous fixture's verdicts into the next.
        if self._current_fixture is None or self._match_fixture(user) is None:
            return json.dumps({})
        matched = self._expected.get(self._current_fixture)
        if matched is None:
            return json.dumps({})
        if "BASE-claim extractor" in system:
            return json.dumps({"claims": matched.get("claims", [])})
        if "per-claim preservation evaluator" in system:
            cid = "C?"
            for line in user.split("\n"):
                if line.startswith("claim_id:"):
                    cid = line.split(":", 1)[1].strip()
                    break
            branch = "branch_a"
            for line in user.split("\n"):
                if line.startswith("branch:"):
                    branch = line.split(":", 1)[1].strip()
                    break
            per_claim = matched.get("per_claim_verdicts", {}).get(cid, {})
            status = per_claim.get(branch, "preserved")
            return json.dumps(
                {
                    "claim_id": cid,
                    "status": status,
                    "surviving_evidence": [],
                    "rationale": f"dry-run {status}",
                    "confidence": 1.0,
                }
            )
        if "human-readable explainer" in system:
            return json.dumps(
                {
                    "explanation": "dry-run explanation",
                    "recommended_resolution": "dry-run fix",
                }
            )
        return json.dumps({})


# ---------------------------------------------------------------------------
# Live M3 client wrapper.
# ---------------------------------------------------------------------------


def _build_live_client() -> MiniMaxClient:
    settings = get_settings()
    if not settings.gmi_api_key:
        raise RuntimeError("GMI_API_KEY not set. Use --dry to run without M3 calls.")
    return MiniMaxClient(settings=settings)


# ---------------------------------------------------------------------------
# Dry-run fixtures — expected per-claim verdicts for each script.
# ---------------------------------------------------------------------------

# For the dry-run mode, we hand the deterministic derivation the
# per-claim verdicts that the user said the fixture should produce
# (so R1/R3/R5 etc. fire correctly and we can confirm the
# deterministic step is wired right end-to-end).
DRY_RUN_PER_CLAIM = {
    "01_canonical_prereq_loss": {
        "claims": [
            {
                "claim_id": "C1",
                "meaning": "The device must be unplugged before the cover is opened.",
                "claim_type": "prerequisite",
                "importance": "critical",
                "evidence_regions": [
                    {"start": 0.0, "end": 3.0, "description": "shot 0 prerequisite"},
                    {
                        "start": 3.0,
                        "end": 6.0,
                        "description": "shot 1 follow-up references it",
                    },
                ],
                "equivalents": [],
            }
        ],
        "per_claim_verdicts": {
            "C1": {
                "branch_a": "preserved",  # A drops shot 0 but keeps shot 1 with the
                # follow-up sentence which implies the prerequisite.
                "branch_b": "preserved",  # B keeps shot 0.
                "combined": "broken",  # Both A and B drop their own copy; combined
                # viewer no longer hears the prerequisite.
            }
        },
    },
    "02_qualifier_loss": {
        "claims": [
            {
                "claim_id": "C1",
                "meaning": "Patients with severe nut allergies must avoid this product.",
                "claim_type": "qualifier",
                "importance": "critical",
                "evidence_regions": [
                    {"start": 0.0, "end": 1.0, "description": "shot 0 qualifier"},
                ],
                "equivalents": [
                    {
                        "start": 2.0,
                        "end": 3.0,
                        "description": "shot 2 restates the qualifier",
                    },
                ],
            }
        ],
        "per_claim_verdicts": {
            "C1": {
                "branch_a": "preserved",  # A is BASE (no edits).
                "branch_b": "preserved",  # B narrows on shot 0 but shot 2 restates it.
                "combined": "broken",  # both branches' narrowing combine to drop
                # the severe qualifier; the restatement is
                # also narrowed (combined reconstruction
                # drops the qualifier from the equivalent too).
            }
        },
    },
    "03_cause_effect_safe": {
        "claims": [
            {
                "claim_id": "C1",
                "meaning": "Bake the cake for 30 minutes.",
                "claim_type": "instruction",
                "importance": "high",
                "evidence_regions": [
                    {"start": 0.0, "end": 1.0, "description": "shot 1 the duration"},
                ],
                "equivalents": [],
            }
        ],
        "per_claim_verdicts": {
            "C1": {
                "branch_a": "preserved",  # A drops shot 0 but keeps shot 1.
                "branch_b": "preserved",  # B keeps shot 0 + rewrites shot 1 to "bake the cake".
                "combined": "preserved",  # loose reading: "bake the cake" still stands.
            }
        },
    },
    "04_safe_unrelated": {
        "claims": [
            {
                "claim_id": "C1",
                "meaning": "Whisk two eggs into the butter.",
                "claim_type": "instruction",
                "importance": "high",
                "evidence_regions": [
                    {"start": 0.0, "end": 1.0, "description": "shot 0"}
                ],
                "equivalents": [],
            },
            {
                "claim_id": "C2",
                "meaning": "Wear protective gloves when handling the blade.",
                "claim_type": "prohibition",
                "importance": "high",
                "evidence_regions": [
                    {"start": 2.0, "end": 3.0, "description": "shot 2"}
                ],
                "equivalents": [],
            },
        ],
        "per_claim_verdicts": {
            "C1": {
                "branch_a": "preserved",
                "branch_b": "preserved",
                "combined": "preserved",
            },
            "C2": {
                "branch_a": "preserved",
                "branch_b": "preserved",
                "combined": "preserved",
            },
        },
    },
    "05_safe_independent": {
        "claims": [
            {
                "claim_id": "C1",
                "meaning": "Apply the cream twice daily for seven days.",
                "claim_type": "instruction",
                "importance": "high",
                "evidence_regions": [
                    {"start": 0.0, "end": 1.0, "description": "shot 0"}
                ],
                "equivalents": [],
            }
        ],
        "per_claim_verdicts": {
            "C1": {
                "branch_a": "preserved",  # A adds "gently" but keeps everything else.
                "branch_b": "preserved",  # B rewrites the second sentence but the 7-day claim is kept.
                "combined": "preserved",
            }
        },
    },
    "06_one_branch_broken": {
        "claims": [
            {
                "claim_id": "C1",
                "meaning": "Do not exceed the recommended dose of this medication.",
                "claim_type": "prohibition",
                "importance": "critical",
                "evidence_regions": [
                    {"start": 0.0, "end": 1.0, "description": "shot 0"}
                ],
                "equivalents": [],
            }
        ],
        "per_claim_verdicts": {
            "C1": {
                "branch_a": "broken",  # A rewrites the safety threshold.
                "branch_b": "preserved",  # B is BASE.
                "combined": "broken",  # Combined has the rewritten (broken) version.
            }
        },
    },
    "07_redundant_wording": {
        "claims": [
            {
                "claim_id": "C1",
                "meaning": "All nut-allergic customers must avoid this product.",
                "claim_type": "prohibition",
                "importance": "critical",
                "evidence_regions": [
                    {"start": 0.0, "end": 3.0, "description": "shot 0"}
                ],
                "equivalents": [
                    {
                        "start": 3.0,
                        "end": 6.0,
                        "description": "shot 1 restates (narrowed)",
                    },
                ],
            }
        ],
        "per_claim_verdicts": {
            "C1": {
                "branch_a": "preserved",  # A narrows shot 1 but shot 0 still says "all".
                "branch_b": "preserved",  # B is BASE.
                "combined": "preserved",  # shot 0's "all" still covers the claim.
            }
        },
    },
    "08_hard_negative_related": {
        "claims": [
            {
                "claim_id": "C1",
                "meaning": "Disable secure boot before installing the driver.",
                "claim_type": "instruction",
                "importance": "high",
                "evidence_regions": [
                    {"start": 0.0, "end": 1.0, "description": "shot 0"}
                ],
                "equivalents": [],
            },
            {
                "claim_id": "C2",
                "meaning": "Run the installer and restart the computer.",
                "claim_type": "instruction",
                "importance": "high",
                "evidence_regions": [
                    {"start": 1.0, "end": 2.0, "description": "shot 1"}
                ],
                "equivalents": [],
            },
        ],
        "per_claim_verdicts": {
            "C1": {
                "branch_a": "preserved",
                "branch_b": "preserved",
                "combined": "preserved",
            },
            "C2": {
                "branch_a": "preserved",
                "branch_b": "preserved",
                "combined": "preserved",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Fixture representation loader (extracted from `main()` so we can
# unit-test the no-redundant-ASR property).
# ---------------------------------------------------------------------------


def _load_fixture_representations(
    *,
    fixture_name: str,
    base_path: Path,
    a_path: Path,
    b_path: Path,
    out_dir: Path,
) -> tuple[VideoRepresentation, VideoRepresentation, VideoRepresentation]:
    """Load BASE / A / B `VideoRepresentation`s once per fixture.

    The Phase 4 ASR gate has already populated the per-fixture
    representation cache (via its own `validate_fixture` calls);
    `process_video` therefore hits the disk cache and no ASR is
    performed here. The eval loop reuses the returned objects
    across all `--runs` M3 iterations so the same three videos
    are processed exactly once per fixture, not 3 × --runs times.
    """
    settings = get_settings()
    settings.derived_dir = out_dir / "derived" / fixture_name
    clear_model_cache()
    base_rep = process_video(base_path)
    a_rep = process_video(a_path)
    b_rep = process_video(b_path)
    return base_rep, a_rep, b_rep


# ---------------------------------------------------------------------------
# Per-fixture run driver (Phase 4.5 — reliability + accounting).
# ---------------------------------------------------------------------------


def _run_fixture_with_reliability(
    *,
    fixture_name: str,
    expected_interaction: str,
    client: MiniMaxClient | _DryRunClient,
    base_rep: VideoRepresentation,
    a_rep: VideoRepresentation,
    b_rep: VideoRepresentation,
    n_target_runs: int,
    max_failed_attempts: int,
    sleeper: Callable[[float], None] | None = None,
    attempt_fn: Callable[[], Any] | None = None,
) -> tuple[FixtureReport, list[dict]]:
    """Drive one fixture through ``n_target_runs`` SUCCESSFUL
    whole-run ``analyze_claims()`` calls.

    Returns ``(report, forensics)``:
      - ``report``: the per-fixture report (successful verdicts,
        failed attempts, stats delta, modal/variance computed
        over successes only).
      - ``forensics``: the list of per-run forensic payloads
        (one per successful run) suitable for
        ``write_forensic_report``.

    The driver's contract is:

      - EXACTLY ``n_target_runs`` successful runs are recorded
        OR the driver stops early on the ``max_failed_attempts``
        cap.
      - Failed attempts are classified into
        ``FailureCategory.PROVIDER`` (transient, may re-attempt)
        vs ``SCHEMA`` / ``SEMANTIC`` / ``ORCHESTRATOR`` /
        ``UNKNOWN`` (deterministic, recorded but not re-attempted).
      - Provider failures do NOT consume a successful slot.
      - Per-claim modal verdicts and the variance are computed
        from SUCCESSFUL runs only.

    ``attempt_fn`` is the callable the driver invokes per
    attempt. The default is the production
    ``analyze_claims(base, branch_a, branch_b, client=client)``
    closure; tests inject a stub that returns canned
    ``ClaimAnalysisArtifacts`` or raises synthetic failures.
    """
    report = FixtureReport(
        name=fixture_name,
        expected_interaction=expected_interaction,
    )
    # The client must expose .stats (live `MiniMaxClient` does;
    # the dry-run client also exposes a compatible attribute).
    stats = getattr(client, "stats", None)
    if stats is None:
        # Defensive: the dry-run client keeps a `calls` list but
        # no RetryStats. Build a stub so the snapshot machinery
        # stays uniform across both paths.
        from app.services.minimax._retry import RetryStats

        stats = RetryStats()

    if attempt_fn is None:

        def _attempt() -> Any:
            return analyze_claims(
                base=base_rep,
                branch_a=a_rep,
                branch_b=b_rep,
                client=client,  # type: ignore[arg-type]
            )

        attempt_fn = _attempt

    successes, failed_attempts, stats_delta = run_attempts_until_success(
        fn=attempt_fn,
        stats=stats,
        fixture_name=fixture_name,
        target_successful_runs=n_target_runs,
        max_failed_attempts=max_failed_attempts,
        sleeper=sleeper,
    )
    report.failed_attempts = failed_attempts
    report.stats_delta = stats_delta

    forensics: list[dict] = []
    for run_idx, outcome in enumerate(successes, start=1):
        artifacts = outcome.result
        verdict = RunVerdict(
            fixture=fixture_name,
            run_index=run_idx,
            overall_interaction=artifacts.analysis.overall_interaction.value,
            n_calls=(
                artifacts.n_evaluation_calls
                + artifacts.n_explanation_calls
                + artifacts.n_extraction_calls
            ),
            n_retries=artifacts.evaluation_retries + artifacts.extraction_retries,
            elapsed_s=outcome.elapsed_s,
            matches_expected=(
                artifacts.analysis.overall_interaction.value == expected_interaction
            ),
        )
        # Capture per-claim verdicts.
        for bc_name, bc in [
            ("branch_a", artifacts.analysis.branch_a_claims),
            ("branch_b", artifacts.analysis.branch_b_claims),
            ("combined", artifacts.analysis.combined_claims),
        ]:
            for cs in bc.claim_survivals:
                verdict.per_claim_verdicts.setdefault(cs.claim_id, {})[bc_name] = (
                    cs.status.value
                )
        report.runs.append(verdict)
        forensics.append(serialize_claim_forensic(artifacts))
        mark = "OK" if verdict.matches_expected else "MISS"
        print(
            f"  {fixture_name:32s} run {run_idx}/{n_target_runs}  "
            f"I={verdict.overall_interaction:24s}  [{mark}]"
        )

    report.n_runs = len(report.runs)
    report.n_correct = sum(1 for r in report.runs if r.matches_expected)
    # Compute modal/variance over SUCCESSFUL runs only. A failed
    # attempt is an accounting event, not a verdict, so 'error'
    # never appears in the distribution.
    verdict_dist = Counter(r.overall_interaction for r in report.runs)
    report.verdict_distribution = dict(verdict_dist)
    report.variance = len(verdict_dist)
    report.modal_interaction = verdict_dist.most_common(1)[0][0] if verdict_dist else ""
    # Aggregate per-claim across runs (modal) using only the
    # SUCCESSFUL verdicts. Per-claim statuses are also guaranteed
    # to be one of preserved / degraded / broken because they come
    # from the orchestrator's per-claim Pydantic model.
    per_claim: dict[str, dict[str, str]] = {}
    if report.runs:
        claim_ids: set[str] = set()
        for run_record in report.runs:
            claim_ids.update(run_record.per_claim_verdicts.keys())
        for cid in claim_ids:
            for branch in ("branch_a", "branch_b", "combined"):
                statuses = [
                    run_record.per_claim_verdicts.get(cid, {}).get(branch, "")
                    for run_record in report.runs
                ]
                # Filter out empty entries (a successful run with
                # no per-claim verdict for this branch) but DO NOT
                # filter out any verdict value. Failed attempts
                # never reach here because they are not appended
                # to report.runs.
                statuses = [s for s in statuses if s]
                per_claim.setdefault(cid, {})[branch] = (
                    Counter(statuses).most_common(1)[0][0] if statuses else ""
                )
    report.per_claim_aggregated = per_claim
    return report, forensics


# ---------------------------------------------------------------------------
# ASR gate phase (extracted from `main()` for unit-testability).
# ---------------------------------------------------------------------------


def _run_asr_gate(
    *,
    paths: dict[str, tuple[Path, Path, Path]],
    out_dir: Path,
) -> tuple[dict[str, FixtureValidation], int]:
    """Run `validate_fixture` on every fixture and persist the report.

    Returns ``(validations, exit_code)``:
      - ``validations``: name -> FixtureValidation for every script.
      - ``exit_code``: ``0`` when every fixture is eligible (the M3
        eval may proceed); ``2`` when at least one fixture is
        ineligible (the harness refuses the live M3 eval).

    The gate writes the per-fixture `representation.json` files into
    ``<out_dir>/derived/<fixture_name>`` so the downstream M3 eval can
    load the three VideoRepresentations as cache hits (no redundant
    ASR between the gate and the eval). Always writes
    ``<out_dir>/asr_validation.json`` so the user can read the
    disqualification reasons even when the harness refuses.
    """
    settings = get_settings()
    validations: dict[str, FixtureValidation] = {}
    print("ASR gate: validating every fixture's BASE/A/B transcripts...")
    for script in SCRIPTS:
        base_path, a_path, b_path = paths[script.name]
        settings.derived_dir = out_dir / "derived" / script.name
        clear_model_cache()
        validations[script.name] = validate_fixture(
            name=script.name,
            base_path=base_path,
            a_path=a_path,
            b_path=b_path,
            base_expected=[line.text for line in script.base_lines()],
            a_expected=[line.text for line in script.branch_a_lines()],
            b_expected=[line.text for line in script.branch_b_lines()],
        )
        v = validations[script.name]
        mark = "OK" if v.eligible_for_evaluation else "FAIL"
        print(
            f"  {script.name:32s}  eligible={v.eligible_for_evaluation!s:5s}  "
            f"min_sim={v.min_similarity:.2f}  flagged={v.flagged_categories}  [{mark}]"
        )

    asr_report = {
        "fixtures": [asr_to_dict(v) for v in validations.values()],
        "eligible_count": sum(
            1 for v in validations.values() if v.eligible_for_evaluation
        ),
        "total": len(validations),
    }
    (out_dir / "asr_validation.json").write_text(json.dumps(asr_report, indent=2))
    print(
        f"\nASR gate: {asr_report['eligible_count']}/{asr_report['total']} "
        f"fixtures eligible. Report: {out_dir / 'asr_validation.json'}"
    )

    ineligible = [n for n, v in validations.items() if not v.eligible_for_evaluation]
    if ineligible:
        print(
            "\nERROR: refusing live M3 evaluation — one or more fixtures failed "
            "the ASR semantic-integrity gate.\n",
            file=sys.stderr,
        )
        for name in ineligible:
            v = validations[name]
            print(f"  {name}:", file=sys.stderr)
            for reason in v.disqualify_reasons():
                print(f"    - {reason}", file=sys.stderr)
            for warn in v.soft_warnings():
                print(f"    ~ {warn}", file=sys.stderr)
        print(
            "\nFix the upstream transcript quality (re-record the offending "
            "fixture, switch TTS voice, or rephrase the script) and re-run. "
            "Ineligible fixtures are NEVER sent to M3 and NEVER appear as "
            "valid scored results.",
            file=sys.stderr,
        )
        return validations, 2
    return validations, 0


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=int, default=4, help="Successful runs per fixture (default 4)."
    )
    parser.add_argument(
        "--max-failed-attempts",
        type=int,
        default=DEFAULT_MAX_FAILED_ATTEMPTS_PER_FIXTURE,
        help=(
            "Maximum failed whole-run attempts per fixture before the "
            "harness stops trying (default 12). Provider failures are "
            "retried until this cap; schema/semantic failures are "
            "recorded but not retried."
        ),
    )
    parser.add_argument(
        "--dry", action="store_true", help="Skip M3 calls; use canned verdicts."
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("/tmp/phase4_claims_eval"),
        help="Where to write the eval artifact (default /tmp/phase4_claims_eval).",
    )
    args = parser.parse_args()

    if sys.platform != "darwin" or not shutil.which("say"):
        print("ERROR: requires macOS `say`.", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    settings.derived_dir = args.out_dir / "derived"
    settings.upload_dir = args.out_dir / "uploads"

    # Build fixtures.
    fx_dir = args.out_dir / "fixtures"
    paths: dict[str, tuple[Path, Path, Path]] = {}
    for script in SCRIPTS:
        clear_model_cache()
        d = args.out_dir / "derived" / script.name
        d.mkdir(parents=True, exist_ok=True)
        settings.derived_dir = d
        paths[script.name] = build_fixture(script, fx_dir)

    # Phase 4 ASR gate: validate every fixture's BASE/A/B transcripts
    # before any M3 evaluation runs. See `_run_asr_gate` for the
    # gate contract; on refusal we exit with code 2 and a clear
    # diagnostic, and ineligible fixtures are NEVER sent to M3 or
    # included as valid scored results.
    _asr_validations, gate_rc = _run_asr_gate(paths=paths, out_dir=args.out_dir)
    if gate_rc != 0:
        return gate_rc

    # Build client.
    if args.dry:
        client: MiniMaxClient | _DryRunClient = _DryRunClient(DRY_RUN_PER_CLAIM)
        print("DRY-RUN: using canned M3 responses.")
    else:
        client = _build_live_client()
        print(
            f"Live M3 eval; model={client.model} "
            f"extraction_version={EXTRACTION_PROMPT_VERSION} "
            f"evaluation_version={EVALUATION_PROMPT_VERSION}"
        )
    print(
        f"Runs per fixture: {args.runs} (exactly this many SUCCESSFUL runs are "
        f"required); max failed attempts per fixture: {args.max_failed_attempts}.\n"
    )

    # Map expected labels.
    expected_by_name = {e.name: e for e in EXPECTED}

    # Snapshot global stats BEFORE any fixture runs.
    global_baseline = RetryStatsSnapshot.from_stats(client.stats)
    global_stats_start = time.monotonic()

    # Run every fixture until N successful runs each.
    reports: list[FixtureReport] = []
    all_forensics: dict[str, list[dict]] = {}
    for fixture_name, (base_path, a_path, b_path) in paths.items():
        expected = expected_by_name[fixture_name]
        # Load each video's processed representation ONCE per fixture
        # and reuse it across all `--runs` M3 iterations. The gate
        # already wrote the per-video `representation.json` to the
        # shared per-fixture derived dir (see ASR gate block above);
        # `process_video` reads them back as cache hits, so no ASR
        # is performed here.
        base_rep, a_rep, b_rep = _load_fixture_representations(
            fixture_name=fixture_name,
            base_path=base_path,
            a_path=a_path,
            b_path=b_path,
            out_dir=args.out_dir,
        )
        fixture_baseline = RetryStatsSnapshot.from_stats(client.stats)
        report, forensics = _run_fixture_with_reliability(
            fixture_name=fixture_name,
            expected_interaction=EXPECTED_INTERACTION_MAP[expected.interaction],
            client=client,
            base_rep=base_rep,
            a_rep=a_rep,
            b_rep=b_rep,
            n_target_runs=args.runs,
            max_failed_attempts=args.max_failed_attempts,
        )
        fixture_end_stats = RetryStatsSnapshot.from_stats(client.stats)
        # The driver already wrote stats_delta; recompute here as a
        # sanity check (the driver's stats_delta uses the same
        # baseline machinery so this should be a no-op).
        assert fixture_end_stats.delta(fixture_baseline).as_dict() == (
            report.stats_delta.as_dict()
        ), (
            f"stats delta mismatch for {fixture_name}: "
            f"driver={report.stats_delta.as_dict()} "
            f"recomputed={fixture_end_stats.delta(fixture_baseline).as_dict()}"
        )
        reports.append(report)
        all_forensics[fixture_name] = forensics
        # Write the per-fixture forensic artifact (and the focused
        # inspection file for the user-named fixtures).
        forensic_path = write_forensic_report(
            out_dir=args.out_dir,
            fixture_name=fixture_name,
            n_successful_runs=report.n_runs,
            forensics=forensics,
            failed_attempts=report.failed_attempts,
            stats_delta=report.stats_delta,
        )
        # Phase 4 representation-correctness: persist the actual
        # verbatim content per fixture (BASE / A / B / combined).
        # Uses the first successful run's representation snapshot
        # so the final reporting layer can show "exactly what M3
        # saw" for each fixture. Writes a standalone JSON file
        # (one per fixture) plus the per-fixture diagnostic block
        # already embedded inside the forensic artifact.
        if forensics:
            try:
                rep_snapshot = forensics[0].get("representation", {})
                rep_path = args.out_dir / f"{fixture_name}_representation.json"
                rep_path.write_text(
                    json.dumps(rep_snapshot, indent=2, ensure_ascii=False, default=str)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "representation_diagnostics.write_failed fixture=%s err=%r",
                    fixture_name,
                    exc,
                )
        print(
            f"  → modal: I={report.modal_interaction}  "
            f"({report.n_correct}/{report.n_runs} correct, "
            f"variance={report.variance}, "
            f"failed_attempts={len(report.failed_attempts)}, "
            f"forensic={forensic_path.name})\n"
        )

    global_end_stats = RetryStatsSnapshot.from_stats(client.stats)
    global_stats_delta = global_end_stats.delta(global_baseline)
    global_elapsed_s = time.monotonic() - global_stats_start

    # Save artifact.
    raw = {
        "extraction_prompt_version": EXTRACTION_PROMPT_VERSION,
        "evaluation_prompt_version": EVALUATION_PROMPT_VERSION,
        "n_runs_per_fixture": args.runs,
        "max_failed_attempts_per_fixture": args.max_failed_attempts,
        "dry_run": args.dry,
        "model": getattr(client, "model", "?"),
        "global_stats_delta": global_stats_delta.as_dict(),
        "global_elapsed_s": global_elapsed_s,
        "fixtures": [
            {
                "name": fixture_report.name,
                "expected": fixture_report.expected_interaction,
                "modal": fixture_report.modal_interaction,
                "n_correct": fixture_report.n_correct,
                "n_runs": fixture_report.n_runs,
                "variance": fixture_report.variance,
                "verdict_distribution": fixture_report.verdict_distribution,
                "per_claim_aggregated": fixture_report.per_claim_aggregated,
                "stats_delta": fixture_report.stats_delta.as_dict(),
                "failed_attempts": [
                    fa.as_dict() for fa in fixture_report.failed_attempts
                ],
                "runs": [asdict(run) for run in fixture_report.runs],
            }
            for fixture_report in reports
        ],
    }
    (args.out_dir / "phase4_claims_eval.json").write_text(json.dumps(raw, indent=2))

    # Summary.
    print()
    print("=" * 60)
    print("PHASE 4 CLAIM-CENTRIC EVAL SUMMARY")
    print("=" * 60)
    n_correct_interaction = sum(
        1 for rep in reports if rep.modal_interaction == rep.expected_interaction
    )
    print(
        f"Modal-correct on overall interaction:  {n_correct_interaction}/{len(reports)}"
    )
    canonical_01 = next(
        (rep for rep in reports if rep.name == "01_canonical_prereq_loss"), None
    )
    canonical_correct_count = (
        sum(
            1
            for run in canonical_01.runs
            if run.overall_interaction == "creates_new_conflict"
        )
        if canonical_01 is not None
        else 0
    )
    print(
        f"Canonical 01 creates_new_conflict:    "
        f"{canonical_correct_count}/{args.runs} (need >= 3/4)"
    )
    # Per-fixture variance summary.
    print()
    print("Per-fixture variance:")
    for fixture_report in reports:
        print(
            f"  {fixture_report.name:32s}  modal={fixture_report.modal_interaction:24s}  "
            f"variance={fixture_report.variance}  exp={fixture_report.expected_interaction}"
        )
    safe_04 = next((rep for rep in reports if rep.name == "04_safe_unrelated"), None)
    safe_04_fp = safe_04 is not None and safe_04.modal_interaction != "none"

    # User gate.
    gate_canonical = canonical_correct_count >= 3
    gate_overall = n_correct_interaction >= max(1, int(0.75 * len(reports)))
    gate_safe_no_fp = not safe_04_fp
    overall = gate_canonical and gate_overall and gate_safe_no_fp

    print()
    print("User gate:")
    print(
        f"  canonical 01 creates_new_conflict >= 3/4:  "
        f"{canonical_correct_count}/{args.runs}  -> {'PASS' if gate_canonical else 'FAIL'}"
    )
    print(
        f"  overall interaction accuracy >= 75%:        "
        f"{n_correct_interaction}/{len(reports)}  -> {'PASS' if gate_overall else 'FAIL'}"
    )
    print(
        f"  safe_unrelated NOT systematic FP:          "
        f"modal={safe_04.modal_interaction if safe_04 is not None else 'n/a'}  "
        f"-> {'PASS' if gate_safe_no_fp else 'FAIL'}"
    )
    print()
    print("Provider stats (cumulative across all fixtures):")
    print(f"  successful M3 calls:        {global_stats_delta.successful_calls}")
    print(f"  retries:                    {global_stats_delta.retries}")
    print(f"  final provider failures:    {global_stats_delta.provider_failures}")
    print(f"  HTTP 429 count:             {global_stats_delta.http_429_count}")
    print(f"  upstream-503 count:         {global_stats_delta.upstream_503_count}")
    print(
        f"  total failed whole-run attempts: "
        f"{sum(len(rep.failed_attempts) for rep in reports)}"
    )
    print(f"  total elapsed:              {global_elapsed_s:.2f}s")
    print()
    print(f"OVERALL: {'GO' if overall else 'INVESTIGATE'}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
