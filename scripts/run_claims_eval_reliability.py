"""Phase 4.5 — reliability + accounting + forensic artifact helpers for
``scripts/run_claims_eval.py``.

This module is intentionally tiny, dependency-free (stdlib only), and
importable by both the harness and the unit tests. It owns three
concerns that the harness needs but the orchestrator / M3 client do
not:

  1. **Failure classification** — distinguishing provider-level
     transient failures from semantic/schema failures. A *provider
     failure* is a transient upstream condition the retry layer
     already exhausted (HTTP 429 / 502 / 503 / 504, misleading
     outer-401 with upstream-503 body, transport errors). A
     *semantic/schema failure* is a deterministic condition
     (Pydantic validation failure, missing 'claims' key, missing
     `claim_id`, M3 returned non-JSON, orchestrator refused to
     proceed). The harness only retries on provider failures.

  2. **Stats accounting** — snapshotting ``MiniMaxClient.stats``
     deltas around each fixture's run window. The retry layer
     already updates the cumulative counters; we snapshot the
     delta so the per-fixture report can show "this fixture's
     runs consumed N successful M3 calls, R retries, F provider
     failures, H HTTP-429, U upstream-503", independent of other
     fixtures.

  3. **Forensic artifact serialization** — writing the full
     ``ClaimCentricAnalysis`` plus every claim/branch/interaction
     detail for every successful run. The harness's primary
     artifact (``phase4_claims_eval.json``) carries the verdict
     rollups; the forensic report is the inspectable per-run
     record of exactly what M3 said about every claim, plus the
     deterministic derivation reasoning and the call counters.

Nothing here mutates the retry layer, the orchestrator, or the
extraction / evaluation prompts. Per the user's brief:
``do not modify prompts, fixtures, expected labels, or interaction
rules``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from app.models.claims import ClaimCentricAnalysis
from app.services.minimax._retry import RetryStats

logger = logging.getLogger(__name__)


T = TypeVar("T")


# ---------------------------------------------------------------------------
# Failure classification.
# ---------------------------------------------------------------------------


class FailureCategory(str, Enum):
    """Category of one attempt's failure.

    The harness treats these categories differently:

      - ``provider``        : transient upstream condition
                              (retry layer already exhausted). The
                              harness may re-attempt the whole
                              ``analyze_claims()`` call without
                              consuming a success slot.
      - ``schema``          : deterministic Pydantic / JSON shape
                              failure from M3. No point retrying;
                              the model will produce the same bad
                              shape again. Surfaces an attempt as
                              ``provider_failure`` for accounting
                              but does NOT trigger a re-attempt.
      - ``semantic``        : orchestrator-side invariant failure
                              (e.g. missing required call). Same
                              as ``schema``: deterministic, no
                              retry, surface as a failed attempt.
      - ``orchestrator``    : any other ``RuntimeError`` raised by
                              the orchestrator or its helpers
                              (alignment crash, etc.). Deterministic
                              by default; treated like ``schema``
                              for retry purposes.
      - ``unknown``         : anything else. Treated like
                              ``schema`` (no retry); flagged for
                              inspection.
    """

    PROVIDER = "provider"
    SCHEMA = "schema"
    SEMANTIC = "semantic"
    ORCHESTRATOR = "orchestrator"
    UNKNOWN = "unknown"


_PROVIDER_TOKENS: tuple[str, ...] = (
    "GMI Cloud 429",
    "GMI Cloud 502",
    "GMI Cloud 503",
    "GMI Cloud 504",
    "HTTP error talking to GMI Cloud",
    "upstream_503",
    "rate_limit_exceeded",
    "temporarily unavailable",
    "connection reset",
    "overload",
)

_SCHEMA_TOKENS: tuple[str, ...] = (
    "ValidationError",
    "validation error",
    "missing 'claims'",
    "missing 'explanation'",
    "non-JSON",
    "M3 failed to return a valid claim list",
    "M3 failed to return a valid ClaimEvaluation",
    "extras_forbidden",
)


_SEMANTIC_TOKENS: tuple[str, ...] = (
    "no rule matched",
    "explanation did not influence",
)


def classify_failure(exc: BaseException) -> FailureCategory:
    """Return the category of one attempt's failure.

    The classifier is intentionally simple: it inspects the
    exception message and the exception type. The retry layer
    already separates transient HTTP / transport from
    deterministic status, so any ``MiniMaxError`` whose message
    matches ``_PROVIDER_TOKENS`` is by definition a post-retry
    exhaustion (the retry layer already tried 5 times before
    giving up). Everything else is deterministic.
    """
    msg = str(exc) if exc is not None else ""
    lower = msg.lower()
    for tok in _PROVIDER_TOKENS:
        if tok.lower() in lower:
            return FailureCategory.PROVIDER
    # Schema-failure tokens are checked before generic RuntimeError
    # so a schema message embedded in a wrapped RuntimeError is
    # still classified correctly.
    for tok in _SCHEMA_TOKENS:
        if tok.lower() in lower:
            return FailureCategory.SCHEMA
    for tok in _SEMANTIC_TOKENS:
        if tok.lower() in lower:
            return FailureCategory.SEMANTIC
    # Pydantic ValidationError is a class-level match.
    cls_name = type(exc).__name__
    if cls_name in {"ValidationError", "PydanticValidationError"}:
        return FailureCategory.SCHEMA
    if cls_name in {"JSONDecodeError"}:
        return FailureCategory.SCHEMA
    if cls_name in {
        "KeyError",
        "TypeError",
        "ValueError",
        "AssertionError",
        "RuntimeError",
    }:
        return FailureCategory.ORCHESTRATOR
    return FailureCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Stats snapshot.
# ---------------------------------------------------------------------------


@dataclass
class RetryStatsSnapshot:
    """Point-in-time snapshot of ``MiniMaxClient.stats`` counters.

    ``as_dict()`` is the JSON-serializable form the harness writes
    into the eval artifact; ``delta()`` produces a fresh snapshot
    representing the difference between two snapshots (so the
    harness can report "this fixture's --runs consumed N successful
    M3 calls, R retries, ...").
    """

    successful_calls: int = 0
    retries: int = 0
    provider_failures: int = 0
    http_429_count: int = 0
    upstream_503_count: int = 0

    @classmethod
    def from_stats(cls, stats: RetryStats) -> RetryStatsSnapshot:
        return cls(
            successful_calls=stats.successful_calls,
            retries=stats.retries,
            provider_failures=stats.provider_failures,
            http_429_count=stats.http_429_count,
            upstream_503_count=stats.upstream_503_count,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "successful_calls": self.successful_calls,
            "retries": self.retries,
            "provider_failures": self.provider_failures,
            "http_429_count": self.http_429_count,
            "upstream_503_count": self.upstream_503_count,
        }

    def delta(self, baseline: RetryStatsSnapshot) -> RetryStatsSnapshot:
        return RetryStatsSnapshot(
            successful_calls=self.successful_calls - baseline.successful_calls,
            retries=self.retries - baseline.retries,
            provider_failures=self.provider_failures - baseline.provider_failures,
            http_429_count=self.http_429_count - baseline.http_429_count,
            upstream_503_count=self.upstream_503_count - baseline.upstream_503_count,
        )


# ---------------------------------------------------------------------------
# Failed-attempt record.
# ---------------------------------------------------------------------------


@dataclass
class FailedAttempt:
    """One failed whole-run attempt, surfaced for forensic reporting.

    The harness records every provider-failure attempt with
    ``attempt_index`` (1-based within a fixture's run window), the
    error message, and the failure category. These records are
    PERSISTED separately from the per-run verdicts and NEVER
    appear in the verdict distribution / modal / variance
    computation — a failed attempt is not a verdict, it is an
    accounting event.
    """

    attempt_index: int
    error: str
    category: str  # FailureCategory value

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_index": self.attempt_index,
            "error": self.error,
            "category": self.category,
        }


# ---------------------------------------------------------------------------
# Bounded retry-on-provider loop (whole-call level).
# ---------------------------------------------------------------------------


@dataclass
class AttemptOutcome:
    """Result of one whole ``analyze_claims()`` attempt."""

    result: Any  # ClaimAnalysisArtifacts on success
    elapsed_s: float
    failed_attempt: FailedAttempt | None = None


DEFAULT_MAX_FAILED_ATTEMPTS_PER_FIXTURE: int = 12


def run_attempts_until_success(
    *,
    fn: Callable[[], Any],
    stats: RetryStats,
    fixture_name: str,
    target_successful_runs: int,
    max_failed_attempts: int = DEFAULT_MAX_FAILED_ATTEMPTS_PER_FIXTURE,
    sleeper: Callable[[float], None] | None = None,
) -> tuple[list[AttemptOutcome], list[FailedAttempt], RetryStatsSnapshot]:
    """Drive ``fn()`` until we have ``target_successful_runs``
    successful outcomes, recording every failure.

    The loop is bounded in TWO ways:

      1. We stop as soon as we have ``target_successful_runs``
         successful outcomes (i.e. successful whole-run
         ``analyze_claims()`` calls).
      2. We stop after ``max_failed_attempts`` failed attempts
         per fixture (default 12). A provider failure does NOT
         consume a successful slot, but the harness MUST cap the
         total number of failed attempts so a persistently broken
         upstream cannot make the eval hang.

    Re-attempts are only made for ``FailureCategory.PROVIDER``
    failures. Schema / semantic / orchestrator failures are
    surface as failed attempts but NOT re-attempted (the same
    prompt / schema / orchestrator invariant will fail the same
    way again).

    Returns ``(successful_outcomes, failed_attempts, stats_snapshot)``.
    The snapshot is the delta from the caller's
    ``RetryStatsSnapshot`` baseline to the final
    ``RetryStats`` state, so the caller can attach per-fixture
    deltas to the eval artifact.

    Sleeps between provider-failure re-attempts are forwarded to
    ``sleeper`` if supplied; the default uses ``time.sleep``
    with a 1-second backoff (the retry layer has already added
    its own backoff; the harness-level backoff is a small extra
    margin to avoid thundering-herd on the same fixture).
    """
    sleeper = sleeper or time.sleep
    baseline = RetryStatsSnapshot.from_stats(stats)
    successful: list[AttemptOutcome] = []
    failed: list[FailedAttempt] = []
    attempt_index = 0
    while len(successful) < target_successful_runs:
        attempt_index += 1
        if len(failed) >= max_failed_attempts:
            logger.warning(
                "run_attempts_until_success fixture=%s cap reached (%d failed attempts);"
                " stopping with %d/%d successful runs",
                fixture_name,
                max_failed_attempts,
                len(successful),
                target_successful_runs,
            )
            break
        t0 = time.monotonic()
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 — we classify everything
            elapsed = time.monotonic() - t0
            category = classify_failure(e)
            failed.append(
                FailedAttempt(
                    attempt_index=attempt_index,
                    error=repr(e),
                    category=category.value,
                )
            )
            logger.info(
                "run_attempts_until_success fixture=%s attempt=%d failed category=%s elapsed=%.2fs",
                fixture_name,
                attempt_index,
                category.value,
                elapsed,
            )
            # Only retry on provider failures. Schema / semantic /
            # orchestrator failures are deterministic and would
            # fail again the same way; recording one of them
            # is enough. Stop the loop immediately so the
            # harness does not "spin" on a deterministic
            # invariant (and so the next fixture in the
            # ``main()`` loop still gets a chance to run
            # within the user's wall-clock budget).
            if category is not FailureCategory.PROVIDER:
                logger.warning(
                    "run_attempts_until_success fixture=%s attempt=%d non-retryable"
                    " failure category=%s; stopping driver",
                    fixture_name,
                    attempt_index,
                    category.value,
                )
                break
            # Provider failure: back off briefly and retry, but
            # only if the cap is not yet hit.
            if len(failed) >= max_failed_attempts:
                break
            sleeper(1.0)
            continue
        elapsed = time.monotonic() - t0
        successful.append(AttemptOutcome(result=result, elapsed_s=elapsed))
        logger.info(
            "run_attempts_until_success fixture=%s attempt=%d success elapsed=%.2fs (%d/%d)",
            fixture_name,
            attempt_index,
            elapsed,
            len(successful),
            target_successful_runs,
        )
    delta = RetryStatsSnapshot.from_stats(stats).delta(baseline)
    return successful, failed, delta


# ---------------------------------------------------------------------------
# Forensic artifact serialization.
# ---------------------------------------------------------------------------


def serialize_claim_forensic(
    artifacts: Any,
) -> dict[str, Any]:
    """Serialize one successful ``ClaimAnalysisArtifacts`` to a JSON
    payload that captures every claim, every branch's per-claim
    survival, every interaction (with the deterministic derivation
    reason and the M3 explanation), the overall rollups, and the
    call counters / timing / retries.

    Designed to be the inspectable record for forensic diffing
    between runs and between fixtures. The shape mirrors the
    orchestrator's output:

      {
        "base_claims": [BaseClaim, ...],          # full id/meaning/
                                                  # type/importance/
                                                  # evidence_regions/
                                                  # equivalents
        "branch_a_claim_survivals": [ClaimSurvival, ...],
        "branch_b_claim_survivals": [ClaimSurvival, ...],
        "combined_claim_survivals": [ClaimSurvival, ...],
        "interactions": [
            {
                "claim_id": str,
                "claim_meaning": str,
                "claim_type": str,
                "claim_importance": str,
                "branch_a_status": str,
                "branch_b_status": str,
                "combined_status": str,
                "interaction": str,
                "derivation_reason": str,
                "m3_explanation": str | None,
                "m3_recommended_resolution": str | None,
            },
            ...
        ],
        "overall_interaction": str,
        "overall_impact": str,
        "overall_confidence": float,
        "notes": str | None,
        "call_counts": {
            "extraction": int,
            "evaluation": int,
            "explanation": int,
            "retries": int,
        },
        "model": str,
        "extraction_prompt_version": str,
        "evaluation_prompt_version": str,
      }

    Every nested Pydantic model is serialized via ``.model_dump()``
    so the JSON is stable across Pydantic minor versions. Lists are
    JSON-serializable without further coercion.
    """
    analysis: ClaimCentricAnalysis = artifacts.analysis
    base_claims = [bc.model_dump() for bc in analysis.base_claims]
    branch_a_survivals = [
        cs.model_dump() for cs in analysis.branch_a_claims.claim_survivals
    ]
    branch_b_survivals = [
        cs.model_dump() for cs in analysis.branch_b_claims.claim_survivals
    ]
    combined_survivals = [
        cs.model_dump() for cs in analysis.combined_claims.claim_survivals
    ]
    interactions = [ci.model_dump() for ci in analysis.interactions]
    return {
        "base_claims": base_claims,
        "branch_a_claim_survivals": branch_a_survivals,
        "branch_b_claim_survivals": branch_b_survivals,
        "combined_claim_survivals": combined_survivals,
        "interactions": interactions,
        "overall_interaction": analysis.overall_interaction.value,
        "overall_impact": analysis.overall_impact.value,
        "overall_confidence": analysis.overall_confidence,
        "notes": analysis.notes,
        # Phase 4 representation-correctness: per-fixture actual
        # content (BASE / A / B / combined) + edit metadata. The
        # final reporting layer can read this to show the exact
        # verbatim content per fixture.
        "representation": {
            "base": _snapshot_for_forensic(artifacts.representation.base),
            "branch_a": _snapshot_for_forensic(artifacts.representation.branch_a),
            "branch_b": _snapshot_for_forensic(artifacts.representation.branch_b),
            "combined": _snapshot_for_forensic(artifacts.representation.combined),
        },
        "call_counts": {
            "extraction": artifacts.n_extraction_calls,
            "evaluation": artifacts.n_evaluation_calls,
            "explanation": artifacts.n_explanation_calls,
            "extraction_retries": artifacts.extraction_retries,
            "evaluation_retries": artifacts.evaluation_retries,
        },
        "model": artifacts.model,
        "extraction_prompt_version": artifacts.extraction_prompt_version,
        "evaluation_prompt_version": artifacts.evaluation_prompt_version,
    }


def _snapshot_for_forensic(snapshot: Any) -> dict[str, Any]:
    """Render one ``ReconstructedActualContent`` for the forensic JSON.

    Carries the actual-content lines (the verbatim text the
    viewer hears) plus the edit-metadata audit. The two are
    separated so the eval harness can show "what the viewer
    heard" vs. "what the alignment decided" side-by-side.
    """
    return {
        "branch": snapshot.branch,
        "lines": [
            {
                "base_sequence_index": line.base_sequence_index,
                "text": line.text,
                "operation": line.operation,
                "deleted": line.deleted,
            }
            for line in snapshot.lines
            if line.text.strip()
        ],
        "text_lines": snapshot.text_lines(),
        "edit_metadata": [e.model_dump() for e in snapshot.edit_metadata.entries],
    }


def write_forensic_report(
    *,
    out_dir: Path,
    fixture_name: str,
    n_successful_runs: int,
    forensics: list[dict[str, Any]],
    failed_attempts: list[FailedAttempt],
    stats_delta: RetryStatsSnapshot,
    focused_fixtures: tuple[str, ...] = (
        "02_qualifier_loss",
        "06_one_branch_broken",
        "08_hard_negative_related",
    ),
) -> Path:
    """Write the per-fixture forensic artifact and return the path.

    ``forensics`` is a list of payloads produced by
    ``serialize_claim_forensic``, one per successful run. The
    artifact shape is:

      {
        "fixture": str,
        "n_successful_runs": int,
        "n_failed_attempts": int,
        "stats_delta": {successful_calls, retries, provider_failures,
                         http_429_count, upstream_503_count},
        "failed_attempts": [{attempt_index, error, category}, ...],
        "runs": [forensic_payload, ...],   # one per successful run
        "focused_inspection": {...}        # only when fixture_name
                                           # is in focused_fixtures
      }

    The ``focused_inspection`` block is a compact, easy-to-scan
    view of the run-1 forensic payload for the user-named
    fixtures (02, 06, 08). It surfaces:

      - every BASE claim (id, meaning, type, importance, evidence
        region count, equivalent count),
      - every ClaimInteraction (the deterministic derivation
        reason + the M3 explanation + the M3 recommended
        resolution),
      - the overall rollups (interaction, impact, confidence).

    The compact view is written to a separate file
    (``<fixture>_focused.json``) so the user can inspect the
    three user-named fixtures without scrolling through the full
    per-run forensic dump.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "fixture": fixture_name,
        "n_successful_runs": n_successful_runs,
        "n_failed_attempts": len(failed_attempts),
        "stats_delta": stats_delta.as_dict(),
        "failed_attempts": [fa.as_dict() for fa in failed_attempts],
        "runs": forensics,
    }
    artifact_path = out_dir / f"{fixture_name}_forensic.json"
    artifact_path.write_text(json.dumps(payload, indent=2, default=str))
    if fixture_name in focused_fixtures and forensics:
        focused = _build_focused_inspection(fixture_name, forensics[0])
        focused_path = out_dir / f"{fixture_name}_focused.json"
        focused_path.write_text(json.dumps(focused, indent=2, default=str))
        logger.info(
            "write_forensic_report fixture=%s wrote focused inspection to %s",
            fixture_name,
            focused_path,
        )
    return artifact_path


def _build_focused_inspection(
    fixture_name: str,
    run_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build the easy-to-scan view for fixtures 02, 06, 08.

    The compact view omits the per-branch ClaimSurvival rationale
    text (which lives in the full artifact) but surfaces:

      - BASE claims: every claim with id, meaning, type,
        importance, evidence region count, equivalent count.
      - interactions: every ClaimInteraction with all the
        user-named fields (branch_a_status, branch_b_status,
        combined_status, interaction, derivation_reason,
        m3_explanation, m3_recommended_resolution).
      - overall rollups.
    """
    base_claims_compact = [
        {
            "claim_id": bc["claim_id"],
            "meaning": bc["meaning"],
            "claim_type": bc["claim_type"],
            "importance": bc["importance"],
            "n_evidence_regions": len(bc.get("evidence_regions", [])),
            "n_equivalents": len(bc.get("equivalents", [])),
            "evidence_regions": bc.get("evidence_regions", []),
            "equivalents": bc.get("equivalents", []),
        }
        for bc in run_payload.get("base_claims", [])
    ]
    interactions_compact = [
        {
            "claim_id": ci["claim_id"],
            "claim_meaning": ci["claim_meaning"],
            "claim_type": ci["claim_type"],
            "claim_importance": ci["claim_importance"],
            "branch_a_status": ci["branch_a_status"],
            "branch_b_status": ci["branch_b_status"],
            "combined_status": ci["combined_status"],
            "interaction": ci["interaction"],
            "derivation_reason": ci["derivation_reason"],
            "m3_explanation": ci["m3_explanation"],
            "m3_recommended_resolution": ci["m3_recommended_resolution"],
        }
        for ci in run_payload.get("interactions", [])
    ]
    return {
        "fixture": fixture_name,
        "base_claims": base_claims_compact,
        "interactions": interactions_compact,
        "overall_interaction": run_payload.get("overall_interaction"),
        "overall_impact": run_payload.get("overall_impact"),
        "overall_confidence": run_payload.get("overall_confidence"),
        "notes": run_payload.get("notes"),
        "call_counts": run_payload.get("call_counts"),
        "model": run_payload.get("model"),
        # Phase 4 representation-correctness: surface the actual
        # verbatim content the viewer hears (BASE / A / B /
        # combined) for the user-named fixtures so the final
        # reporting can show "what M3 saw" for each one.
        "representation": run_payload.get("representation", {}),
    }


__all__ = [
    "DEFAULT_MAX_FAILED_ATTEMPTS_PER_FIXTURE",
    "AttemptOutcome",
    "FailedAttempt",
    "FailureCategory",
    "RetryStatsSnapshot",
    "classify_failure",
    "run_attempts_until_success",
    "serialize_claim_forensic",
    "write_forensic_report",
]
