#!/usr/bin/env python3
"""Print the recording manifest the user should fill in.

For each of the three user-recorded Phase 4 fixtures, this script
prints:
  - the intended line text (so the user knows what to say)
  - the target path for the audio file (so the user knows where to drop it)
  - the line index (which becomes the audio filename stem)

Indexing convention:
  - BASE folder: filename index = BASE line index.
  - branch_a / branch_b folder: filename index = branch line
    index (the position in the branch's surviving lines,
    *not* the BASE line index). For a branch with no deletes
    these two coincide.

Usage:
    cd backend && uv run python ../scripts/print_recording_manifest.py

Or specify a subset:
    cd backend && uv run python ../scripts/print_recording_manifest.py \
        01_canonical_prereq_loss 02_qualifier_loss
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT / "backend"))

from tests.fixtures.semantic_fixtures import (  # noqa: E402
    RECORDED_DIR,
    SCRIPTS,
)


def main() -> int:
    only: set[str] = set(sys.argv[1:])
    scripts = SCRIPTS if not only else [s for s in SCRIPTS if s.name in only]
    if not scripts:
        print(f"No matching scripts. Available: {[s.name for s in SCRIPTS]}")
        return 1
    for script in scripts:
        print()
        print("=" * 72)
        print(f"FIXTURE: {script.name}")
        print(f"  BASE lines (filename index == BASE line index):")
        for i, line in enumerate(script.lines):
            print(f"    [{i}] {line.text!r}")
            print(
                f"        -> record to {RECORDED_DIR / script.name / 'base' / f'{i}.m4a'}"
            )
        # Branch A
        a_lines = script.branch_a_lines()
        if a_lines and a_lines != script.lines:
            print(f"  BRANCH_A lines (filename index == branch line index):")
            for b_idx, line in enumerate(a_lines):
                print(f"    [{b_idx}] {line.text!r}")
                print(
                    f"        -> record to {RECORDED_DIR / script.name / 'branch_a' / f'{b_idx}.m4a'}"
                )
        # Branch B
        b_lines = script.branch_b_lines()
        if b_lines and b_lines != script.lines:
            print(f"  BRANCH_B lines (filename index == branch line index):")
            for b_idx, line in enumerate(b_lines):
                print(f"    [{b_idx}] {line.text!r}")
                print(
                    f"        -> record to {RECORDED_DIR / script.name / 'branch_b' / f'{b_idx}.m4a'}"
                )
    print()
    print("=" * 72)
    print("Recording tips:")
    print("- Speak at a natural pace; don't try to be too fast.")
    print("- Each audio is forced to exactly line.duration seconds by")
    print("  the fixture builder. Default line.duration is 7.0 seconds")
    print("  (long enough for ~20 words at 175 wpm). If your line is")
    print("  longer, edit the ScriptLine.duration for that line BEFORE")
    print("  recording.")
    print("- Audio format: 16 kHz mono PCM is recommended; the builder")
    print("  re-encodes via ffmpeg so WAV, M4A, MP3, AAC, FLAC, OGG, or")
    print("  Opus are all accepted.")
    print("- Don't worry about pronunciation; just say the intended line")
    print("  exactly as printed above.")
    print("- Filenames: 0-based numbering is canonical but the builder")
    print("  also accepts 1-based (e.g. `1.m4a` for line index 0).")
    print()
    print("If you numbered your files 1, 2, 3 by *position in the folder*,")
    print("(e.g. the first audio in branch_a is 1.m4a even though the")
    print("BASE line it represents is BASE line 1) then the builder will")
    print("map position-in-folder to branch-line-index — which is what")
    print("the manifest above prints. Just make sure the 1-based")
    print("convention is consistent across a single (fixture, branch)")
    print("folder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
