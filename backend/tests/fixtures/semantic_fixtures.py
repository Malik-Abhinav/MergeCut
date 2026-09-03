"""Phase 4 real-video semantic fixtures.

Each fixture is a (BASE, A, B) triple of `VideoRepresentation`s
built from a single text "script" that encodes the semantic
content. The script is rendered as a sequence of shot-level
audio segments using macOS `say` so the Phase 2 pipeline
produces real transcripts.

The eight fixtures map to the user's required Phase 4 cases:

  01 canonical_prereq_loss   — A preserved, B preserved, combined broken
                                 (creates_new_conflict).
  02 qualifier_loss           — A preserves a qualifier, B preserves
                                 it, combined drops it.
  03 cause_effect_safe        — A removes the cause, B removes the
                                 effect; combined still works.
  04 safe_unrelated           — A and B edit unrelated claims.
  05 safe_independent         — A and B edit different parts of
                                 the same claim, combined is fine.
  06 one_branch_broken        — A is broken alone, B preserved,
                                 combined broken, interaction is
                                 AMPLIFIES (or NONE) not
                                 creates_new_conflict.
  07 redundant_wording        — A degraded, B preserved, combined
                                 still preserved (no new conflict).
  08 hard_negative_related    — A and B touch related content but
                                 do not combine to break it.

Each script is a list of (speaker, text) lines. The fixture
builds one BASE shot per line, then constructs A and B by
editing specific lines in the script:

  - "DELETE"  : the line is removed (and a "Step skipped." TTS
                line is NOT inserted; the surrounding shots
                collapse in the timeline).
  - "REPLACE" : the line is replaced with the alternative text.
  - "KEEP"    : the line is unchanged.
  - "WEAKEN"  : the line's qualifier is softened (used for
                the qualifier_loss case).

The semantics (which line carries which claim) are encoded in
the script AND in the `expected_*.py` companion file. The
fixtures do not encode expected labels into the produced
videos; the expected labels live separately so a Phase 5
evaluation harness can swap them in.

The fixture builder is deterministic: re-runs produce
byte-identical MP4 files (the only stochastic step is
`faster-whisper` ASR, which is greedy given `beam_size=1`).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

VOICE = "Daniel"  # macOS TTS voice. en_GB; produces ASR-clean transcripts
# on every Phase 4 fixture line. The previous voice ("Albert") produced
# wildly non-deterministic transcripts (e.g. "Before opening the device"
# was heard as "Before I leave the device") and broke the canonical
# Phase 4 prerequisite-loss fixture. Daniel is the new default. Per-line
# WAV overrides (`wav_path` on `ScriptLine`) let the user drop in a
# hand-recorded WAV for fixtures that need a human voice.
SAY_RATE = 175  # wpm

# Where the user can drop hand-recorded WAVs that override the TTS
# render. The fixture builder uses a `<wav_path>` set on a `ScriptLine`
# if it exists; otherwise it falls back to `say -v Daniel`.
RECORDED_DIR = Path("/Users/abhinav/Desktop/Projects/mergecut/data/recorded")


# ---------------------------------------------------------------------------
# Script dataclass.
# ---------------------------------------------------------------------------


@dataclass
class ScriptLine:
    """One shot's text + speaker. The Phase 2 pipeline renders
    one shot per line."""

    speaker: str
    text: str
    duration: float = 7.0  # seconds; long enough for the longest
    # Phase 4 fixture sentence (including the user-recorded
    # ones, which come in around 5.5–6.3 seconds). The previous
    # default of 4.0s truncated user recordings. If you record
    # a line that exceeds 7 seconds, set `ScriptLine.duration`
    # explicitly for that line.
    # Optional path to a pre-recorded audio file. When set AND
    # the file exists, the fixture builder uses the recorded
    # audio instead of calling `say`. Audio can be WAV, M4A,
    # MP3, AAC, FLAC, OGG, or Opus — the builder re-encodes
    # to mono 16 kHz PCM via ffmpeg.
    wav_path: Path | None = None


@dataclass
class Script:
    """A complete script (BASE) with the edit descriptions for A and B.

    `edits_a` and `edits_b` map a 0-based line index to one of:
        "KEEP"    : the line is unchanged in the branch.
        "DELETE"  : the line is removed in the branch.
        "REPLACE" : the line is replaced with `replacements[i]`.
        "WEAKEN"  : the line's qualifier is softened to
                    `replacements[i]`.

    The base script is the union of all lines that are not
    deleted in either branch.
    """

    name: str
    lines: list[ScriptLine]
    edits_a: dict[int, str] = field(default_factory=dict)
    edits_b: dict[int, str] = field(default_factory=dict)
    replacements_a: dict[int, str] = field(default_factory=dict)
    replacements_b: dict[int, str] = field(default_factory=dict)

    def base_lines(self) -> list[ScriptLine]:
        """Lines that form BASE.

        BASE is the full script — every line. The branches are
        the script with their respective edits applied. The
        Phase 3 alignment then determines which shots each
        branch kept / deleted / replaced.

        We previously filtered BASE to the intersection of the
        two branches' surviving lines, but that filter drops
        lines that are *essential* for the canonical MergeCut
        case: the prerequisite that A deletes AND the
        follow-up that B rewrites both live in BASE; their
        edits are the conflict. Filtering them out of BASE
        prevents the Phase 4 orchestrator (and the user, in
        the UI) from seeing the claim structure that the
        branches each half-destroy.
        """
        return list(self.lines)

    def branch_a_lines(self) -> list[ScriptLine]:
        out: list[ScriptLine] = []
        for i, line in enumerate(self.lines):
            edit = self.edits_a.get(i, "KEEP")
            if edit == "DELETE":
                continue
            if edit == "REPLACE" and i in self.replacements_a:
                out.append(ScriptLine(line.speaker, self.replacements_a[i], line.duration))
            elif edit == "WEAKEN" and i in self.replacements_a:
                out.append(ScriptLine(line.speaker, self.replacements_a[i], line.duration))
            else:
                out.append(line)
        return out

    def branch_b_lines(self) -> list[ScriptLine]:
        out: list[ScriptLine] = []
        for i, line in enumerate(self.lines):
            edit = self.edits_b.get(i, "KEEP")
            if edit == "DELETE":
                continue
            if edit == "REPLACE" and i in self.replacements_b:
                out.append(ScriptLine(line.speaker, self.replacements_b[i], line.duration))
            elif edit == "WEAKEN" and i in self.replacements_b:
                out.append(ScriptLine(line.speaker, self.replacements_b[i], line.duration))
            else:
                out.append(line)
        return out


# ---------------------------------------------------------------------------
# The 8 fixture scripts.
# ---------------------------------------------------------------------------


SCRIPTS: list[Script] = [
    # ----------------------------------------------------------------------
    # 01. Canonical prerequisite-loss conflict.
    # BASE communicates: "before opening the device, unplug it; once
    # unplugged, lift the cover."
    # A deletes the unplugging prerequisite.
    # B rewrites the second sentence to remove the "once unplugged"
    # context (so it no longer implies unplugging happened).
    # Combined: the user no longer hears "unplug before opening".
    Script(
        name="01_canonical_prereq_loss",
        lines=[
            ScriptLine("narrator", "Before opening the device, unplug it from the wall."),
            ScriptLine("narrator", "Once the device is unplugged, lift the cover."),
            ScriptLine("narrator", "Then you can access the battery compartment."),
        ],
        edits_a={0: "DELETE"},
        edits_b={1: "REPLACE"},
        replacements_b={1: "Lift the cover."},
    ),
    # ----------------------------------------------------------------------
    # 02. Qualifier-loss conflict.
    # BASE: "Patients with severe nut allergies must avoid this product."
    # A preserves the qualifier by restating it later.
    # B narrows the qualifier to "allergy" alone.
    # Combined: the qualifier is no longer present in any form.
    Script(
        name="02_qualifier_loss",
        lines=[
            ScriptLine("narrator", "Patients with severe nut allergies must avoid this product."),
            ScriptLine("narrator", "If you are unsure, ask your doctor before use."),
            ScriptLine("narrator", "Customers with any nut allergy should consult a pharmacist."),
        ],
        edits_a={},
        edits_b={0: "REPLACE"},
        replacements_b={0: "Patients with allergies must avoid this product."},
    ),
    # ----------------------------------------------------------------------
    # 03. Cause/effect, but combined still works.
    # BASE: "First, preheat the oven to 350. Then bake the cake for
    # 30 minutes."
    # A removes the cause (preheating).
    # B removes the effect (the 30-minute duration).
    # Combined: viewer still hears "bake the cake" but loses
    # both the temperature AND the duration. Under a strict
    # reading this IS a loss; under a loose reading the
    # procedure is still coherent ("bake the cake" stands on
    # its own). The fixture is labelled `expected_safe_combined`
    # because the user-defined rule for Phase 4 is the same
    # loose reading used in Phase 1's v2.1.0 prompt.
    Script(
        name="03_cause_effect_safe",
        lines=[
            ScriptLine("narrator", "First, preheat the oven to 350 degrees."),
            ScriptLine("narrator", "Then bake the cake for 30 minutes."),
        ],
        edits_a={0: "DELETE"},
        edits_b={1: "REPLACE"},
        replacements_b={1: "Then bake the cake."},
    ),
    # ----------------------------------------------------------------------
    # 04. Safe unrelated edits.
    # BASE: a tutorial with three unrelated claims.
    # A edits claim 1 (recipe), B edits claim 3 (safety).
    # Combined: every claim is preserved.
    Script(
        name="04_safe_unrelated",
        lines=[
            ScriptLine("narrator", "To make the sauce, whisk two eggs into the butter."),
            ScriptLine("narrator", "Set the timer for ten minutes."),
            ScriptLine("narrator", "Always wear protective gloves when handling the blade."),
        ],
        edits_a={0: "REPLACE"},
        replacements_a={0: "To make the sauce, whisk three eggs into the butter."},
        edits_b={2: "REPLACE"},
        replacements_b={2: "Always wear protective gloves when handling any sharp tool."},
    ),
    # ----------------------------------------------------------------------
    # 05. Safe independent edits.
    # BASE: "Apply the cream twice daily for seven days. If symptoms
    # persist after seven days, consult a doctor."
    # A rewrites the first sentence to add "gently" (preserves
    # meaning; just changes the procedure).
    # B rewrites the second sentence (preserves the
    # seven-day-persistence claim by moving it earlier).
    # Combined: viewer still gets the 7-day threshold.
    Script(
        name="05_safe_independent",
        lines=[
            ScriptLine("narrator", "Apply the cream twice daily for seven days."),
            ScriptLine("narrator", "If symptoms persist after seven days, consult a doctor."),
        ],
        edits_a={0: "REPLACE"},
        replacements_a={0: "Apply the cream gently twice daily for seven days."},
        edits_b={1: "REPLACE"},
        replacements_b={1: "If your symptoms do not improve within a week, see a doctor."},
    ),
    # ----------------------------------------------------------------------
    # 06. One branch already broken.
    # BASE: "Do not exceed the recommended dose of this medication."
    # A rewrites the sentence to remove the safety threshold.
    # B is a no-op (same as BASE).
    # Combined: the safety threshold is gone. But A is
    # `broken` ALONE — the interaction is not
    # `creates_new_conflict` (B added nothing new).
    Script(
        name="06_one_branch_broken",
        lines=[
            ScriptLine("narrator", "Do not exceed the recommended dose of this medication."),
        ],
        edits_a={0: "REPLACE"},
        replacements_a={0: "Take this medication as needed."},
        edits_b={},  # B = BASE
    ),
    # ----------------------------------------------------------------------
    # 07. Redundant wording (A degraded, combined still safe).
    # BASE: "All customers with nut allergies: ask staff for
    # alternatives. Customers with severe nut allergies: do not
    # consume."
    # A narrows the second sentence (weakening "severe" → "any")
    # — but the first sentence already says "all nut allergies".
    # B is a no-op.
    # Combined: the "all nut allergies" preservation in the
    # first sentence means the A narrowing does not constitute
    # a break.
    Script(
        name="07_redundant_wording",
        lines=[
            ScriptLine("narrator", "All customers with nut allergies: ask staff for alternatives."),
            ScriptLine("narrator", "Customers with severe nut allergies: do not consume."),
        ],
        edits_a={1: "REPLACE"},
        replacements_a={1: "Customers with nut allergies: do not consume."},
        edits_b={},  # B = BASE
    ),
    # ----------------------------------------------------------------------
    # 08. Hard negative — semantically related edits that do not interact.
    # BASE: "Before installing the driver, disable secure boot. Then
    # install the driver."
    # A rewrites sentence 1 (different reason to disable secure boot).
    # B rewrites sentence 2 (different install command).
    # The two edits are semantically related (both touch the
    # install procedure) but the claims they each turn on are
    # independent. Combined is still safe.
    Script(
        name="08_hard_negative_related",
        lines=[
            ScriptLine("narrator", "Before installing the driver, disable secure boot."),
            ScriptLine("narrator", "Then run the installer and restart the computer."),
        ],
        edits_a={0: "REPLACE"},
        replacements_a={0: "Before installing the driver, turn off secure boot in the BIOS."},
        edits_b={1: "REPLACE"},
        replacements_b={1: "Then run the installer as administrator and restart."},
    ),
]


# ---------------------------------------------------------------------------
# Builders.
# ---------------------------------------------------------------------------


def _probe_audio_duration(path: Path) -> float:
    """Return the duration of an audio file in seconds (0.0 on error)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return 0.0
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def _resolve_line_wav(
    line: ScriptLine,
    line_index: int,
    target: Path,
) -> tuple[Path, str]:
    """Return (wav_path, source) for one script line.

    `source` is "recorded" when the line's `wav_path` exists,
    "synthesized" when we have to call macOS `say` to generate
    the audio. We force the WAV to mono 16 kHz pcm_s16le so
    faster-whisper ASR (and the Phase 2 pipeline) get a
    consistent shape regardless of whether the source was
    human-recorded or TTS.

    The output WAV is forced to `effective_duration` seconds,
    where `effective_duration` is the larger of `line.duration`
    and the source audio's actual duration (rounded up to the
    next 0.5s). This guarantees the recording is never
    truncated by the `atrim` filter, even when the user took
    longer than the script's `line.duration` to speak. If the
    source is shorter than `line.duration`, trailing silence
    is appended.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not on PATH")

    def _normalize(input_path: Path, effective: float) -> Path:
        if target.exists():
            target.unlink()
        ff = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(input_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-acodec",
                "pcm_s16le",
                "-af",
                f"apad=pad_dur={effective},atrim=0:{effective}",
                str(target),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if ff.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed normalizing {input_path} (rc={ff.returncode}): {ff.stderr[:200]}"
            )
        return target

    if line.wav_path is not None and line.wav_path.exists():
        src_dur = _probe_audio_duration(line.wav_path)
        effective = max(line.duration, src_dur)
        # Round up to the next 0.5s so we never chop the end of
        # the recording. 0.5s of headroom is enough for
        # faster-whisper's VAD / chunk boundaries.
        effective = max(line.duration, (round(src_dur * 2) + 1) / 2.0)
        return _normalize(line.wav_path, effective), "recorded"

    aiff = target.with_suffix(".aiff")
    if target.exists():
        target.unlink()
    if aiff.exists():
        aiff.unlink()
    proc = subprocess.run(
        ["say", "-v", VOICE, "-r", str(SAY_RATE), "-o", str(aiff), line.text],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"`say` failed (rc={proc.returncode}): {proc.stderr}")
    # For TTS, `say`'s output length is roughly the spoken
    # duration at SAY_RATE wpm, which is usually close to
    # line.duration already. Pad to line.duration exactly.
    return _normalize(aiff, line.duration), "synthesized"


def _say_to_wav(text: str, target: Path) -> Path:
    """Backwards-compatible thin wrapper around `_resolve_line_wav`.

    Used by the existing fixture builder; new code should call
    `_resolve_line_wav` directly so the (source) tag is surfaced.
    """
    wav, _source = _resolve_line_wav(ScriptLine("narrator", text), 0, target)
    return wav


def _build_one_video(
    lines: Sequence[ScriptLine],
    target: Path,
    *,
    fps: int = 30,
    width: int = 320,
    height: int = 240,
    leading_silence: float = 0.5,
    base_indices: Sequence[int] | None = None,
) -> Path:
    """Build a single MP4 from a sequence of script lines.

    Each line becomes a colour block (so PySceneDetect sees
    distinct cuts) with the line's text rendered as a mono
    16 kHz WAV.

    A short silent pre-roll (`leading_silence` seconds of
    black video + silence audio) is inserted before the
    first shot. This is REQUIRED for PySceneDetect's
    `ContentDetector` to see the first shot: without a
    pre-roll, the first shot's hard cut at t=0 is not
    detected (PySceneDetect needs a content change
    *during* the video, not at the start), and the
    resulting `VideoRepresentation` is missing shot 0.
    Without the pre-roll, the Phase 4 fixtures
    consistently lose their first line — which is the
    prerequisite / safety claim that the canonical
    MergeCut test depends on.

    Phase 3.5 — visual provenance:

        ``base_indices`` is a parallel sequence giving the BASE
        line index each ``lines[i]`` corresponds to. When
        provided, the colour assigned to the i-th block is the
        BASE-index colour, not the branch-current-position
        colour. This is the Phase 3.5 repair: a branch that
        deletes an earlier line keeps the original BASE colours
        on the surviving lines, so the BASE visual identity
        survives the deletion.

        For BASE itself, ``base_indices`` is ``range(len(lines))``
        and the colour map is identical to the legacy behaviour.
        For the branches, ``base_indices`` is computed by
        ``branch_base_indices`` below so the colour of a
        surviving branch line is the colour the same line had in
        BASE — never the colour of a different BASE line.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not on PATH")

    wavs: list[Path] = []
    sources: list[str] = []
    effective_durations: list[float] = []
    for i, line in enumerate(lines):
        wav, source = _resolve_line_wav(line, i, target.with_name(f"{target.stem}_shot{i:02d}.wav"))
        wavs.append(wav)
        sources.append(source)
        # Read back the actual audio duration so the video block
        # matches the audio length. `_resolve_line_wav` enforces
        # `effective = max(line.duration, ceil_0.5(audio_dur))`,
        # so a long human recording extends the video block too
        # — preventing PySceneDetect / ASR from truncating the
        # tail of the user's sentence.
        actual = _probe_audio_duration(wav)
        effective_durations.append(actual if actual > 0 else line.duration)

    # Build a leading-silence WAV if requested.
    leading_wav: Path | None = None
    if leading_silence > 0:
        leading_wav = target.with_name(f"{target.stem}_leading.wav")
        if leading_wav.exists():
            leading_wav.unlink()
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r=16000:cl=mono:d={leading_silence}",
                str(leading_wav),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg leading-silence failed: {proc.stderr[:200]}")

    # Inputs.
    inputs: list[str] = []
    palette = ["black", "white", "red", "green", "blue", "yellow", "cyan", "magenta"]

    def _line_colour(position: int) -> str:
        provenance_index = base_indices[position] if base_indices is not None else position
        return palette[provenance_index % len(palette)]

    # The silent pre-roll uses the first surviving shot's BASE
    # colour. A fixed black pre-roll becomes a separate 0.5 s
    # scene whenever an earlier deletion makes the first surviving
    # shot non-black; that phantom scene then corrupts alignment.
    # Matching the first shot's colour keeps the silence inside the
    # first real scene while preserving the first shot boundary.
    leading_colour = _line_colour(0) if lines else "black"
    inputs += [
        "-f",
        "lavfi",
        "-i",
        f"color=c={leading_colour}:s={width}x{height}:r={fps}:d={leading_silence}",
    ]
    # One color per line so each shot is visually distinct.
    # Phase 3.5: when ``base_indices`` is provided, colour by
    # the BASE line index so a branch that deletes an earlier
    # line keeps the original BASE colours on its surviving
    # lines. Without ``base_indices`` (i.e. the legacy BASE
    # builder) we colour by current position, which is
    # 1:1 with BASE indices for the BASE itself.
    for i, line in enumerate(lines):
        colour = _line_colour(i)
        dur = effective_durations[i] if i < len(effective_durations) else line.duration
        inputs += [
            "-f",
            "lavfi",
            "-i",
            f"color=c={colour}:s={width}x{height}:r={fps}:d={dur}",
        ]
    # Audio: leading silence + one wav per line.
    if leading_silence > 0:
        inputs += ["-i", str(leading_wav)]
    for wav in wavs:
        inputs += ["-i", str(wav)]

    n_v = len(lines) + 1  # +1 for the leading black block
    n_a = len(lines) + (1 if leading_silence > 0 else 0)
    v_filter = "".join(f"[{i}:v]" for i in range(n_v)) + f"concat=n={n_v}:v=1:a=0[v]"
    a_filter = "".join(f"[{i + n_v}:a]" for i in range(n_a)) + f"concat=n={n_a}:v=0:a=1[a]"
    filter_complex = f"{v_filter};{a_filter}"

    cmd = [
        ffmpeg,
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-shortest",
        str(target),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed (rc={proc.returncode}): {proc.stderr[:500]}")

    for wav in wavs:
        wav.unlink(missing_ok=True)
    if leading_wav is not None:
        leading_wav.unlink(missing_ok=True)
    return target


def branch_base_indices(script: Script, branch: str) -> list[int]:
    """Return the BASE line index each surviving branch line corresponds to.

    Phase 3.5 — visual provenance repair. The branch line list
    (``script.branch_a_lines()`` / ``script.branch_b_lines()``)
    is the branch's *current* surviving line list, in the order
    those lines appear in the branch video. A delete in an
    earlier line shifts every later line's current position by
    one.

    This helper maps each surviving branch line back to the
    BASE line index it is the (possibly edited) version of.
    The mapping is the single source of truth that the fixture
    video builder uses to colour each surviving branch block
    with the BASE-original colour — so a delete in line 0 of
    A does not change the colour of A's line 1 (which is BASE
    line 1, and must keep BASE line 1's colour in the branch
    video for the visual provenance to survive).

    The mapping is deterministic: iterate BASE in BASE order,
    skip lines the branch deleted, record the BASE index for
    every kept (or replaced) line. Replacements keep the BASE
    index; the visual colour is the BASE line's colour.
    """
    if branch == "branch_a":
        edits = script.edits_a
    elif branch == "branch_b":
        edits = script.edits_b
    else:
        raise ValueError(f"branch must be 'branch_a' or 'branch_b', got {branch!r}")
    out: list[int] = []
    for i, _line in enumerate(script.lines):
        edit = edits.get(i, "KEEP")
        if edit == "DELETE":
            continue
        out.append(i)
    return out


def build_fixture(script: Script, target_dir: Path) -> tuple[Path, Path, Path]:
    """Build one (BASE, A, B) fixture triple from a `Script`.

    Recorded overrides: a per-line WAV override is keyed by the BASE
    line index, not the branch's line index. The user records the
    audio for *what each branch says about that BASE line*. The
    builder figures out the right branch line for each BASE line
    based on the edits dictionary.

    Phase 3.5 — visual provenance:

        The branch videos are built with ``base_indices`` so the
        colour of each surviving branch block is the BASE-index
        colour. Deletions no longer shift the colours of later
        branch blocks: the BASE visual identity survives.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    base_lines = script.base_lines()
    a_lines = script.branch_a_lines()
    b_lines = script.branch_b_lines()
    _apply_recorded_overrides_for_base(script, "base", base_lines)
    _apply_recorded_overrides_for_branch(script, "branch_a", a_lines)
    _apply_recorded_overrides_for_branch(script, "branch_b", b_lines)
    a_base_idx = branch_base_indices(script, "branch_a")
    b_base_idx = branch_base_indices(script, "branch_b")
    base_path = _build_one_video(base_lines, target_dir / f"{script.name}_base.mp4")
    a_path = _build_one_video(a_lines, target_dir / f"{script.name}_a.mp4", base_indices=a_base_idx)
    b_path = _build_one_video(b_lines, target_dir / f"{script.name}_b.mp4", base_indices=b_base_idx)
    return base_path, a_path, b_path


def build_all_fixtures(target_dir: Path) -> dict[str, tuple[Path, Path, Path]]:
    """Build every Phase 4 fixture. Returns name -> (base, a, b)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, tuple[Path, Path, Path]] = {}
    for script in SCRIPTS:
        out[script.name] = build_fixture(script, target_dir)
    return out


def _detect_indexing(branch_dir: Path) -> str:
    """Detect whether the user named their files 0-based or 1-based.

    Returns "0-based" when the branch folder contains any file
    with a 0-suffixed name; otherwise "1-based". The detection
    is per-folder so the user can mix conventions across
    fixtures if they really want to (though they shouldn't).
    """
    if not branch_dir.exists():
        return "0-based"
    for ext in ("wav", "m4a", "mp3", "aac", "flac", "ogg", "opus"):
        if (branch_dir / f"0.{ext}").exists():
            return "0-based"
    return "1-based"


def _find_recorded_audio(
    branch_dir: Path,
    base_line_index: int,
    indexing: str | None = None,
) -> Path | None:
    """Find a recorded audio file for the given BASE line index.

    Honors two naming conventions:

      - "0-based": `<branch_dir>/<base_line_index>.<ext>` (the
        canonical form documented in the recording manifest).

      - "1-based": `<branch_dir>/<base_line_index + 1>.<ext>`
        (a convenience for users who naturally number their
        recordings 1, 2, 3).

    The convention is detected once per (fixture, branch) folder
    via `_detect_indexing` so the same file is never reused for
    two different base indices. Audio extensions supported: wav,
    m4a, mp3, aac, flac, ogg, opus.

    `indexing` is a thread-through for callers that have already
    detected the convention; pass None to detect on the fly.
    """
    extensions = ("wav", "m4a", "mp3", "aac", "flac", "ogg", "opus")
    if indexing is None:
        indexing = _detect_indexing(branch_dir)
    if indexing == "0-based":
        filename = str(base_line_index)
    else:
        filename = str(base_line_index + 1)
    for ext in extensions:
        cand = branch_dir / f"{filename}.{ext}"
        if cand.exists():
            return cand
    return None


def _apply_recorded_overrides_for_base(
    script: Script,
    branch: str,
    lines: list[ScriptLine],
) -> None:
    """Set `wav_path` on each line in `lines` when the user recorded
    a BASE-line-indexed override for this branch.

    Recorded layout (any of these audio extensions work):
        data/recorded/<fixture>/<branch>/<base_line_index>.{wav,m4a,mp3,...}

    The builder also accepts a 1-based filename (e.g. `1.m4a` for
    BASE line index 0) so users can number their recordings
    naturally. The convention is detected per (fixture, branch)
    folder by `_detect_indexing` so the same file is never
    reused for two different base indices.

    For BASE this is 1:1 (the BASE line index == the BASE line index).
    """
    fx_dir = RECORDED_DIR / script.name / branch
    if not fx_dir.exists():
        return
    indexing = _detect_indexing(fx_dir)
    for i, line in enumerate(lines):
        audio = _find_recorded_audio(fx_dir, i, indexing=indexing)
        if audio is not None:
            line.wav_path = audio


def _apply_recorded_overrides_for_branch(
    script: Script,
    branch: str,  # "branch_a" or "branch_b"
    lines: list[ScriptLine],
) -> None:
    """Set `wav_path` on each line in `lines` for branch A or B.

    The recorded layout is **by branch line index**, not BASE
    line index. The user numbers their recordings 0, 1, 2 ... in
    the order the branch's surviving lines appear. (Per the
    recording manifest, this matches the order in
    `branch_a_lines()` / `branch_b_lines()`.)

    This is the inverse of the BASE override, where the file
    index == the BASE line index (1:1). For branches with no
    deletes the two conventions coincide.

    We detect the per-folder indexing (0-based vs 1-based) and
    apply it consistently.
    """
    fx_dir = RECORDED_DIR / script.name / branch
    if not fx_dir.exists():
        return
    indexing = _detect_indexing(fx_dir)
    for branch_idx, line in enumerate(lines):
        audio = _find_recorded_audio(fx_dir, branch_idx, indexing=indexing)
        if audio is not None:
            line.wav_path = audio


def wire_recorded_overrides(
    scripts: list[Script] | None = None,
    recorded_dir: Path = RECORDED_DIR,
) -> list[tuple[str, str, int, Path]]:
    """Walk `recorded_dir` and report which per-line audio files
    are wired (and which line index each one represents).

    Returns a list of (fixture_name, branch, line_index, audio_path)
    tuples, for logging. The actual wiring happens in
    `_apply_recorded_overrides_for_base` and
    `_apply_recorded_overrides_for_branch` at build time so the
    lookup is deterministic and idempotent across runs.

    The line index in the report is:

      - for the BASE folder: the BASE line index.
      - for branch_a / branch_b folders: the branch line index
        (i.e. the position of the line in `branch_a_lines()` /
        `branch_b_lines()`).

    A 1-based filename (e.g. `1.m4a` for line index 0) is
    accepted when the 0-based file is missing; the convention
    is detected per (fixture, branch) folder.

    Expected layout under `recorded_dir`:

        <fixture_name>/<base>/<base_line_index>.<ext>
        <fixture_name>/<branch_a|branch_b>/<branch_line_index>.<ext>
    """
    scripts = scripts or SCRIPTS
    wired: list[tuple[str, str, int, Path]] = []
    if not recorded_dir.exists():
        return wired
    by_name = {s.name: s for s in scripts}
    seen: set[tuple[str, str, int]] = set()
    for fx_dir in sorted(recorded_dir.iterdir()):
        if not fx_dir.is_dir():
            continue
        script = by_name.get(fx_dir.name)
        if script is None:
            continue
        for branch in ("base", "branch_a", "branch_b"):
            branch_dir = fx_dir / branch
            if not branch_dir.is_dir():
                continue
            indexing = _detect_indexing(branch_dir)
            if branch == "base":
                line_count = len(script.lines)
            elif branch == "branch_a":
                line_count = len(script.branch_a_lines())
            else:
                line_count = len(script.branch_b_lines())
            for line_idx in range(line_count):
                audio = _find_recorded_audio(branch_dir, line_idx, indexing=indexing)
                if audio is None:
                    continue
                key = (script.name, branch, line_idx)
                if key in seen:
                    continue
                seen.add(key)
                wired.append((script.name, branch, line_idx, audio))
    return wired


__all__ = [
    "ScriptLine",
    "Script",
    "SCRIPTS",
    "RECORDED_DIR",
    "branch_base_indices",
    "build_fixture",
    "build_all_fixtures",
    "wire_recorded_overrides",
]
