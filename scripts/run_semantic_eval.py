"""Phase 4 semantic-evaluation runner.

Builds the 8 controlled real-video semantic fixtures, runs the
end-to-end orchestrator (alignment -> context -> M3) on each
fixture N times, scores the results against the expected labels
in `tests.fixtures.semantic_expected`, and reports:

- Per-fixture: per-run verdicts + the most-common verdict.
- Per-axis accuracy: fraction of fixtures where the modal
  verdict matches the expected value.
- Cross-edit interaction accuracy: fraction of fixtures
  where the modal `interaction_type` matches.
- Per-fixture variance: how many distinct verdicts the
  model produced across runs.
- False positives / false negatives on the canonical
  MergeCut case (01) and the safe controls (04, 05).

Usage:
    # Live M3 eval (requires GMI_API_KEY in .env at repo root).
    cd backend && uv run python ../scripts/run_semantic_eval.py

    # Dry-run on the schema/alignment-only pipeline (skips
    # M3 calls; useful in CI / on a stripped env).
    cd backend && uv run python ../scripts/run_semantic_eval.py --dry

    # Custom number of runs.
    cd backend && uv run python ../scripts/run_semantic_eval.py --runs 2

The script is intentionally verbose: every run is logged with
the model's response and the score, so the build log can
include a faithful record.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

# chdir to the repo root so pydantic-settings finds the .env
# file (which lives at the repo root, not under backend/).
REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.services.media.pipeline import process_video  # noqa: E402
from app.services.media.transcript import clear_model_cache  # noqa: E402
from app.services.minimax.client import MiniMaxClient  # noqa: E402
from app.services.semantic.prompts_v2 import PROMPT_VERSION  # noqa: E402
from app.services.semantic.run import analyze_merge  # noqa: E402
from tests.fixtures.semantic_expected import EXPECTED, Expected  # noqa: E402
from tests.fixtures.semantic_fixtures import SCRIPTS, build_fixture  # noqa: E402


# ---------------------------------------------------------------------------
# Data structures.
# ---------------------------------------------------------------------------


@dataclass
class FixtureVerdict:
    """One model's response to one fixture, after scoring."""

    fixture: str
    run_index: int
    branch_a_impact: str
    branch_b_impact: str
    combined_impact: str
    interaction: str
    confidence: float
    retries: int
    raw_response: str
    matches_expected: bool
    matches_expected_per_axis: dict[str, bool]
    notes: str = ""


@dataclass
class FixtureReport:
    name: str
    expected: dict[str, str]
    runs: list[FixtureVerdict] = field(default_factory=list)
    majority_a: str = ""
    majority_b: str = ""
    majority_combined: str = ""
    majority_interaction: str = ""
    n_runs: int = 0
    n_correct: int = 0  # runs where ALL four axes match expected
    verdict_distribution: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _modal(values: list[str]) -> str:
    if not values:
        return ""
    counts = Counter(values)
    return counts.most_common(1)[0][0]


def _verdict_key(v: FixtureVerdict) -> str:
    return f"A={v.branch_a_impact}|B={v.branch_b_impact}|C={v.combined_impact}|I={v.interaction}"


def _score_run(
    v: FixtureVerdict,
    expected: Expected,
) -> None:
    """Mutate v in place: populate matches_expected and per-axis flags."""
    v.matches_expected_per_axis = {
        "branch_a_impact": v.branch_a_impact == expected.branch_a_impact,
        "branch_b_impact": v.branch_b_impact == expected.branch_b_impact,
        "combined_impact": v.combined_impact == expected.combined_impact,
        "interaction": v.interaction == expected.interaction,
    }
    v.matches_expected = all(v.matches_expected_per_axis.values())


# ---------------------------------------------------------------------------
# Dry-run fallback.
# ---------------------------------------------------------------------------


class _DryRunClient:
    """A mock M3 client that returns a synthetic canonical
    response — used when --dry is passed (no GMI key, no
    network). Records every call so the eval log shows the
    exact context the model would have seen.

    The mock returns one canned verdict per call, in the
    order the script asks for them. The eval runner hands
    the mock a "next" expected label between calls, so each
    call returns the right canned response for whichever
    fixture is currently being evaluated.
    """

    def __init__(self) -> None:
        self._next_exp: Expected | None = None
        self.calls: list[dict[str, str]] = []
        self.model = "DRY-RUN-MOCK"

    def set_next_expected(self, exp: Expected) -> None:
        self._next_exp = exp

    def chat_json_sync(self, *, system: str, user: str, **kwargs) -> str:  # type: ignore[no-untyped-def]
        self.calls.append({"system": system, "user": user})
        if self._next_exp is None:
            exp = Expected(
                name="?",
                branch_a_impact="preserved",
                branch_b_impact="preserved",
                combined_impact="preserved",
                interaction="none",
            )
        else:
            exp = self._next_exp
        return json.dumps(_synthesize_payload(exp))


def _synthesize_payload(exp: Expected) -> dict:
    """Render a SyntheticAnalysisV2 JSON from the expected label."""
    return {
        "branch_a_impact": {
            "branch": "branch_a",
            "impact_level": exp.branch_a_impact,
            "affected_claims": ["synthetic claim"],
            "preserved_equivalents": [],
            "evidence": [
                {"video": "base", "start": 0.0, "end": 1.0, "description": "synthetic"}
            ],
            "confidence": 1.0,
            "rationale": f"synthetic dry-run, expected={exp.branch_a_impact}",
        },
        "branch_b_impact": {
            "branch": "branch_b",
            "impact_level": exp.branch_b_impact,
            "affected_claims": ["synthetic claim"],
            "preserved_equivalents": [],
            "evidence": [
                {"video": "base", "start": 0.0, "end": 1.0, "description": "synthetic"}
            ],
            "confidence": 1.0,
            "rationale": f"synthetic dry-run, expected={exp.branch_b_impact}",
        },
        "combined_impact": exp.combined_impact,
        "interactions": [
            {
                "branch_a_edit_ids": ["shot_0000"],
                "branch_b_edit_ids": ["shot_0000"],
                "combined_impact": exp.combined_impact,
                "interaction_type": exp.interaction,
                "conflict_type": (
                    "prerequisite_loss"
                    if exp.interaction == "creates_new_conflict"
                    else None
                ),
                "base_claim": "synthetic claim",
                "branch_a_effect": "synthetic",
                "branch_b_effect": "synthetic",
                "combined_effect": "synthetic",
                "evidence": [
                    {
                        "video": "base",
                        "start": 0.0,
                        "end": 1.0,
                        "description": "synthetic",
                    }
                ],
                "confidence": 1.0,
                "recommended_resolution": "synthetic",
            }
        ],
        "overall_confidence": 1.0,
        "notes": "dry-run",
    }


# ---------------------------------------------------------------------------
# Live client.
# ---------------------------------------------------------------------------


def _build_live_client() -> MiniMaxClient:
    settings = get_settings()
    if not settings.gmi_api_key:
        raise RuntimeError(
            "GMI_API_KEY not set. Use --dry to run without M3 calls."
        )
    return MiniMaxClient(settings=settings)


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=4, help="Runs per fixture (default 4).")
    parser.add_argument(
        "--dry",
        action="store_true",
        help="Skip M3 calls; synthesize responses from the expected labels. Useful for CI.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("/tmp/phase4_eval"),
        help="Where to write the eval artifact (default /tmp/phase4_eval).",
    )
    args = parser.parse_args()

    if sys.platform != "darwin" or not shutil.which("say"):
        print(
            "ERROR: Phase 4 eval requires macOS `say` (fixtures use TTS audio).",
            file=sys.stderr,
        )
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    settings.derived_dir = args.out_dir / "derived"
    settings.upload_dir = args.out_dir / "uploads"

    # ------------------------------------------------------------------
    # Build fixtures (one-time, deterministic).
    # ------------------------------------------------------------------
    fx_dir = args.out_dir / "fixtures"
    print(f"Building {len(SCRIPTS)} fixtures under {fx_dir}…")
    paths: dict[str, tuple[Path, Path, Path]] = {}
    for script in SCRIPTS:
        clear_model_cache()
        # Per-fixture derived dir to keep ASR caches from clobbering each other.
        d = args.out_dir / "derived" / script.name
        d.mkdir(parents=True, exist_ok=True)
        settings.derived_dir = d
        paths[script.name] = build_fixture(script, fx_dir)

    # ------------------------------------------------------------------
    # Build the client (live or dry).
    # ------------------------------------------------------------------
    if args.dry:
        client: MiniMaxClient | _DryRunClient = _DryRunClient()
        print("DRY-RUN: using synthetic M3 responses (no network calls).")
    else:
        client = _build_live_client()
        print(f"Live M3 eval; model={client.model} prompt_version={PROMPT_VERSION}")
    print(f"Runs per fixture: {args.runs}\n")

    # ------------------------------------------------------------------
    # Run every fixture N times.
    # ------------------------------------------------------------------
    reports: list[FixtureReport] = []
    expected_by_name = {e.name: e for e in EXPECTED}

    for fixture_name, (base_path, a_path, b_path) in paths.items():
        expected = expected_by_name[fixture_name]
        fixture_report = FixtureReport(
            name=fixture_name,
            expected=asdict(expected),
        )
        # Tell the dry-run mock which label to return for this
        # fixture's runs.
        if args.dry:
            client.set_next_expected(expected)  # type: ignore[attr-defined]
        for run_idx in range(1, args.runs + 1):
            # Per-run derived dir so the pipeline doesn't reuse
            # cached ASR across runs (we want independent ASR
            # behaviour — actually it IS deterministic given the
            # audio, so this just keeps the test dirs separate).
            d = args.out_dir / "derived" / fixture_name / f"run{run_idx}"
            d.mkdir(parents=True, exist_ok=True)
            settings.derived_dir = d
            clear_model_cache()
            t0 = time.monotonic()
            try:
                base = process_video(base_path)
                a = process_video(a_path)
                b = process_video(b_path)
                artifacts = analyze_merge(
                    base=base,
                    branch_a=a,
                    branch_b=b,
                    client=client,  # type: ignore[arg-type]
                )
                verdict = FixtureVerdict(
                    fixture=fixture_name,
                    run_index=run_idx,
                    branch_a_impact=artifacts.analysis.branch_a_impact.impact_level.value,
                    branch_b_impact=artifacts.analysis.branch_b_impact.impact_level.value,
                    combined_impact=artifacts.analysis.combined_impact.value,
                    interaction=artifacts.analysis.interactions[0].interaction_type.value,
                    confidence=artifacts.analysis.overall_confidence,
                    retries=artifacts.retries,
                    raw_response=artifacts.raw_response,
                    matches_expected=False,
                    matches_expected_per_axis={},
                )
                _score_run(verdict, expected)
                verdict.notes = (
                    f"latency={time.monotonic() - t0:.1f}s retries={artifacts.retries}"
                )
            except Exception as e:  # noqa: BLE001
                verdict = FixtureVerdict(
                    fixture=fixture_name,
                    run_index=run_idx,
                    branch_a_impact="error",
                    branch_b_impact="error",
                    combined_impact="error",
                    interaction="error",
                    confidence=0.0,
                    retries=0,
                    raw_response=repr(e),
                    matches_expected=False,
                    matches_expected_per_axis={},
                    notes=f"exception: {e!r}",
                )
            fixture_report.runs.append(verdict)
            print(
                f"  {fixture_name:32s} run {run_idx}/{args.runs}  "
                f"A={verdict.branch_a_impact:9s} B={verdict.branch_b_impact:9s} "
                f"C={verdict.combined_impact:9s} I={verdict.interaction:24s} "
                f"{'OK' if verdict.matches_expected else 'MISS'}"
            )
        # Modal verdict.
        fixture_report.n_runs = len(fixture_report.runs)
        fixture_report.majority_a = _modal([v.branch_a_impact for v in fixture_report.runs])
        fixture_report.majority_b = _modal([v.branch_b_impact for v in fixture_report.runs])
        fixture_report.majority_combined = _modal(
            [v.combined_impact for v in fixture_report.runs]
        )
        fixture_report.majority_interaction = _modal(
            [v.interaction for v in fixture_report.runs]
        )
        fixture_report.n_correct = sum(1 for v in fixture_report.runs if v.matches_expected)
        verdict_dist = Counter(_verdict_key(v) for v in fixture_report.runs)
        fixture_report.verdict_distribution = dict(verdict_dist)
        reports.append(fixture_report)
        print(
            f"  → modal: A={fixture_report.majority_a} B={fixture_report.majority_b} "
            f"C={fixture_report.majority_combined} I={fixture_report.majority_interaction} "
            f"({fixture_report.n_correct}/{fixture_report.n_runs} fully correct, "
            f"variance={len(verdict_dist)} distinct verdicts)\n"
        )

    # ------------------------------------------------------------------
    # Aggregate metrics.
    # ------------------------------------------------------------------
    n_fixtures = len(reports)
    correct_full = sum(1 for r in reports if r.n_correct == r.n_runs)
    correct_a = sum(
        1 for r in reports if r.majority_a == expected_by_name[r.name].branch_a_impact
    )
    correct_b = sum(
        1 for r in reports if r.majority_b == expected_by_name[r.name].branch_b_impact
    )
    correct_c = sum(
        1 for r in reports if r.majority_combined == expected_by_name[r.name].combined_impact
    )
    correct_i = sum(
        1 for r in reports if r.majority_interaction == expected_by_name[r.name].interaction
    )

    # Canonical case: 01 must be classified creates_new_conflict.
    canonical_01 = next(r for r in reports if r.name == "01_canonical_prereq_loss")
    canonical_correct = canonical_01.majority_interaction == "creates_new_conflict"

    # Safe controls: 04 + 05 must NOT be classified creates_new_conflict.
    safe_04 = next(r for r in reports if r.name == "04_safe_unrelated")
    safe_05 = next(r for r in reports if r.name == "05_safe_independent")
    safe_no_new_conflict = (
        safe_04.majority_interaction != "creates_new_conflict"
        and safe_05.majority_interaction != "creates_new_conflict"
    )

    # Save raw artifact.
    raw = {
        "prompt_version": PROMPT_VERSION,
        "n_runs_per_fixture": args.runs,
        "dry_run": args.dry,
        "model": client.model,
        "fixtures": [
            {
                "name": r.name,
                "expected": r.expected,
                "modal_verdict": {
                    "branch_a_impact": r.majority_a,
                    "branch_b_impact": r.majority_b,
                    "combined_impact": r.majority_combined,
                    "interaction": r.majority_interaction,
                },
                "n_correct": r.n_correct,
                "n_runs": r.n_runs,
                "verdict_distribution": r.verdict_distribution,
                "runs": [asdict(v) for v in r.runs],
            }
            for r in reports
        ],
    }
    (args.out_dir / "phase4_eval.json").write_text(json.dumps(raw, indent=2))

    # ------------------------------------------------------------------
    # Final report (mirrors the user's 14 metrics).
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("PHASE 4 EVAL SUMMARY")
    print("=" * 60)
    print(f"Fixtures:                   {n_fixtures}")
    print(f"Runs per fixture:           {args.runs}")
    print(f"Model:                      {client.model}")
    print(f"Prompt version:             {PROMPT_VERSION}")
    print(f"Dry run:                    {args.dry}")
    print()
    print("Per-axis modal accuracy (across fixtures):")
    print(f"  branch_a_impact:          {correct_a}/{n_fixtures}")
    print(f"  branch_b_impact:          {correct_b}/{n_fixtures}")
    print(f"  combined_impact:          {correct_c}/{n_fixtures}")
    print(f"  interaction:              {correct_i}/{n_fixtures}")
    print(f"  all four correct:         {correct_full}/{n_fixtures}")
    print()
    print(f"Canonical 01 creates_new_conflict?  {canonical_correct}")
    print(f"Safe controls 04/05 NOT creates_new_conflict?  {safe_no_new_conflict}")
    print()
    # User gate.
    overall = (
        correct_i >= max(1, int(0.75 * n_fixtures))  # ≥ 75% interaction accuracy
        and canonical_correct
        and safe_no_new_conflict
    )
    print("User gate:")
    print(f"  canonical conflict cases ≥ 3/4:    {'?'}  (canonical_01 = {canonical_correct})")
    print(f"  safe / no-new-conflict ≥ 3/4:     {'?'}  (04={safe_04.majority_interaction}, 05={safe_05.majority_interaction})")
    print(f"  overall interaction accuracy ≥ 75%:  {correct_i}/{n_fixtures} ({100*correct_i/n_fixtures:.0f}%)")
    print(f"  no systematic FP behavior:           {safe_no_new_conflict}")
    print(f"  canonical demo fixture correct:     {canonical_correct}")
    print(f"\nOVERALL: {'GO' if overall else 'INVESTIGATE'}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
