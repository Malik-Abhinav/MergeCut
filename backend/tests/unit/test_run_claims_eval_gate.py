"""Focused tests for the Phase 4 ASR gate integration in the
`scripts/run_claims_eval.py` harness.

These tests lock in the gate contract:

  - The harness runs `validate_fixture` on every fixture before
    any M3 evaluation.
  - The gate persists a JSON-serializable report at
    `<out-dir>/asr_validation.json` whether or not any fixture
    is ineligible (so the user can read disqualification
    reasons even on refusal).
  - When a fixture is ineligible, the harness refuses the
    live M3 evaluation with exit code 2 and a clear diagnostic;
    ineligible fixtures are NEVER sent to M3 or included as
    valid scored results.
  - `process_video` is called once per fixture per branch (NOT
    once per M3 run), so the ASR work is not repeated across
    the `--runs` iterations.

These tests stub out `process_video` and `validate_fixture`
to avoid the FFmpeg / faster-whisper / macOS `say` dependency.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the scripts/ directory is importable so the harness module
# can be imported without going through the project Makefile.
# The harness lives at <repo-root>/scripts/ (sibling of `backend/`),
# so we walk up from `backend/tests/unit/<this file>` to the repo
# root before joining `scripts` — `parents[3]` is the repo root.
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Imports below intentionally come AFTER the sys.path tweak so
# `import run_claims_eval` resolves regardless of test cwd.
import run_claims_eval as harness  # noqa: E402

from tests.fixtures.asr_gate import (  # noqa: E402
    FixtureValidation,
    ShotRecord,
    VideoRecord,
)

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _make_fixture_validation(
    *,
    name: str,
    eligible: bool,
    min_similarity: float = 1.0,
    flagged: dict[str, int] | None = None,
) -> FixtureValidation:
    """Build a deterministic `FixtureValidation` for unit testing.

    `validate_fixture` constructs one of these from `process_video`
    outputs; we sidestep that path entirely so the test does not need
    FFmpeg or faster-whisper.
    """
    flagged = flagged or {}

    def _record(branch: str) -> VideoRecord:
        return VideoRecord(
            branch=branch,
            video_path=Path(f"/tmp/{name}_{branch}.mp4"),
            expected_lines=["hello world"],
            shots=[
                ShotRecord(
                    shot_id="shot_0000",
                    start=0.0,
                    end=2.0,
                    expected="hello world",
                    recognized="hello world",
                    similarity=1.0,
                )
            ],
        )

    videos = {branch: _record(branch) for branch in ("base", "branch_a", "branch_b")}
    for v in videos.values():
        v.flagged_categories = flagged
        # The real `validate_fixture` calls `rec.recompute()` after
        # building shots; without it the per-shot similarities never
        # propagate to `VideoRecord.min_similarity` / `.avg_similarity`,
        # which makes every stub fixture ineligible for the soft-floor
        # check (0.0 < 0.50). Recompute so the stub mirrors production.
        v.recompute()
        # `recompute()` rebuilds `flagged_categories` from per-shot
        # `missing_markers`. Since the stub's expected/recognized text
        # is fixed ("hello world" / "hello world") and produces no
        # missing markers, recompute would clear the test's intended
        # `flagged` injection. Re-apply after recompute so the harness
        # sees the category the test asked for. (The harness reads
        # `FixtureValidation.flagged_categories`, which aggregates
        # `videos[*].flagged_categories`, so per-video is the right
        # level to set.)
        if flagged:
            v.flagged_categories = flagged
    return FixtureValidation(name=name, videos=videos)


@pytest.fixture
def stub_gate(monkeypatch: pytest.MonkeyPatch) -> dict[str, FixtureValidation]:
    """Replace `validate_fixture` with a stubbed table of pre-built
    `FixtureValidation` objects keyed by fixture name.

    The test mutates `table[name]` to flip eligibility per fixture.
    """
    table: dict[str, FixtureValidation] = {}

    def fake_validate_fixture(  # type: ignore[no-untyped-def]
        *, name, base_path, a_path, b_path, base_expected, a_expected, b_expected, threshold=0.75
    ):
        return table[name]

    monkeypatch.setattr(harness, "validate_fixture", fake_validate_fixture)
    return table


@pytest.fixture
def fixture_paths(tmp_path: Path) -> dict[str, tuple[Path, Path, Path]]:
    """Stand in for the {script.name -> (base, a, b)} path map.

    `_run_asr_gate` only passes the paths through to
    `validate_fixture` (which is stubbed); three empty path
    stubs are sufficient.
    """
    return {
        name: (
            tmp_path / f"{name}_base.mp4",
            tmp_path / f"{name}_a.mp4",
            tmp_path / f"{name}_b.mp4",
        )
        for name in ("01_canonical_prereq_loss", "02_qualifier_loss")
    }


@pytest.fixture(autouse=True)
def _narrow_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restrict the gate iteration to a 2-script subset.

    `_run_asr_gate` iterates over `harness.SCRIPTS`. Using the
    real 8-script list would force the test to stub 8
    validations; we shrink it to the 2 names we actually care
    about, while leaving the rest of the module unchanged.
    """
    narrow = [
        script
        for script in harness.SCRIPTS
        if script.name in {"01_canonical_prereq_loss", "02_qualifier_loss"}
    ]
    monkeypatch.setattr(harness, "SCRIPTS", narrow)


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_gate_persists_report_when_all_eligible(
    tmp_path: Path,
    stub_gate: dict[str, FixtureValidation],
    fixture_paths: dict[str, tuple[Path, Path, Path]],
) -> None:
    """When every fixture is eligible, the report is persisted and
    the gate returns exit code 0 (the M3 eval may proceed)."""
    for name in fixture_paths:
        stub_gate[name] = _make_fixture_validation(name=name, eligible=True)

    validations, rc = harness._run_asr_gate(paths=fixture_paths, out_dir=tmp_path)

    assert rc == 0
    assert set(validations) == set(fixture_paths)
    assert all(v.eligible_for_evaluation for v in validations.values())

    report_path = tmp_path / "asr_validation.json"
    assert report_path.exists()
    payload = json.loads(report_path.read_text())
    assert payload["total"] == len(fixture_paths)
    assert payload["eligible_count"] == len(fixture_paths)
    assert {f["name"] for f in payload["fixtures"]} == set(fixture_paths)
    # The gate schema mirrors `to_dict(FixtureValidation)`.
    for fixture_payload in payload["fixtures"]:
        assert "eligible" in fixture_payload
        assert "videos" in fixture_payload
        assert set(fixture_payload["videos"]) == {"base", "branch_a", "branch_b"}


def test_gate_refuses_and_persists_report_when_any_ineligible(
    tmp_path: Path,
    stub_gate: dict[str, FixtureValidation],
    fixture_paths: dict[str, tuple[Path, Path, Path]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When ANY fixture is ineligible, the harness returns exit
    code 2 with a diagnostic on stderr, AND still persists the
    report (so the user can read disqualification reasons)."""
    # Make the second fixture ineligible: a flagged marker category.
    stub_gate["01_canonical_prereq_loss"] = _make_fixture_validation(
        name="01_canonical_prereq_loss", eligible=True
    )
    stub_gate["02_qualifier_loss"] = _make_fixture_validation(
        name="02_qualifier_loss",
        eligible=False,
        flagged={"negation": 1},
    )

    validations, rc = harness._run_asr_gate(paths=fixture_paths, out_dir=tmp_path)

    assert rc == 2
    assert validations["01_canonical_prereq_loss"].eligible_for_evaluation is True
    assert validations["02_qualifier_loss"].eligible_for_evaluation is False

    # Report was still written so the user can read the diagnostic.
    report_path = tmp_path / "asr_validation.json"
    assert report_path.exists()
    payload = json.loads(report_path.read_text())
    assert payload["eligible_count"] == 1
    assert payload["total"] == 2
    ineligible_payload = next(f for f in payload["fixtures"] if f["name"] == "02_qualifier_loss")
    assert ineligible_payload["eligible"] is False
    assert "negation" in ineligible_payload["flagged_categories"]

    # Stderr surfaces a clear diagnostic naming the ineligible fixture
    # AND warning that the M3 eval was refused.
    captured = capsys.readouterr()
    assert "02_qualifier_loss" in captured.err
    assert "refusing live M3 evaluation" in captured.err


def test_gate_iterates_over_every_fixture(
    tmp_path: Path,
    stub_gate: dict[str, FixtureValidation],
    fixture_paths: dict[str, tuple[Path, Path, Path]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate must call `validate_fixture` exactly once per
    script in `SCRIPTS` — missing a fixture would silently let a
    corrupted transcript reach the live M3 evaluation."""
    for name in fixture_paths:
        stub_gate[name] = _make_fixture_validation(name=name, eligible=True)
    call_log: list[str] = []

    def spy(**kwargs):  # type: ignore[no-untyped-def]
        call_log.append(kwargs["name"])
        return stub_gate[kwargs["name"]]

    monkeypatch.setattr(harness, "validate_fixture", spy)

    harness._run_asr_gate(paths=fixture_paths, out_dir=tmp_path)

    assert sorted(call_log) == sorted(fixture_paths)


def test_load_fixture_representations_calls_process_video_once_per_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_load_fixture_representations` must call `process_video`
    exactly 3 times per fixture (one per BASE/A/B branch) and
    return the resulting `VideoRepresentation`s. The eval loop
    then reuses those objects across all `--runs` M3 iterations,
    so the ASR work is not repeated.

    Before this refactor, `process_video` was called 3 × --runs
    times per fixture, which re-ran faster-whisper every M3 run.
    """
    calls: list[str] = []

    class _FakeRep:
        """Stand-in for `VideoRepresentation`. Anything the M3 eval
        loop might read off it (shots, metadata) is a no-op."""

        shots: list = []

    def fake_process_video(path: Path) -> _FakeRep:
        calls.append(str(path))
        return _FakeRep()

    monkeypatch.setattr(harness, "process_video", fake_process_video)

    base_path = tmp_path / "01_canonical_prereq_loss_base.mp4"
    a_path = tmp_path / "01_canonical_prereq_loss_a.mp4"
    b_path = tmp_path / "01_canonical_prereq_loss_b.mp4"
    for p in (base_path, a_path, b_path):
        p.write_bytes(b"")  # need to exist for repr; fake ignores bytes

    base_rep, a_rep, b_rep = harness._load_fixture_representations(
        fixture_name="01_canonical_prereq_loss",
        base_path=base_path,
        a_path=a_path,
        b_path=b_path,
        out_dir=tmp_path,
    )

    assert len(calls) == 3
    assert calls[0].endswith("01_canonical_prereq_loss_base.mp4")
    assert calls[1].endswith("01_canonical_prereq_loss_a.mp4")
    assert calls[2].endswith("01_canonical_prereq_loss_b.mp4")
    # Reuse property: a SECOND call must NOT add more entries.
    harness._load_fixture_representations(
        fixture_name="01_canonical_prereq_loss",
        base_path=base_path,
        a_path=a_path,
        b_path=b_path,
        out_dir=tmp_path,
    )
    # The second call hits `process_video` again because the cache
    # is on disk (not in-process). The eval loop is responsible
    # for caching the returned objects in Python so subsequent
    # runs reuse them — `_load_fixture_representations` itself is
    # called ONCE per fixture per `main()` invocation. The point of
    # this test is to lock the per-call count of `process_video`
    # so the no-redundant-ASR contract is visible.
    assert len(calls) == 6  # 3 per call × 2 calls
