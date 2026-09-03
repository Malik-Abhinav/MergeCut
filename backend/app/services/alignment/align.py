"""Sequence-aware shot alignment (DP / Needleman-Wunsch variant).

Aligns a sequence of BASE `ShotFingerprint`s against a sequence of
branch `ShotFingerprint`s using a 2-D dynamic program that
enforces monotonicity (no reordering). Three transitions per
cell:

- Match  : align base[i] with branch[j]  (cost = blend score)
- Delete : skip base[i]                  (cost = SKIP_PENALTY)
- Insert : skip branch[j]                (cost = SKIP_PENALTY)

The total cell value is the best (maximum, since higher blend
scores are better) of the three. The result is the traceback
from (n, m) back to (0, 0) which gives us:

- base[i] ↔ branch[j]  (match: needs edit-op inference later)
- base[i] only         (deletion)
- branch[j] only       (insert — surfaced as `uncertain` for now)

Why DP and not greedy? Greedy "best match" alignment can pair
shot_2 of BASE with shot_4 of branch (high visual match) while
leaving shot_3 of BASE unmapped, producing an impossible edit
graph. The DP forbids that because every base shot must be
consumed by some transition.

Why monotonic (no reordering)? The brief says reordering is not
required yet. We assume most shots preserve relative order. If a
fixture requires MOVE later (Phase 4+), we'll add a different
algorithm.

The blend score is in [0, 1]. The skip penalty is in [-1, 0].
So a perfect match (1.0) trivially beats a skip (-SKIP_PENALTY).
"""

from __future__ import annotations

from enum import Enum

from app.models.alignment import ShotFingerprint
from app.services.alignment.similarity import (
    DEFAULT_WEIGHTS,
    blend,
)

# Cost of skipping a shot (delete or insert). This is *added* (we
# maximise score) so it is negative. We want the DP to prefer a
# skip only when no good match exists — set it close to zero so
# even a weak match wins.
SKIP_PENALTY = -0.05


class Transition(Enum):
    """How a DP cell was reached."""

    MATCH = "match"  # align base[i] with branch[j]
    DELETE = "delete"  # skip base[i] (branch deleted this shot)
    INSERT = "insert"  # skip branch[j] (branch inserted this shot)


def _cell_label(i: int, j: int) -> tuple[int, int]:
    """2-D index for the DP cell (base i, branch j)."""
    return (i, j)


def align_sequences(
    base: list[ShotFingerprint],
    branch: list[ShotFingerprint],
    *,
    weights: dict[str, float] | None = None,
) -> list[tuple[Transition, ShotFingerprint | None, ShotFingerprint | None]]:
    """Align `base` against `branch` and return a list of
    (transition, base_shot_or_None, branch_shot_or_None).

    The list covers every base shot (deletes have base_shot, None
    branch_shot) and every branch shot (inserts have None
    base_shot, branch_shot). Match entries have both.

    Deterministic: same inputs → same outputs.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    n, m = len(base), len(branch)

    # Edge cases.
    if n == 0 and m == 0:
        return []
    if n == 0:
        return [(Transition.INSERT, None, b) for b in branch]
    if m == 0:
        return [(Transition.DELETE, b, None) for b in base]

    # Pre-compute blend scores for every (i, j) pair so the DP
    # itself is cheap. blend() returns a SimilarityComponents; we
    # only need the final_score.
    score = [
        [blend(base[i], branch[j], weights=weights).final_score for j in range(m)] for i in range(n)
    ]

    # DP table.
    #   dp[i][j] = best score reaching cell (i, j) in 1-based
    #              indexing. dp[0][0] = 0. dp[i][0] = i * SKIP.
    #              dp[0][j] = j * SKIP.
    # We use 1-based indexing to keep the traceback logic simple.
    inf_neg = float("-inf")
    dp: list[list[float]] = [[inf_neg] * (m + 1) for _ in range(n + 1)]
    # Traceback table; each cell records which transition produced
    # the best score.
    tb: list[list[Transition]] = [[Transition.MATCH] * (m + 1) for _ in range(n + 1)]

    dp[0][0] = 0.0
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + SKIP_PENALTY
        tb[i][0] = Transition.DELETE
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + SKIP_PENALTY
        tb[0][j] = Transition.INSERT

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            match_score = dp[i - 1][j - 1] + score[i - 1][j - 1]
            del_score = dp[i - 1][j] + SKIP_PENALTY
            ins_score = dp[i][j - 1] + SKIP_PENALTY

            best = match_score
            best_t = Transition.MATCH
            if del_score > best:
                best = del_score
                best_t = Transition.DELETE
            if ins_score > best:
                best = ins_score
                best_t = Transition.INSERT

            dp[i][j] = best
            tb[i][j] = best_t

    # Traceback.
    out: list[tuple[Transition, ShotFingerprint | None, ShotFingerprint | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and tb[i][j] == Transition.MATCH:
            out.append((Transition.MATCH, base[i - 1], branch[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or tb[i][j] == Transition.DELETE):
            out.append((Transition.DELETE, base[i - 1], None))
            i -= 1
        else:
            out.append((Transition.INSERT, None, branch[j - 1]))
            j -= 1
    out.reverse()
    return out


__all__ = ["Transition", "SKIP_PENALTY", "align_sequences"]
