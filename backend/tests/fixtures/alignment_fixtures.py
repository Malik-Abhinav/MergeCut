"""Phase 3 controlled real-video fixtures.

Each fixture is built from the same BASE (5 shots with distinct
visuals + distinct spoken sentences), then edited deterministically
to produce the branches the user's brief lists:

  Case 1 — deletion:        A = BASE minus shot 2
  Case 2 — replacement:     B = BASE with shot 3 replaced
  Case 3 — trim:            T = BASE with shot 3 trimmed (2s → 1.2s)
  Case 4 — independent:     I_A deletes shot 2; I_B replaces shot 4
  Case 5 — unchanged:       U = BASE re-encoded (no edits)
  Case 6 — transcript-help: TB same visuals as BASE, different speech
                            in shot 3 — transcript similarity must
                            disambiguate
  Case 7 — visual-help:     VB no speech at all but visually distinct
                            shots — visual similarity must succeed
  MergeCut canonical:       MC_A removes shot 1, MC_B replaces shot 3

Each shot's spoken sentence is generated with macOS `say`
(deterministic given the same text + voice) so transcript-to-shot
assignment can be tested.

Each builder returns a tuple of paths:

  (base_path, branch_a_path, branch_b_path, branch_paths_other...)

depending on the case. The path arguments include the whole
fixture set so the integration tests can run all cases without
re-building anything.

The fixtures are *not* re-generated on every test run; they live
under `tests/fixtures/alignment/` and the per-test builders are
deterministic.
"""

from __future__ import annotations

import shutil as _shutil
import subprocess
from pathlib import Path
from typing import Final

# Voice used for ALL `say` calls. Default macOS voice. Pinned so
# re-builds produce identical audio bytes.
VOICE: Final[str] = "Albert"
SAY_RATE: Final[int] = 175  # wpm

# 5-shot BASE. Each shot:
#   (colour, duration, transcript_text)
# Shot durations are 3.0 s (not 2.0) so we have room for a 1 s
# pre-roll of silence + ~1.5 s of speech + ~0.5 s trailing
# silence — the 2 s natural pauses between sentences give
# faster-whisper enough silence to segment one transcript per
# shot without losing the alignment between speech midpoint and
# visual shot window.
BASE_SHOTS: Final[list[tuple[str, float, str]]] = [
    ("black", 3.0, "Step one, open the device carefully."),
    ("white", 3.0, "Step two, disconnect the battery first."),
    ("red", 3.0, "Step three, remove the back panel."),
    ("green", 3.0, "Step four, locate the memory slot."),
    ("blue", 3.0, "Step five, install the new module."),
]

# Case 2 replacement visuals + speech.
REPLACE_SHOT: Final[tuple[str, float, str]] = (
    "yellow",
    3.0,
    "Step three, lift the cover instead.",
)

# Case 3 trim: same colour, shortened from 3.0s to 2.2s (a real
# trim — 26.7% shorter than BASE, beyond the
# similarity.TRIM_MAX_REL_DIFF=0.30 threshold is exceeded, so it
# would be classified as "different shot" not "trim". Use a
# trim of 3.0 → 2.4 (20% shorter) which is under the threshold
# and keeps it the same shot.
TRIM_SHOT: Final[tuple[str, float, str]] = (
    "red",
    2.4,
    "Step three, remove the back panel.",  # same transcript
)


def _say_wav(text: str, target: Path) -> Path:
    """Generate a mono AIFF/WAV file of `text` via macOS `say`."""
    # `say` outputs AIFF natively. Convert to WAV pcm_s16le 16 kHz
    # mono to match the Phase 2 pipeline expectation.
    aiff = target.with_suffix(".aiff")
    wav = target
    if wav.exists():
        wav.unlink()
    if aiff.exists():
        aiff.unlink()
    proc = subprocess.run(
        ["say", "-v", VOICE, "-r", str(SAY_RATE), "-o", str(aiff), text],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"`say` failed (rc={proc.returncode}): {proc.stderr}")
    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not on PATH")
    ff = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(aiff),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            str(wav),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if ff.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (rc={ff.returncode}): {ff.stderr}")
    aiff.unlink(missing_ok=True)
    return wav


def _video_from_shots(
    shot_specs: list[tuple[str, float, str]],
    target: Path,
    *,
    fps: int = 30,
    width: int = 320,
    height: int = 240,
    inter_shot_silence: float = 0.0,
    leading_silence: float = 0.0,
) -> Path:
    """Build one MP4 from (colour, duration, transcript_text) specs.

    Each shot is rendered as a solid-colour block at `width`x`height`
    @ `fps` for the given duration. Each shot's audio is a fresh
    `say` synthesis of the transcript text. All shots are concatenated
    with ffmpeg's concat filter (audio + video merged per shot).

    A short silent gap (`inter_shot_silence` seconds of black
    video + silence audio) is inserted between shots. This gives
    faster-whisper's VAD filter a real silence boundary to split
    on — without it, the ASR groups adjacent sentences into one
    segment and the shot-level transcript assignment cannot be
    done at the midpoint (a midpoint falls inside the merged
    segment).

    The output MP4 is h264 + yuv420p + 30 fps with per-shot audio
    segments aligned to the shot durations.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not on PATH")

    # Generate per-shot WAVs first.
    wav_paths: list[Path] = []
    for i, (_colour, _dur, text) in enumerate(shot_specs):
        # We pad short audio to exactly the shot duration with
        # `apad` + `atrim` so each shot's audio matches its
        # visual duration. Without padding, the merged audio would
        # be shorter than the visual track and ffmpeg would
        # produce a black-frozen-tail video.
        wav = _say_wav(text, target.with_name(f"{target.stem}_shot{i:02d}.wav"))
        wav_paths.append(wav)

    # Build the ffmpeg command. We interleave silent audio pads
    # between shot audios so faster-whisper gets a real silence
    # boundary to split on — but only when `inter_shot_silence >
    # 0`. When it is 0, audio is shot0 → shot1 → ... → shotN
    # concatenated with no padding. The video stream is *not*
    # padded mid-clip in either case (that would cause
    # PySceneDetect to detect extra black "shots").
    #
    # Sequence (no silence pads):
    #   video: shot0_video → shot1_video → ... → shotN_video
    #   audio: shot0_audio → shot1_audio → ... → shotN_audio
    #
    # Sequence (with silence pads):
    #   video: shot0_video → shot1_video → ... → shotN_video
    #   audio: shot0_audio → silence → shot1_audio → silence → ...
    #
    # When `inter_shot_silence > 0`, the audio stream is longer
    # than the video stream. We extend the video with a trailing
    # black pad so ffmpeg's `-shortest` does not truncate the
    # audio.
    #
    # When `inter_shot_silence == 0`, the two streams line up
    # exactly and we use `-shortest` to handle any tiny rounding
    # difference between shot durations.

    has_silence_pads = inter_shot_silence > 0 or leading_silence > 0
    silence_wav: Path | None = None
    leading_silence_wav: Path | None = None
    if has_silence_pads:
        if inter_shot_silence > 0:
            silence_wav = target.with_name(f"{target.stem}_silence.wav")
            if silence_wav.exists():
                silence_wav.unlink()
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"anullsrc=r=16000:cl=mono:d={inter_shot_silence}",
                    str(silence_wav),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg silence failed: {proc.stderr[:200]}")
        if leading_silence > 0:
            leading_silence_wav = target.with_name(f"{target.stem}_leading.wav")
            if leading_silence_wav.exists():
                leading_silence_wav.unlink()
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"anullsrc=r=16000:cl=mono:d={leading_silence}",
                    str(leading_silence_wav),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg leading silence failed: {proc.stderr[:200]}")

    # Inputs.
    inputs: list[str] = []
    # Video inputs: one per shot, no trailing pad. If the audio
    # is shorter than the video (which happens when `say` output
    # duration < shot duration), the final shot's frame is silent
    # for the last fraction of a second — acceptable for
    # downstream PySceneDetect.
    n = len(shot_specs)
    for _i, (colour, dur, _text) in enumerate(shot_specs):
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s={width}x{height}:r={fps}:d={dur}"]
    n_v = n

    # Audio inputs.
    for i, wav in enumerate(wav_paths):
        if leading_silence > 0:
            inputs += ["-i", str(leading_silence_wav)]
        inputs += ["-i", str(wav)]
        if inter_shot_silence > 0 and i < len(wav_paths) - 1:
            inputs += ["-i", str(silence_wav)]

    # Audio input count: per shot we have (leading? + shot) =
    # (1+1)*n = 2n if leading. Inter-shot silences: (n-1).
    # Total: 2n + (n-1) = 3n-1 (with both), or n + (n-1) = 2n-1
    # (inter only), or n (no silence).
    if leading_silence > 0 and inter_shot_silence > 0:
        n_a = 3 * n - 1
    elif leading_silence > 0:
        n_a = 2 * n
    elif inter_shot_silence > 0:
        n_a = 2 * n - 1
    else:
        n_a = n

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
    ]
    cmd.append(str(target))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed (rc={proc.returncode}): {proc.stderr[:500]}")

    # Tidy up.
    for wav in wav_paths:
        wav.unlink(missing_ok=True)
    if silence_wav is not None:
        silence_wav.unlink(missing_ok=True)
    if leading_silence_wav is not None:
        leading_silence_wav.unlink(missing_ok=True)

    return target


def build_base(target_dir: Path) -> Path:
    """Build the canonical 5-shot BASE."""
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / "base_5shots.mp4"
    _video_from_shots(BASE_SHOTS, out)
    return out


def build_case1_deletion(target_dir: Path) -> Path:
    """A = BASE with shot 2 deleted (between shot 1 and shot 3)."""
    out = target_dir / "case1_deletion.mp4"
    # Drop BASE_SHOTS[1] (white) — keep 0, 2, 3, 4.
    _video_from_shots([s for i, s in enumerate(BASE_SHOTS) if i != 1], out)
    return out


def build_case2_replacement(target_dir: Path) -> Path:
    """B = BASE with shot 3 replaced by REPLACE_SHOT (yellow + new speech)."""
    out = target_dir / "case2_replacement.mp4"
    new_shots = list(BASE_SHOTS)
    new_shots[2] = REPLACE_SHOT
    _video_from_shots(new_shots, out)
    return out


def build_case3_trim(target_dir: Path) -> Path:
    """T = BASE with shot 3 trimmed (2.0s → 1.2s, same colour + same speech)."""
    out = target_dir / "case3_trim.mp4"
    new_shots = list(BASE_SHOTS)
    new_shots[2] = TRIM_SHOT
    _video_from_shots(new_shots, out)
    return out


def build_case4_independent_a(target_dir: Path) -> Path:
    """IA = BASE with shot 2 deleted."""
    out = target_dir / "case4_independent_a.mp4"
    _video_from_shots([s for i, s in enumerate(BASE_SHOTS) if i != 1], out)
    return out


def build_case4_independent_b(target_dir: Path) -> Path:
    """IB = BASE with shot 4 replaced."""
    out = target_dir / "case4_independent_b.mp4"
    new_shots = list(BASE_SHOTS)
    new_shots[3] = ("purple", 2.0, "Step four, find the secondary slot.")
    _video_from_shots(new_shots, out)
    return out


def build_case5_unchanged(target_dir: Path) -> Path:
    """U = BASE re-encoded through ffmpeg (no semantic edits).

    We re-encode BASE through ffmpeg (copy codec → same bytes) so
    the file is byte-equivalent to BASE — the alignment layer
    must report zero edits.
    """
    out = target_dir / "case5_unchanged.mp4"
    src = build_base(target_dir)
    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not on PATH")
    if out.exists():
        out.unlink()
    proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(src), "-c", "copy", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg copy failed: {proc.stderr[:300]}")
    return out


def build_case6_transcript_helpful(target_dir: Path) -> Path:
    """TB = same visuals as BASE, different speech in shot 3.

    Transcript similarity must be low for shot 3 even though
    visuals match. The alignment should report REPLACE on shot 3
    once transcript disambiguates (or at least UNCERTAIN, not
    UNCHANGED).
    """
    out = target_dir / "case6_transcript_helpful.mp4"
    new_shots: list[tuple[str, float, str]] = []
    for i, (colour, dur, text) in enumerate(BASE_SHOTS):
        if i == 2:
            new_shots.append((colour, dur, "Different wording for the same visuals."))
        else:
            new_shots.append((colour, dur, text))
    _video_from_shots(new_shots, out)
    return out


def build_case7_visual_helpful(target_dir: Path) -> Path:
    """VB = no audio at all, but visually distinct shots.

    Visual similarity must drive the alignment (transcript
    signal absent → re-normalize over visual+duration+order).
    """
    out = target_dir / "case7_visual_helpful.mp4"
    target_dir.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not on PATH")

    # Build a 5-shot silent video (distinct colours, same
    # duration as BASE).
    colours = [c for c, _, _ in BASE_SHOTS]
    durs = [d for _, d, _ in BASE_SHOTS]
    inputs: list[str] = []
    for c, d in zip(colours, durs, strict=True):
        inputs += ["-f", "lavfi", "-i", f"color=c={c}:s=320x240:r=30:d={d}"]
    filter_inputs = "".join(f"[{i}:v]" for i in range(len(colours)))
    cmd = [
        ffmpeg,
        "-y",
        *inputs,
        "-filter_complex",
        f"{filter_inputs}concat=n={len(colours)}:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[:500]}")
    return out


def build_mergecut_canonical_a(target_dir: Path) -> Path:
    """MergeCut canonical: A removes shot 1 (the prerequisite)."""
    out = target_dir / "mergecut_canonical_a.mp4"
    new_shots = [s for i, s in enumerate(BASE_SHOTS) if i != 0]
    _video_from_shots(new_shots, out)
    return out


def build_mergecut_canonical_b(target_dir: Path) -> Path:
    """MergeCut canonical: B replaces shot 3 with a different instruction."""
    out = target_dir / "mergecut_canonical_b.mp4"
    new_shots = list(BASE_SHOTS)
    new_shots[2] = ("red", 2.0, "Step three, just lift the cover.")  # same colour
    _video_from_shots(new_shots, out)
    return out


# Convenience: build everything in one call.
def build_all(target_dir: Path) -> dict[str, Path]:
    """Build every Phase 3 fixture. Returns a name → path map."""
    target_dir.mkdir(parents=True, exist_ok=True)
    return {
        "base": build_base(target_dir),
        "case1_deletion": build_case1_deletion(target_dir),
        "case2_replacement": build_case2_replacement(target_dir),
        "case3_trim": build_case3_trim(target_dir),
        "case4_independent_a": build_case4_independent_a(target_dir),
        "case4_independent_b": build_case4_independent_b(target_dir),
        "case5_unchanged": build_case5_unchanged(target_dir),
        "case6_transcript_helpful": build_case6_transcript_helpful(target_dir),
        "case7_visual_helpful": build_case7_visual_helpful(target_dir),
        "mergecut_canonical_a": build_mergecut_canonical_a(target_dir),
        "mergecut_canonical_b": build_mergecut_canonical_b(target_dir),
    }


__all__ = [
    "BASE_SHOTS",
    "REPLACE_SHOT",
    "TRIM_SHOT",
    "VOICE",
    "SAY_RATE",
    "build_base",
    "build_case1_deletion",
    "build_case2_replacement",
    "build_case3_trim",
    "build_case4_independent_a",
    "build_case4_independent_b",
    "build_case5_unchanged",
    "build_case6_transcript_helpful",
    "build_case7_visual_helpful",
    "build_mergecut_canonical_a",
    "build_mergecut_canonical_b",
    "build_all",
]
