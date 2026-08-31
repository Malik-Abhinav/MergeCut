"""Phase 1 capability spike runner.

Runs the eight controlled fixtures (5 original + 3 v2 new) through
MiniMax M3 (via GMI Cloud) and records raw responses + validated
verdicts to JSON/MD under `data/derived/`.

v2 changes vs v1.0.0:

- Fixtures live in `tests/fixtures/spike_fixtures.py` and now carry
  per-branch expected safety booleans.
- Output paths are namespaced by prompt version so the v1.0.0 results
  are NOT overwritten.
- The runner evaluates the v2 gate from the product-owner instructions:

      * For the 3 canonical cross-edit conflict fixtures:
          branch_a_safe == True AND branch_b_safe == True AND
          combined_safe == False.
      * Combined classification accuracy >= 7 / 8.
      * Individual branch-safety classification accuracy >= 14 / 16
        (8 fixtures * 2 branches).
      * Both original safe controls remain correctly classified.

Exit codes:
    0  all fixtures produced schema-valid responses AND the v2 gate passes
    2  schema-valid responses but the v2 gate fails (INVESTIGATE)
    3  any fixture failed to produce a schema-valid response (STOP)
    4  GMI_API_KEY missing (no live calls attempted)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# Make `app` importable when this script is run directly.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.services.minimax import (  # noqa: E402
    MiniMaxClient,
    MiniMaxError,
    PROMPT_VERSION,
)
from app.services.minimax.schemas import analyze_semantic_merge  # noqa: E402

from tests.fixtures.spike_fixtures import FIXTURES, Fixture  # noqa: E402


DERIVED_DIR = ROOT / "data" / "derived"


# ---------------------------------------------------------------------------
# Per-prompt-version output paths so v1.0.0 results are preserved.
# ---------------------------------------------------------------------------


def _paths_for_version(version: str) -> tuple[Path, Path]:
    safe = version.replace(".", "_")
    return (
        DERIVED_DIR / f"spike_results_v_{safe}.json",
        DERIVED_DIR / f"spike_results_v_{safe}.md",
    )


# ---------------------------------------------------------------------------
# Per-fixture evaluation.
# ---------------------------------------------------------------------------


def _classify_combined(result_dict: dict) -> str:
    """Reduce the validated result to a spike-level combined verdict.

    `safe`  iff combined_safe is true and conflicts is empty.
    `conflict` otherwise.
    """
    if result_dict.get("combined_safe") is True and not result_dict.get("conflicts"):
        return "safe"
    return "conflict"


def _extract_branch_safe(result_dict: dict, which: str) -> bool | None:
    """Return `branch_a_safe.safe` or `branch_b_safe.safe`, or None."""
    key = "branch_a_safe" if which == "a" else "branch_b_safe"
    obj = result_dict.get(key) or {}
    return obj.get("safe")


async def run_one(client: MiniMaxClient, fixture: Fixture) -> dict:
    """Run a single fixture through M3 and return a JSON-serialisable record."""
    t0 = time.monotonic()
    error_msg: str | None = None
    result_dict: dict | None = None
    raw_text: str | None = None
    try:
        result, raw_text = await analyze_semantic_merge(
            client,
            base_context=fixture.base_context,
            branch_a_change=fixture.branch_a_change,
            branch_b_change=fixture.branch_b_change,
            mechanical_diff=fixture.mechanical_diff,
        )
        result_dict = result.model_dump()
    except MiniMaxError as e:
        error_msg = f"{type(e).__name__}: {e}"
    latency = time.monotonic() - t0

    record = {
        "id": fixture.id,
        "expected_label": fixture.expected_label,
        "expected_conflict_type": fixture.expected_conflict_type,
        "expected_branch_a_safe": fixture.expected_branch_a_safe,
        "expected_branch_b_safe": fixture.expected_branch_b_safe,
        "latency_s": round(latency, 3),
        "error": error_msg,
        "raw": raw_text,
        "parsed": result_dict,
        "conflicts": (result_dict or {}).get("conflicts", []),
        "combined_safe": (result_dict or {}).get("combined_safe"),
        "branch_a_safe_pred": _extract_branch_safe(result_dict, "a") if result_dict else None,
        "branch_b_safe_pred": _extract_branch_safe(result_dict, "b") if result_dict else None,
        "overall_confidence": (result_dict or {}).get("overall_confidence"),
        "m3_verdict": _classify_combined(result_dict) if result_dict else "error",
        "notes": (result_dict or {}).get("notes"),
    }
    return record


# ---------------------------------------------------------------------------
# v2 gate evaluation.
# ---------------------------------------------------------------------------


CANONICAL_CONFLICT_IDS = {"01_prereq_loss", "02_qualifier_loss", "03_cause_effect"}
ORIGINAL_SAFE_CONTROL_IDS = {"04_safe_unrelated", "05_safe_independent"}


def evaluate_v2_gate(rows: list[dict]) -> dict:
    """Compute the metrics the v2 gate requires.

    Returns a dict with:
        - canonical_ok: list of canonical-fixture rows where the axis matches
        - canonical_required: 3
        - combined_total: 8
        - combined_correct: int
        - branch_total: 16
        - branch_correct: int
        - safe_controls_ok: int (out of 2)
        - false_positives: combined conflict -> predicted safe (worst kind)
        - false_negatives: combined safe -> predicted conflict
        - branch_false_positives: branch-A/B predicted safe when expected unsafe
        - branch_false_negatives: branch-A/B predicted unsafe when expected safe
        - decision: "GO" | "INVESTIGATE" | "STOP"
        - decision_reasons: list[str]
    """
    canonical_ok: list[dict] = []
    combined_correct = 0
    branch_correct = 0
    branch_total = 0
    safe_controls_ok = 0
    # Combined-verdict errors of commission vs omission:
    #   false_positive = model says "conflict" when truth is "safe"
    #                   (model over-flags; alarm is false).
    #   false_negative = model says "safe" when truth is "conflict"
    #                   (model misses a real conflict).
    false_positives: list[str] = []  # combined: predicted conflict, expected safe
    false_negatives: list[str] = []  # combined: predicted safe, expected conflict
    branch_fp: list[str] = []
    branch_fn: list[str] = []

    for r in rows:
        parsed = r.get("parsed")
        if parsed is None:
            continue

        # Combined verdict.
        if r["expected_label"] == r["m3_verdict"]:
            combined_correct += 1
        elif r["expected_label"] == "conflict" and r["m3_verdict"] == "safe":
            # Model missed a real conflict — false NEGATIVE.
            false_negatives.append(r["id"])
        elif r["expected_label"] == "safe" and r["m3_verdict"] == "conflict":
            # Model flagged something that's actually safe — false POSITIVE.
            false_positives.append(r["id"])

        # Per-branch safety.
        for which, expected, predicted in (
            (
                "a",
                r["expected_branch_a_safe"],
                r["branch_a_safe_pred"],
            ),
            (
                "b",
                r["expected_branch_b_safe"],
                r["branch_b_safe_pred"],
            ),
        ):
            branch_total += 1
            if predicted is None:
                continue
            if expected == predicted:
                branch_correct += 1
            elif expected is False and predicted is True:
                # Model predicted SAFE when truth is UNSAFE.
                # This is the v1 failure mode: model missed the
                # unsafety. That's a false NEGATIVE against the
                # unsafety class.
                branch_fn.append(f"{r['id']}/branch_{which}")
            elif expected is True and predicted is False:
                # Model predicted UNSAFE when truth is SAFE.
                # Model over-flagged this branch. That's a false
                # POSITIVE against the safety class.
                branch_fp.append(f"{r['id']}/branch_{which}")

        # Canonical axis check.
        if r["id"] in CANONICAL_CONFLICT_IDS:
            if (
                r["branch_a_safe_pred"] is True
                and r["branch_b_safe_pred"] is True
                and r["m3_verdict"] == "conflict"
            ):
                canonical_ok.append(r["id"])

        # Original safe controls still safe?
        if r["id"] in ORIGINAL_SAFE_CONTROL_IDS and r["m3_verdict"] == "safe":
            safe_controls_ok += 1

    # Decide.
    reasons: list[str] = []
    canonical_required = 3
    combined_required = 7
    combined_total = len(rows)
    branch_required = 14
    decision = "GO"

    if combined_correct < combined_required:
        decision = "INVESTIGATE"
        reasons.append(
            f"combined accuracy {combined_correct}/{combined_total} < required {combined_required}/{combined_total}"
        )
    if branch_correct < branch_required:
        decision = "INVESTIGATE"
        reasons.append(
            f"per-branch safety accuracy {branch_correct}/{branch_total} < required {branch_required}/{branch_total}"
        )
    if len(canonical_ok) < canonical_required:
        decision = "INVESTIGATE"
        reasons.append(
            f"canonical axis satisfied for {len(canonical_ok)}/{canonical_required} conflict fixtures"
        )
    if safe_controls_ok < 2:
        decision = "INVESTIGATE"
        reasons.append(
            f"original safe controls correctly classified: {safe_controls_ok}/2"
        )

    return {
        "canonical_ok": canonical_ok,
        "canonical_required": canonical_required,
        "combined_correct": combined_correct,
        "combined_total": len(rows),
        "branch_correct": branch_correct,
        "branch_total": branch_total,
        "safe_controls_ok": safe_controls_ok,
        "safe_controls_required": 2,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "branch_false_positives": branch_fp,
        "branch_false_negatives": branch_fn,
        "decision": decision,
        "decision_reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Markdown report.
# ---------------------------------------------------------------------------


def _format_md_report(rows: list[dict], stats: dict, gate: dict) -> str:
    lines: list[str] = []
    lines.append("# MergeCut Phase 1 spike results (v2)")
    lines.append("")
    lines.append(f"- Prompt version: `{stats['prompt_version']}`")
    lines.append(f"- Model: `{stats['model']}`")
    lines.append(f"- Provider: GMI Cloud")
    lines.append(f"- Timestamp (UTC): {stats['timestamp_utc']}")
    lines.append(
        f"- Combined classification: **{gate['combined_correct']}/{gate['combined_total']}**"
    )
    lines.append(
        f"- Per-branch safety classification: **{gate['branch_correct']}/{gate['branch_total']}**"
    )
    lines.append(
        f"- Canonical axis satisfied: **{len(gate['canonical_ok'])}/{gate['canonical_required']}**"
    )
    lines.append(
        f"- Original safe controls still correct: **{gate['safe_controls_ok']}/{gate['safe_controls_required']}**"
    )
    lines.append("")
    lines.append(
        f"- v2 decision: **{gate['decision']}**"
        + (f" — {'; '.join(gate['decision_reasons'])}" if gate["decision_reasons"] else "")
    )
    lines.append("")
    lines.append(
        "| Fixture | Expected combined | M3 combined | Branch A exp/pred | Branch B exp/pred | Conflicts | Conf. |"
    )
    lines.append(
        "|---------|-------------------|-------------|-------------------|-------------------|-----------|-------|"
    )
    for r in rows:
        a_exp = r["expected_branch_a_safe"]
        b_exp = r["expected_branch_b_safe"]
        a_pred = r["branch_a_safe_pred"]
        b_pred = r["branch_b_safe_pred"]
        a_mark = "OK" if a_exp == a_pred else ("FP" if a_exp is True else "FN")
        b_mark = "OK" if b_exp == b_pred else ("FP" if b_exp is True else "FN")
        conf = (
            f"{r['overall_confidence']:.2f}"
            if r["overall_confidence"] is not None
            else "n/a"
        )
        lines.append(
            f"| `{r['id']}` | {r['expected_label']} | {r['m3_verdict']} | "
            f"{a_exp}/{a_pred} {a_mark} | {b_exp}/{b_pred} {b_mark} | "
            f"{len(r['conflicts'])} | {conf} |"
        )
    lines.append("")

    if gate["false_positives"]:
        lines.append("## False positives (combined: predicted conflict, expected safe)")
        for fid in gate["false_positives"]:
            lines.append(f"- `{fid}`")
        lines.append("")
    if gate["false_negatives"]:
        lines.append("## False negatives (combined: predicted safe, expected conflict)")
        for fid in gate["false_negatives"]:
            lines.append(f"- `{fid}`")
        lines.append("")
    if gate["branch_false_positives"]:
        lines.append("## Branch false positives (predicted unsafe, expected safe)")
        for fid in gate["branch_false_positives"]:
            lines.append(f"- `{fid}`")
        lines.append("")
    if gate["branch_false_negatives"]:
        lines.append("## Branch false negatives (predicted safe, expected unsafe)")
        for fid in gate["branch_false_negatives"]:
            lines.append(f"- `{fid}`")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


async def main_async() -> int:
    settings = get_settings()
    if not settings.gmi_api_key:
        print(
            "ERROR: GMI_API_KEY is not set. Configure .env (see .env.example) "
            "before running the live spike.",
            file=sys.stderr,
        )
        return 4

    DERIVED_DIR.mkdir(parents=True, exist_ok=True)

    async with MiniMaxClient(settings) as client:
        rows: list[dict] = []
        for fixture in FIXTURES:
            print(f"[spike] running {fixture.id} ...", flush=True)
            row = await run_one(client, fixture)
            rows.append(row)
            print(
                f"[spike]   expected_combined={row['expected_label']} "
                f"got={row['m3_verdict']} "
                f"branch_A={row['branch_a_safe_pred']} "
                f"branch_B={row['branch_b_safe_pred']} "
                f"conflicts={len(row['conflicts'])} "
                f"latency={row['latency_s']:.2f}s",
                flush=True,
            )

    gate = evaluate_v2_gate(rows)

    stats = {
        "model": settings.minimax_m3_model,
        "prompt_version": PROMPT_VERSION,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    results_json, results_md = _paths_for_version(PROMPT_VERSION)
    results_json.write_text(
        json.dumps(
            {"stats": stats, "gate": gate, "fixtures": rows},
            indent=2,
        )
    )
    results_md.write_text(_format_md_report(rows, stats, gate))

    print()
    print(f"Prompt version: {PROMPT_VERSION}")
    print(f"Combined: {gate['combined_correct']}/{gate['combined_total']}")
    print(f"Per-branch safety: {gate['branch_correct']}/{gate['branch_total']}")
    print(f"Canonical axis OK: {len(gate['canonical_ok'])}/{gate['canonical_required']}")
    print(f"Safe controls OK: {gate['safe_controls_ok']}/{gate['safe_controls_required']}")
    print(f"False positives: {gate['false_positives']}")
    print(f"False negatives: {gate['false_negatives']}")
    print(f"Branch false positives: {gate['branch_false_positives']}")
    print(f"Branch false negatives: {gate['branch_false_negatives']}")
    print(f"Decision: {gate['decision']}")
    if gate["decision_reasons"]:
        for reason in gate["decision_reasons"]:
            print(f"  - {reason}")
    print(f"Results: {results_json}")
    print(f"Summary: {results_md}")

    any_schema_fail = any(r["parsed"] is None for r in rows)
    if any_schema_fail:
        return 3
    if gate["decision"] != "GO":
        return 2
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        return 130


def regenerate_report_from_json(path: Path) -> int:
    """Re-render the markdown report from an existing spike JSON file.

    Useful when the runner logic changes (e.g. terminology fixes) but
    the live call results don't need to be re-collected. Reads the JSON,
    re-evaluates the gate, and writes back both files.

    Returns 0 on success, non-zero on failure.
    """
    if not path.exists():
        print(f"ERROR: {path} does not exist", file=sys.stderr)
        return 1
    data = json.loads(path.read_text())
    rows = data.get("fixtures", [])
    if not rows:
        print(f"ERROR: {path} contains no fixtures", file=sys.stderr)
        return 2
    gate = evaluate_v2_gate(rows)
    stats = data.get("stats", {})
    md_path = path.with_suffix(".md")
    md_path.write_text(_format_md_report(rows, stats, gate))
    data["gate"] = gate
    path.write_text(json.dumps(data, indent=2))
    print(f"Regenerated {md_path} and updated {path}")
    return 0


if __name__ == "__main__":
    # Convenience: `python run_spike.py --regen-report <json>` rerenders
    # an existing spike JSON's markdown without re-running live calls.
    if len(sys.argv) >= 3 and sys.argv[1] == "--regen-report":
        raise SystemExit(regenerate_report_from_json(Path(sys.argv[2])))
    raise SystemExit(main())