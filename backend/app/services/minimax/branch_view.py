"""Branch-content reconstruction helpers.

When the v2.0.0 prompt asks M3 to evaluate per-branch safety against the
FULL remaining branch content (not just the diff), we need a way to
materialize "BASE minus this branch's edit". These helpers do that from
the textual fixture / transcript format used by Phase 1.

The reconstruction is intentionally conservative: it can drop a segment
when the branch-change prose explicitly marks it as `--> DELETED`, and
otherwise preserves the segment. Replacements are not auto-re-written
(the replacement text has to come from the branch-change prose anyway);
the placeholder `<<segment replaced in this branch>>` makes the gap
visible to both M3 and human readers.

In Phase 3 this module is replaced by the real mechanical-edit graph
once alignment + diff are implemented. The Phase 1 spike just needs the
text-level helper to make the decision rule operable.
"""

from __future__ import annotations

import re

# Matches a single timestamped segment line:
#   [00:18–00:24] 'some text'
_SEGMENT_RE = re.compile(
    r"^\[(?P<start>\d{1,2}:\d{2})\u2013(?P<end>\d{1,2}:\d{2})\]\s+'(?P<text>.*)'$"
)


def _ts_to_seconds(ts: str) -> float:
    minutes, seconds = ts.split(":")
    return int(minutes) * 60 + int(seconds)


def parse_base_segments(base_context: str) -> list[dict]:
    """Parse `[mm:ss–mm:ss] 'text'` lines out of a BASE block."""
    out: list[dict] = []
    for line in base_context.splitlines():
        line = line.strip()
        m = _SEGMENT_RE.match(line)
        if not m:
            continue
        out.append(
            {
                "start": _ts_to_seconds(m["start"]),
                "end": _ts_to_seconds(m["end"]),
                "text": m["text"],
            }
        )
    return out


def _is_deleted_segment(branch_change: str, seg_text: str) -> bool:
    """Did this branch change explicitly delete this segment?

    Matches the segment text followed by `--> DELETED` (or
    `--> deleted`, `deleted entirely`, `--> TRIMMED`).
    """
    seg_text_clean = seg_text.strip()
    needle = f"'{seg_text_clean}'"
    pattern = re.compile(
        re.escape(needle) + r".{0,200}?(?:--> DELETED|--> deleted|deleted entirely|--> TRIMMED)",
        re.DOTALL,
    )
    return bool(pattern.search(branch_change))


def _is_replaced_segment(branch_change: str, seg_text: str) -> bool:
    """Did this branch change explicitly replace this segment?

    Matches the segment text followed by `--> REPLACED`.
    """
    seg_text_clean = seg_text.strip()
    needle = f"'{seg_text_clean}'"
    pattern = re.compile(
        re.escape(needle) + r".{0,200}?--> REPLACED",
        re.DOTALL,
    )
    return bool(pattern.search(branch_change))


def _fmt_ts(seconds: float) -> str:
    mm = int(seconds // 60)
    ss = int(seconds % 60)
    return f"{mm:02d}:{ss:02d}"


def build_branch_view(base_context: str, branch_change: str) -> str:
    """Render the *full reconstructed* branch content.

    See module docstring. Returns `base_context` unchanged if no
    `[mm:ss–mm:ss]` segments can be parsed (the v2 prompt still
    degrades gracefully — M3 just sees the raw BASE rather than the
    reconstructed view).
    """
    segments = parse_base_segments(base_context)
    if not segments:
        return base_context

    out_lines: list[str] = []
    for seg in segments:
        stamp = f"[{_fmt_ts(seg['start'])}\u2013{_fmt_ts(seg['end'])}]"
        if _is_deleted_segment(branch_change, seg["text"]):
            out_lines.append(f"{stamp} <<segment deleted in this branch>>")
            continue
        if _is_replaced_segment(branch_change, seg["text"]):
            out_lines.append(
                f"{stamp} <<segment replaced in this branch; see branch-change prose>>"
            )
            continue
        out_lines.append(f"{stamp} '{seg['text']}'")
    return "\n".join(out_lines)
