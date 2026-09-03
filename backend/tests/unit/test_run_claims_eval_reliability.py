"""Phase 4.5 — reliability + accounting + forensic-artifact tests.

These tests cover the user-stated contracts for
``scripts/run_claims_eval.py`` after the Phase 4.5 refactor:

  - **Provider failures do not consume successful-run slots.**
    A transient (provider) failure is recorded as a failed
    attempt and the driver re-attempts the whole
    ``analyze_claims()`` call. The successful run count is the
    only thing that counts toward `--runs`.

  - **Exact successful run count per fixture.**
    The driver stops after exactly `--runs` SUCCESSFUL runs
    OR after the documented `--max-failed-attempts` cap,
    whichever comes first. The eval is finite.

  - **Schema / semantic failures are NOT retried.**
    Deterministic failures (Pydantic validation, missing
    fields, orchestrator invariant) are recorded as failed
    attempts but the driver does NOT re-attempt them — the
    same prompt / schema / orchestrator invariant would fail
    again. A schema failure IS counted toward the
    `--max-failed-attempts` cap.

  - **`'error'` is excluded from semantic verdict
    distributions and from modal / variance.**
    A failed attempt is an accounting event, not a verdict.
    The per-fixture modal verdict is computed over the
    successful verdicts only.

  - **Stats delta reporting.** The harness snapshots
    ``MiniMaxClient.stats`` before / after each fixture and
    before / after the whole eval. The per-fixture report
    shows the deltas (successful calls, retries, final
    provider failures, HTTP 429, upstream 503) consumed by
    that fixture; the global rollup shows the totals.

  - **Full forensic serialization.** For every successful run
    the harness writes a JSON payload containing every
    ``BaseClaim`` (id/meaning/type/importance/evidence_regions
    /equivalents), every branch's per-claim ``ClaimSurvival``
    (status/surviving_evidence/rationale), every
    ``ClaimInteraction`` (branch statuses, combined status,
    deterministic derivation_reason, M3 explanation, M3
    recommended resolution), overall interaction/impact/
    confidence, call counts/timing/retries.

  - **Focused inspection file for fixtures 02 / 06 / 08.**
    The user-named fixtures get a compact
    ``<fixture>_focused.json`` artifact alongside the full
    ``<fixture>_forensic.json``.

The tests stub the orchestrator's ``analyze_claims()`` and the
``MiniMaxClient.stats`` shape so they need no FFmpeg, no
faster-whisper, and no macOS `say`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure the scripts/ directory is importable so the harness module
# can be imported without going through the project Makefile.
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Imports below intentionally come AFTER the sys.path tweak so
# `import run_claims_eval` / `import run_claims_eval_reliability`
# resolve regardless of test cwd.
import run_claims_eval as harness  # noqa: E402
import run_claims_eval_reliability as reliability  # noqa: E402

from app.models.claims import (  # noqa: E402
    BaseClaim,
    BranchClaims,
    ClaimCentricAnalysis,
    ClaimEvidenceRegion,
    ClaimImportance,
    ClaimInteraction,
    ClaimStatus,
    ClaimSurvival,
    ClaimType,
    CrossEditInteraction,
)
from app.services.minimax._retry import RetryStats  # noqa: E402
from app.services.minimax.client import MiniMaxError  # noqa: E402
from app.services.semantic.claims.orchestrate import (  # noqa: E402
    RepresentationSnapshot,
)
from app.services.semantic.claims.represent import (  # noqa: E402
    EditMetadata,
    ReconstructedActualContent,
)

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _make_base_claim(
    *,
    claim_id: str = "C1",
    meaning: str = "The device must be unplugged before the cover is opened.",
    claim_type: ClaimType = ClaimType.PREREQUISITE,
    importance: ClaimImportance = ClaimImportance.CRITICAL,
) -> BaseClaim:
    return BaseClaim(
        claim_id=claim_id,
        meaning=meaning,
        claim_type=claim_type,
        importance=importance,
        evidence_regions=[
            ClaimEvidenceRegion(
                start=0.0,
                end=3.0,
                description="shot 0 prerequisite",
            )
        ],
        equivalents=[
            ClaimEvidenceRegion(
                start=3.0,
                end=6.0,
                description="shot 1 follow-up references it",
            )
        ],
    )


def _empty_representation_snapshot() -> RepresentationSnapshot:
    """Empty ``RepresentationSnapshot`` for the test stubs.

    Tests that exercise the per-fixture driver / forensic
    serializer only assert that the artifacts expose a
    ``representation`` attribute; the per-fixture
    actual-content tests live in ``test_phase45_forensic.py``.
    """
    empty = ReconstructedActualContent(
        branch="base",
        lines=[],
        edit_metadata=EditMetadata(),
    )
    return RepresentationSnapshot(
        base=empty,
        branch_a=empty,
        branch_b=empty,
        combined=empty,
    )


def _make_artifacts(
    *,
    overall: CrossEditInteraction,
    branch_a_status: ClaimStatus = ClaimStatus.PRESERVED,
    branch_b_status: ClaimStatus = ClaimStatus.PRESERVED,
    combined_status: ClaimStatus = ClaimStatus.BROKEN,
    n_extraction_calls: int = 1,
    n_evaluation_calls: int = 3,
    n_explanation_calls: int = 1,
    extraction_retries: int = 0,
    evaluation_retries: int = 0,
    confidence: float = 0.9,
    explanation: str | None = "M3 prose explanation",
    recommended_resolution: str | None = "Restore the prerequisite in BASE.",
) -> Any:
    """Build a minimal `ClaimAnalysisArtifacts` for forensic testing."""
    base_claim = _make_base_claim()
    branch_a_survivals = [
        ClaimSurvival(
            claim_id="C1",
            branch="branch_a",
            status=branch_a_status,
            surviving_evidence=[ClaimEvidenceRegion(start=3.0, end=6.0, description="follow-up")]
            if branch_a_status is not ClaimStatus.BROKEN
            else [],
            rationale=f"branch_a: {branch_a_status.value}",
        )
    ]
    branch_b_survivals = [
        ClaimSurvival(
            claim_id="C1",
            branch="branch_b",
            status=branch_b_status,
            surviving_evidence=[ClaimEvidenceRegion(start=0.0, end=3.0, description="shot 0")]
            if branch_b_status is not ClaimStatus.BROKEN
            else [],
            rationale=f"branch_b: {branch_b_status.value}",
        )
    ]
    combined_survivals = [
        ClaimSurvival(
            claim_id="C1",
            branch="combined",
            status=combined_status,
            surviving_evidence=[]
            if combined_status is ClaimStatus.BROKEN
            else [
                ClaimEvidenceRegion(start=0.0, end=3.0, description="shot 0"),
            ],
            rationale=f"combined: {combined_status.value}",
        )
    ]
    interactions = [
        ClaimInteraction(
            claim_id="C1",
            claim_meaning=base_claim.meaning,
            claim_type=base_claim.claim_type,
            claim_importance=base_claim.importance,
            branch_a_status=branch_a_status,
            branch_b_status=branch_b_status,
            combined_status=combined_status,
            interaction=overall,
            derivation_reason="R1: A=preserved, B=preserved, combined=broken → creates_new_conflict",
            m3_explanation=explanation,
            m3_recommended_resolution=recommended_resolution,
        )
    ]
    analysis = ClaimCentricAnalysis(
        base_claims=[base_claim],
        branch_a_claims=BranchClaims(branch="branch_a", claim_survivals=branch_a_survivals),
        branch_b_claims=BranchClaims(branch="branch_b", claim_survivals=branch_b_survivals),
        combined_claims=BranchClaims(branch="combined", claim_survivals=combined_survivals),
        interactions=interactions,
        overall_interaction=overall,
        overall_impact=combined_status,
        overall_confidence=confidence,
        notes="forensic test fixture",
    )

    class _Artifacts:
        """Stand-in for `ClaimAnalysisArtifacts` exposing the fields
        the forensic serializer reads."""

        def __init__(self) -> None:
            self.analysis = analysis
            self.base_claims = [base_claim]
            self.branch_a_claims_reconstructed = branch_a_survivals
            self.branch_b_claims_reconstructed = branch_b_survivals
            self.combined_claims_reconstructed = combined_survivals
            self.n_extraction_calls = n_extraction_calls
            self.n_evaluation_calls = n_evaluation_calls
            self.n_explanation_calls = n_explanation_calls
            self.extraction_retries = extraction_retries
            self.evaluation_retries = evaluation_retries
            self.extraction_prompt_version = "v4.1.0"
            self.evaluation_prompt_version = "v4.1.0"
            self.model = "TEST-MODEL"
            self.notes = ["test"]
            self.representation = _empty_representation_snapshot()

    return _Artifacts()


# ---------------------------------------------------------------------------
# Failure classification.
# ---------------------------------------------------------------------------


def test_classify_provider_failure_from_503_message() -> None:
    err = MiniMaxError("GMI Cloud 503: upstream_503: temporarily unavailable")
    assert reliability.classify_failure(err) is reliability.FailureCategory.PROVIDER


def test_classify_provider_failure_from_429_message() -> None:
    err = MiniMaxError("GMI Cloud 429: rate_limit_exceeded")
    assert reliability.classify_failure(err) is reliability.FailureCategory.PROVIDER


def test_classify_provider_failure_from_transport_message() -> None:
    err = MiniMaxError("HTTP error talking to GMI Cloud: connection reset")
    assert reliability.classify_failure(err) is reliability.FailureCategory.PROVIDER


def test_classify_schema_failure_from_validation_message() -> None:
    err = MiniMaxError(
        "M3 failed to return a valid claim list after repair: ValidationError: extras_forbidden"
    )
    assert reliability.classify_failure(err) is reliability.FailureCategory.SCHEMA


def test_classify_schema_failure_from_missing_claims_key() -> None:
    err = MiniMaxError("missing 'claims' list in extraction response: []")
    assert reliability.classify_failure(err) is reliability.FailureCategory.SCHEMA


def test_classify_orchestrator_failure_from_runtime_error() -> None:
    err = RuntimeError("alignment produced no matches for fixture X")
    assert reliability.classify_failure(err) is reliability.FailureCategory.ORCHESTRATOR


def test_classify_unknown_failure_from_plain_value_error() -> None:
    # ValueError is the catch-all in the classifier.
    err = ValueError("unexpected shape: 123")
    assert reliability.classify_failure(err) is reliability.FailureCategory.ORCHESTRATOR


# ---------------------------------------------------------------------------
# Stats snapshot.
# ---------------------------------------------------------------------------


def test_stats_snapshot_delta_reports_correct_counters() -> None:
    stats = RetryStats()
    baseline = reliability.RetryStatsSnapshot.from_stats(stats)
    stats.record_success()
    stats.record_success()
    stats.record_retry(status_429=True, upstream_503=False)
    stats.record_retry(status_429=False, upstream_503=True)
    stats.record_failure()
    delta = reliability.RetryStatsSnapshot.from_stats(stats).delta(baseline)
    assert delta.successful_calls == 2
    assert delta.retries == 2
    assert delta.http_429_count == 1
    assert delta.upstream_503_count == 1
    assert delta.provider_failures == 1


def test_stats_snapshot_as_dict_round_trip() -> None:
    snap = reliability.RetryStatsSnapshot(
        successful_calls=5,
        retries=3,
        provider_failures=1,
        http_429_count=2,
        upstream_503_count=1,
    )
    payload = snap.as_dict()
    assert payload == {
        "successful_calls": 5,
        "retries": 3,
        "provider_failures": 1,
        "http_429_count": 2,
        "upstream_503_count": 1,
    }


# ---------------------------------------------------------------------------
# run_attempts_until_success — driver behavior.
# ---------------------------------------------------------------------------


def test_provider_failure_does_not_consume_success_slot() -> None:
    """A provider failure is retried; the next success is recorded as
    the 1st successful run (not the 3rd attempt)."""
    sleeps: list[float] = []
    stats = RetryStats()
    attempts: list[int] = []

    def fn() -> Any:
        attempts.append(len(attempts) + 1)
        # First two calls raise a provider failure; third succeeds.
        if len(attempts) <= 2:
            raise MiniMaxError("GMI Cloud 503: temporarily unavailable")
        return _make_artifacts(overall=CrossEditInteraction.NONE)

    successes, failed, delta = reliability.run_attempts_until_success(
        fn=fn,
        stats=stats,
        fixture_name="test_fixture",
        target_successful_runs=1,
        max_failed_attempts=12,
        sleeper=sleeps.append,
    )
    assert len(successes) == 1
    assert len(failed) == 2
    assert [fa.category for fa in failed] == ["provider", "provider"]
    assert [fa.attempt_index for fa in failed] == [1, 2]
    assert successes[0].result.analysis.overall_interaction is CrossEditInteraction.NONE
    assert delta.provider_failures == 0  # provider retries didn't escalate to a failure


def test_schema_failure_is_recorded_but_not_retried() -> None:
    """A schema failure is counted in the failed-attempts list and the
    driver stops on the deterministic failure (no re-attempt)."""
    stats = RetryStats()
    attempts: list[int] = []

    def fn() -> Any:
        attempts.append(len(attempts) + 1)
        raise MiniMaxError("M3 failed to return a valid claim list: extras_forbidden")

    successes, failed, delta = reliability.run_attempts_until_success(
        fn=fn,
        stats=stats,
        fixture_name="test_fixture",
        target_successful_runs=4,
        max_failed_attempts=12,
    )
    # Driver stops after 1 attempt because schema failures are not retried.
    assert len(attempts) == 1
    assert len(successes) == 0
    assert len(failed) == 1
    assert failed[0].category == "schema"


def test_exact_successful_count_is_collected() -> None:
    """The driver stops after exactly the target number of successful runs."""
    sleeps: list[float] = []
    stats = RetryStats()
    call_count = {"n": 0}

    def fn() -> Any:
        call_count["n"] += 1
        return _make_artifacts(overall=CrossEditInteraction.NONE)

    successes, failed, _delta = reliability.run_attempts_until_success(
        fn=fn,
        stats=stats,
        fixture_name="test_fixture",
        target_successful_runs=3,
        max_failed_attempts=12,
        sleeper=sleeps.append,
    )
    assert len(successes) == 3
    assert len(failed) == 0
    assert call_count["n"] == 3


def test_finite_cap_stops_provider_retry_loop() -> None:
    """When the cap is reached the driver stops with the successful
    runs it has so far."""
    sleeps: list[float] = []
    stats = RetryStats()
    attempts = {"n": 0}

    def fn() -> Any:
        attempts["n"] += 1
        # Always fails with a provider error. The driver should
        # stop after max_failed_attempts.
        raise MiniMaxError("GMI Cloud 503: upstream_503")

    successes, failed, delta = reliability.run_attempts_until_success(
        fn=fn,
        stats=stats,
        fixture_name="test_fixture",
        target_successful_runs=4,
        max_failed_attempts=3,
        sleeper=sleeps.append,
    )
    assert len(successes) == 0
    assert len(failed) == 3
    assert attempts["n"] == 3
    # The driver slept between successive provider-failure
    # re-attempts: 2 sleeps between 3 failed attempts (no sleep
    # before the very first attempt, no sleep after the last one
    # because the cap check breaks out before sleep).
    assert sleeps == [1.0, 1.0]


def test_stats_delta_reflects_per_fixture_window() -> None:
    """The driver's stats delta is independent of prior runs.

    Simulate two fixtures sharing a single RetryStats object; the
    delta for each run_attempts_until_success call reports the
    counts consumed by that call only.
    """
    sleeps: list[float] = []
    stats = RetryStats()
    # Baseline for fixture 1.
    baseline1 = reliability.RetryStatsSnapshot.from_stats(stats)

    def fn_ok() -> Any:
        stats.record_success()
        stats.record_retry(status_429=True, upstream_503=False)
        return _make_artifacts(overall=CrossEditInteraction.NONE)

    successes1, _, delta1 = reliability.run_attempts_until_success(
        fn=fn_ok,
        stats=stats,
        fixture_name="fixture_1",
        target_successful_runs=2,
        max_failed_attempts=12,
        sleeper=sleeps.append,
    )
    assert len(successes1) == 2
    expected1 = reliability.RetryStatsSnapshot.from_stats(stats).delta(baseline1)
    assert delta1.successful_calls == expected1.successful_calls == 2
    assert delta1.retries == 2
    assert delta1.http_429_count == 2

    # Fixture 2 with a different baseline.
    baseline2 = reliability.RetryStatsSnapshot.from_stats(stats)

    def fn_with_failure() -> Any:
        stats.record_success()
        if len(successes1) == 0:  # never true on this call; placeholder
            pass
        raise MiniMaxError("GMI Cloud 503: upstream_503")

    successes2, failed2, delta2 = reliability.run_attempts_until_success(
        fn=fn_with_failure,
        stats=stats,
        fixture_name="fixture_2",
        target_successful_runs=1,
        max_failed_attempts=2,
        sleeper=sleeps.append,
    )
    # Fixture 2's first attempt fails with provider, then driver
    # gives up (it doesn't retry because target_successful_runs=1
    # is not yet met and max_failed_attempts hit).
    # Actually the driver DOES retry provider failures until either
    # the cap or the target is met; with max_failed_attempts=2 the
    # second failure triggers the cap-reached break.
    assert len(failed2) == 2
    assert (
        delta2.successful_calls
        == reliability.RetryStatsSnapshot.from_stats(stats).delta(baseline2).successful_calls
    )


# ---------------------------------------------------------------------------
# Forensic serialization.
# ---------------------------------------------------------------------------


def test_serialize_claim_forensic_includes_every_field() -> None:
    arts = _make_artifacts(overall=CrossEditInteraction.CREATES_NEW_CONFLICT)
    payload = reliability.serialize_claim_forensic(arts)
    # BASE claim — full forensic detail (id, meaning, type,
    # importance, evidence_regions, equivalents).
    assert len(payload["base_claims"]) == 1
    bc = payload["base_claims"][0]
    assert bc["claim_id"] == "C1"
    assert "unplugged" in bc["meaning"]
    assert bc["claim_type"] == ClaimType.PREREQUISITE.value
    assert bc["importance"] == ClaimImportance.CRITICAL.value
    assert len(bc["evidence_regions"]) == 1
    assert bc["evidence_regions"][0]["start"] == 0.0
    assert len(bc["equivalents"]) == 1
    # Branch claim survivals — full detail.
    for branch_name in ("branch_a", "branch_b", "combined"):
        survivals = payload[f"{branch_name}_claim_survivals"]
        assert len(survivals) == 1
        cs = survivals[0]
        assert cs["claim_id"] == "C1"
        assert cs["branch"] == branch_name
        assert "status" in cs
        assert "surviving_evidence" in cs
        assert "rationale" in cs
    # Interactions — derivation_reason, M3 explanation, M3 recommended_resolution.
    assert len(payload["interactions"]) == 1
    ci = payload["interactions"][0]
    assert ci["claim_id"] == "C1"
    assert ci["branch_a_status"] == ClaimStatus.PRESERVED.value
    assert ci["branch_b_status"] == ClaimStatus.PRESERVED.value
    assert ci["combined_status"] == ClaimStatus.BROKEN.value
    assert ci["interaction"] == CrossEditInteraction.CREATES_NEW_CONFLICT.value
    assert "R1:" in ci["derivation_reason"]
    assert ci["m3_explanation"] == "M3 prose explanation"
    assert ci["m3_recommended_resolution"] == "Restore the prerequisite in BASE."
    # Overall rollups.
    assert payload["overall_interaction"] == CrossEditInteraction.CREATES_NEW_CONFLICT.value
    assert payload["overall_impact"] == ClaimStatus.BROKEN.value
    assert payload["overall_confidence"] == 0.9
    # Call counts / timing / retries.
    cc = payload["call_counts"]
    assert cc["extraction"] == 1
    assert cc["evaluation"] == 3
    assert cc["explanation"] == 1
    assert cc["extraction_retries"] == 0
    assert cc["evaluation_retries"] == 0
    assert payload["model"] == "TEST-MODEL"
    assert payload["extraction_prompt_version"] == "v4.1.0"
    assert payload["evaluation_prompt_version"] == "v4.1.0"


def test_write_forensic_report_writes_full_and_focused_files(tmp_path: Path) -> None:
    arts = _make_artifacts(overall=CrossEditInteraction.CREATES_NEW_CONFLICT)
    forensics = [reliability.serialize_claim_forensic(arts) for _ in range(2)]
    failed_attempts: list[reliability.FailedAttempt] = [
        reliability.FailedAttempt(attempt_index=3, error="repr-fail", category="provider"),
    ]
    stats_delta = reliability.RetryStatsSnapshot(
        successful_calls=10,
        retries=2,
        provider_failures=1,
        http_429_count=1,
        upstream_503_count=0,
    )
    out_path = reliability.write_forensic_report(
        out_dir=tmp_path,
        fixture_name="02_qualifier_loss",
        n_successful_runs=2,
        forensics=forensics,
        failed_attempts=failed_attempts,
        stats_delta=stats_delta,
    )
    # Full forensic artifact.
    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert payload["fixture"] == "02_qualifier_loss"
    assert payload["n_successful_runs"] == 2
    assert payload["n_failed_attempts"] == 1
    assert payload["stats_delta"] == stats_delta.as_dict()
    assert len(payload["runs"]) == 2
    assert payload["failed_attempts"][0]["attempt_index"] == 3
    assert payload["failed_attempts"][0]["category"] == "provider"
    # Focused inspection file — 02 is in the user-named set.
    focused_path = tmp_path / "02_qualifier_loss_focused.json"
    assert focused_path.exists()
    focused = json.loads(focused_path.read_text())
    assert focused["fixture"] == "02_qualifier_loss"
    assert len(focused["base_claims"]) == 1
    assert focused["base_claims"][0]["n_evidence_regions"] == 1
    assert focused["base_claims"][0]["n_equivalents"] == 1
    assert len(focused["interactions"]) == 1
    assert "R1:" in focused["interactions"][0]["derivation_reason"]
    assert focused["overall_interaction"] == CrossEditInteraction.CREATES_NEW_CONFLICT.value


@pytest.mark.parametrize(
    "fixture_name",
    ["02_qualifier_loss", "06_one_branch_broken", "08_hard_negative_related"],
)
def test_focused_inspection_for_user_named_fixtures(tmp_path: Path, fixture_name: str) -> None:
    """Each of the user-named fixtures gets a focused inspection file."""
    arts = _make_artifacts(
        overall=CrossEditInteraction.NONE
        if fixture_name != "06_one_branch_broken"
        else CrossEditInteraction.AMPLIFIES_EXISTING_ISSUE,
    )
    forensics = [reliability.serialize_claim_forensic(arts)]
    stats_delta = reliability.RetryStatsSnapshot()
    reliability.write_forensic_report(
        out_dir=tmp_path,
        fixture_name=fixture_name,
        n_successful_runs=1,
        forensics=forensics,
        failed_attempts=[],
        stats_delta=stats_delta,
    )
    focused_path = tmp_path / f"{fixture_name}_focused.json"
    assert focused_path.exists(), f"focused file missing for {fixture_name}"
    focused = json.loads(focused_path.read_text())
    # All user-named fields present in the compact view.
    ci = focused["interactions"][0]
    for field in (
        "claim_id",
        "claim_meaning",
        "claim_type",
        "claim_importance",
        "branch_a_status",
        "branch_b_status",
        "combined_status",
        "interaction",
        "derivation_reason",
        "m3_explanation",
        "m3_recommended_resolution",
    ):
        assert field in ci, f"missing {field} in focused inspection for {fixture_name}"


def test_non_focused_fixture_has_no_focused_file(tmp_path: Path) -> None:
    """Fixtures outside the user-named set don't get a focused file."""
    arts = _make_artifacts(overall=CrossEditInteraction.NONE)
    forensics = [reliability.serialize_claim_forensic(arts)]
    reliability.write_forensic_report(
        out_dir=tmp_path,
        fixture_name="01_canonical_prereq_loss",
        n_successful_runs=1,
        forensics=forensics,
        failed_attempts=[],
        stats_delta=reliability.RetryStatsSnapshot(),
    )
    assert (tmp_path / "01_canonical_prereq_loss_focused.json").exists() is False


# ---------------------------------------------------------------------------
# Per-fixture driver integration (errors excluded from modal/variance).
# ---------------------------------------------------------------------------


class _StubClient:
    """A minimal stand-in for MiniMaxClient + stats snapshotting."""

    def __init__(self) -> None:
        self.stats = RetryStats()
        self.model = "STUB"
        self.calls: list[int] = []


def _stub_video_rep() -> Any:
    """A minimal stand-in for VideoRepresentation."""

    class _Rep:
        shots: list = []

    return _Rep()


def test_fixture_driver_excludes_errors_from_modal_and_variance() -> None:
    """Failed attempts do NOT appear in verdict_distribution, modal,
    or variance. The driver appends only SUCCESSFUL verdicts to
    report.runs and computes the modal from those.

    The test scenario interleaves transient provider failures
    with successful runs (schema/semantic failures are tested
    in a separate test, since by spec the driver STOPS on a
    non-provider failure)."""

    class _FakeArtifacts:
        """Stand-in for `ClaimAnalysisArtifacts` exposing the fields
        the per-fixture driver reads."""

        def __init__(self, overall: CrossEditInteraction) -> None:
            self.analysis = _make_analysis(overall)
            self.base_claims: list[Any] = []
            self.n_extraction_calls: int = 1
            self.n_evaluation_calls: int = 3
            self.n_explanation_calls: int = 1
            self.extraction_retries: int = 0
            self.evaluation_retries: int = 0
            self.extraction_prompt_version: str = "v4.1.0"
            self.evaluation_prompt_version: str = "v4.1.0"
            self.model: str = "STUB"
            self.representation = _empty_representation_snapshot()

    def _make_analysis(overall: CrossEditInteraction) -> ClaimCentricAnalysis:
        return ClaimCentricAnalysis(
            base_claims=[_make_base_claim()],
            branch_a_claims=BranchClaims(
                branch="branch_a",
                claim_survivals=[
                    ClaimSurvival(
                        claim_id="C1",
                        branch="branch_a",
                        status=ClaimStatus.PRESERVED,
                        surviving_evidence=[],
                        rationale="ok",
                    )
                ],
            ),
            branch_b_claims=BranchClaims(
                branch="branch_b",
                claim_survivals=[
                    ClaimSurvival(
                        claim_id="C1",
                        branch="branch_b",
                        status=ClaimStatus.PRESERVED,
                        surviving_evidence=[],
                        rationale="ok",
                    )
                ],
            ),
            combined_claims=BranchClaims(
                branch="combined",
                claim_survivals=[
                    ClaimSurvival(
                        claim_id="C1",
                        branch="combined",
                        status=ClaimStatus.BROKEN,
                        surviving_evidence=[],
                        rationale="ok",
                    )
                ],
            ),
            interactions=[],
            overall_interaction=overall,
            overall_impact=ClaimStatus.BROKEN,
            overall_confidence=0.9,
        )

    client = _StubClient()
    call_count = {"n": 0}

    def fn() -> Any:
        call_count["n"] += 1
        # First call: provider failure; the driver retries.
        if call_count["n"] == 1:
            raise MiniMaxError("GMI Cloud 503: upstream_503")
        # Subsequent calls succeed; the verdict cycles through
        # distinct values so variance > 0.
        verdict_cycle = [
            CrossEditInteraction.NONE,
            CrossEditInteraction.NONE,
            CrossEditInteraction.CREATES_NEW_CONFLICT,
        ]
        return _FakeArtifacts(verdict_cycle[(call_count["n"] - 2) % 3])

    report, forensics = harness._run_fixture_with_reliability(
        fixture_name="test_fixture",
        expected_interaction=CrossEditInteraction.CREATES_NEW_CONFLICT.value,
        client=client,  # type: ignore[arg-type]
        base_rep=_stub_video_rep(),  # type: ignore[arg-type]
        a_rep=_stub_video_rep(),  # type: ignore[arg-type]
        b_rep=_stub_video_rep(),  # type: ignore[arg-type]
        n_target_runs=2,
        max_failed_attempts=12,
        sleeper=lambda _s: None,
        attempt_fn=fn,
    )

    # Exactly 2 successful runs.
    assert report.n_runs == 2
    assert len(forensics) == 2
    # 1 failed attempt (the initial provider failure; the driver
    # then retried into success).
    assert len(report.failed_attempts) == 1
    assert report.failed_attempts[0].category == "provider"
    # The verdict distribution must NOT contain 'error'.
    assert "error" not in report.verdict_distribution
    assert all(
        v
        in {
            CrossEditInteraction.NONE.value,
            CrossEditInteraction.CREATES_NEW_CONFLICT.value,
        }
        for v in report.verdict_distribution
    )
    # Modal is computed from successful verdicts only.
    assert report.modal_interaction == CrossEditInteraction.NONE.value
    # Variance is the count of distinct successful verdicts.
    assert report.variance == len(report.verdict_distribution)
    # Stats delta is non-empty (the provider attempt at least
    # incremented the snapshot machinery via the classify_failure
    # path; the assertion is that the snapshot machinery does
    # not blow up).
    assert isinstance(report.stats_delta, reliability.RetryStatsSnapshot)


def test_fixture_driver_stops_at_max_failed_attempts() -> None:
    """The driver stops collecting successful runs once the
    per-fixture max-failed-attempts cap is hit."""

    class _FakeArtifacts:
        def __init__(self) -> None:
            self.analysis = _make_analysis_ok()
            self.base_claims: list[Any] = []
            self.n_extraction_calls: int = 1
            self.n_evaluation_calls: int = 3
            self.n_explanation_calls: int = 1
            self.extraction_retries: int = 0
            self.evaluation_retries: int = 0
            self.extraction_prompt_version: str = "v4.1.0"
            self.evaluation_prompt_version: str = "v4.1.0"
            self.model: str = "STUB"
            self.representation = _empty_representation_snapshot()

    def _make_analysis_ok() -> ClaimCentricAnalysis:
        return ClaimCentricAnalysis(
            base_claims=[],
            branch_a_claims=BranchClaims(branch="branch_a", claim_survivals=[]),
            branch_b_claims=BranchClaims(branch="branch_b", claim_survivals=[]),
            combined_claims=BranchClaims(branch="combined", claim_survivals=[]),
            interactions=[],
            overall_interaction=CrossEditInteraction.NONE,
            overall_impact=ClaimStatus.PRESERVED,
            overall_confidence=0.9,
        )

    client = _StubClient()

    def fn() -> Any:
        # Always fails with a provider error so the cap is reached.
        raise MiniMaxError("GMI Cloud 503: upstream_503")

    report, forensics = harness._run_fixture_with_reliability(
        fixture_name="test_fixture",
        expected_interaction=CrossEditInteraction.NONE.value,
        client=client,  # type: ignore[arg-type]
        base_rep=_stub_video_rep(),  # type: ignore[arg-type]
        a_rep=_stub_video_rep(),  # type: ignore[arg-type]
        b_rep=_stub_video_rep(),  # type: ignore[arg-type]
        n_target_runs=4,
        max_failed_attempts=2,
        sleeper=lambda _s: None,
        attempt_fn=fn,
    )
    assert report.n_runs == 0
    assert len(report.failed_attempts) == 2
    assert forensics == []
    # The cap was reached before the target; the run window
    # closed early.
    assert all(fa.category == "provider" for fa in report.failed_attempts)


def test_fixture_driver_records_stats_delta_per_fixture() -> None:
    """The driver returns a `RetryStatsSnapshot` describing the
    RetryStats counters consumed by this fixture's run window."""

    class _FakeArtifacts:
        def __init__(self) -> None:
            self.analysis = ClaimCentricAnalysis(
                base_claims=[],
                branch_a_claims=BranchClaims(branch="branch_a", claim_survivals=[]),
                branch_b_claims=BranchClaims(branch="branch_b", claim_survivals=[]),
                combined_claims=BranchClaims(branch="combined", claim_survivals=[]),
                interactions=[],
                overall_interaction=CrossEditInteraction.NONE,
                overall_impact=ClaimStatus.PRESERVED,
                overall_confidence=0.9,
            )
            self.base_claims: list[Any] = []
            self.n_extraction_calls: int = 1
            self.n_evaluation_calls: int = 3
            self.n_explanation_calls: int = 1
            self.extraction_retries: int = 0
            self.evaluation_retries: int = 0
            self.extraction_prompt_version: str = "v4.1.0"
            self.evaluation_prompt_version: str = "v4.1.0"
            self.model: str = "STUB"
            self.representation = _empty_representation_snapshot()

    client = _StubClient()

    def fn() -> Any:
        return _FakeArtifacts()

    report, _forensics = harness._run_fixture_with_reliability(
        fixture_name="test_fixture",
        expected_interaction=CrossEditInteraction.NONE.value,
        client=client,  # type: ignore[arg-type]
        base_rep=_stub_video_rep(),  # type: ignore[arg-type]
        a_rep=_stub_video_rep(),  # type: ignore[arg-type]
        b_rep=_stub_video_rep(),  # type: ignore[arg-type]
        n_target_runs=1,
        max_failed_attempts=12,
        sleeper=lambda _s: None,
        attempt_fn=fn,
    )
    # Stats delta is exposed on the report (a Snapshot, not the live
    # RetryStats).
    assert isinstance(report.stats_delta, reliability.RetryStatsSnapshot)
    assert report.stats_delta.successful_calls == 0
    # The driver added no provider failures on a successful path.
    assert report.stats_delta.provider_failures == 0


# ---------------------------------------------------------------------------
# end-to-end smoke — write_forensic_report + driver + classify.
# ---------------------------------------------------------------------------


def test_failed_attempts_serialized_separately_from_verdicts(tmp_path: Path) -> None:
    """FailedAttempt.as_dict() is JSON-serializable and contains
    attempt_index / error / category — it is NOT mixed with the
    semantic verdicts."""
    fa = reliability.FailedAttempt(
        attempt_index=7,
        error="MiniMaxError('GMI Cloud 503: upstream_503')",
        category="provider",
    )
    payload = fa.as_dict()
    assert payload == {
        "attempt_index": 7,
        "error": "MiniMaxError('GMI Cloud 503: upstream_503')",
        "category": "provider",
    }
    # JSON-roundtrip safe (no Pydantic models leaking through).
    encoded = json.dumps(payload)
    assert "attempt_index" in encoded
    assert "category" in encoded
