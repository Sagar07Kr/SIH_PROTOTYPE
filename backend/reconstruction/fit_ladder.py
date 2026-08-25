"""The fit ladder (§4.3).

Seven rungs, strictly ordered by how much a reader would notice the change.
The placer walks them in order and stops at the first rung whose measured
result fits -- `insert_textbox` returns the unused height when the text fits
and a negative number when it does not, and that return value is the only
overflow oracle used anywhere in this codebase.

Rung weights (0, 1, 2, 3, 5, 8, 13) are the adjustment-budget cost of each
rung. They are Fibonacci for one reason: each concession is meaningfully worse
than the last, and a linear scale would let a page of small reductions look
cheaper than a single overflow.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from backend.config import settings
from backend.utils.geometry import Box, height, width

RUNG_WEIGHTS = (0, 1, 2, 3, 5, 8, 13)
RUNG_NAMES = ("original", "tighter leading", "tighter tracking",
              "smaller font", "taller box", "wider box", "overflow")
RUNG_DESCRIPTIONS = (
    "placed at the original size and leading",
    "line spacing tightened",
    "letter spacing tightened by 2%",
    "font size reduced {pct:.0f}% to fit",
    "text box grown {grow:.0f}pt downward into empty space",
    "text box grown {grow:.0f}pt into the page margin",
    "text does not fit; {excess:.0f}pt of overflow left unresolved",
)

MIN_LEADING = 0.95
TRACKING = -0.02
SIZE_STEP = 0.02
MAX_GROW_DOWN = 0.25       # of original box height
MAX_GROW_MARGIN = 0.50     # of the available margin width


@dataclass(frozen=True)
class Attempt:
    rung: int
    rect: Box
    size: float
    leading: float
    tracking: float = 0.0
    writer: str = "textbox"
    grow_pt: float = 0.0
    note: str = ""

    @property
    def weight(self) -> int:
        return RUNG_WEIGHTS[self.rung]


def describe(rung: int, *, original_size: float = 0.0, size: float = 0.0,
             grow: float = 0.0, excess: float = 0.0) -> str:
    pct = 0.0
    if original_size and size:
        pct = (1.0 - size / original_size) * 100
    return RUNG_DESCRIPTIONS[rung].format(pct=pct, grow=grow, excess=excess)


def fit_ladder(rect: Box, size: float, leading: float, writer: str, *,
               size_floor_factor: float | None = None,
               grow_down: float = 0.0, grow_right: float = 0.0,
               margin_width: float = 0.0, allow_grow: bool = True,
               allow_tracking: bool = True) -> list[Attempt]:
    """Materialise the ladder for one block.

    `grow_down` / `grow_right` are the *verified* free space available to rungs
    4 and 5, and `margin_width` is the outer margin rung 5 may eat into; the
    caller measures all three against everything else on the page, and the
    placer re-runs the overlap sweep after growing (I5).

    Rungs 2 and above use the html writer, because tracking needs CSS and the
    concessions are cumulative: once tracking is tightened it stays tightened
    while the size comes down.
    """
    floor = size_floor_factor or settings.prose_size_floor
    out: list[Attempt] = []

    out.append(Attempt(0, rect, size, leading, writer=writer))

    tight = max(MIN_LEADING, min(leading, 1.00))
    if tight < leading - 1e-6:
        out.append(Attempt(1, rect, size, tight, writer=writer))
    lead = tight if tight < leading else leading

    # Tracking needs CSS, so this rung uses the html writer. Scripts whose
    # faces are too large for repeated html layout (CJK) skip the rung
    # entirely: the ladder is ordered, not obligatory.
    track = TRACKING if allow_tracking else 0.0
    if allow_tracking:
        out.append(Attempt(2, rect, size, lead, tracking=TRACKING,
                           writer="htmlbox"))

    steps = int(round((1.0 - floor) / SIZE_STEP))
    for k in range(1, steps + 1):
        s = round(size * (1.0 - k * SIZE_STEP), 3)
        if s < size * floor - 1e-6:
            break
        out.append(Attempt(3, rect, s, lead, tracking=track,
                           writer="htmlbox" if allow_tracking else writer))

    smallest = size * floor
    if allow_grow and grow_down > 1.0:
        grow = min(grow_down, MAX_GROW_DOWN * height(rect))
        if grow > 1.0:
            grown = (rect[0], rect[1], rect[2], rect[3] + grow)
            out.append(Attempt(4, grown, smallest, lead, tracking=track,
                               writer="htmlbox" if allow_tracking else writer,
                               grow_pt=grow))
    if allow_grow and grow_right > 1.0 and margin_width > 1.0:
        grow = min(grow_right, MAX_GROW_MARGIN * margin_width)
        if grow > 1.0:
            wider = (rect[0], rect[1], rect[2] + grow, rect[3])
            if grow_down > 1.0:
                wider = (wider[0], wider[1], wider[2],
                         wider[3] + min(grow_down, MAX_GROW_DOWN * height(rect)))
            out.append(Attempt(5, wider, smallest, lead, tracking=track,
                               writer="htmlbox" if allow_tracking else writer,
                               grow_pt=grow))
    return out


def monotonic(attempts: list[Attempt]) -> bool:
    """Each rung is a weaker concession than the next (unit-tested)."""
    weights = [a.weight for a in attempts]
    return all(b >= a for a, b in zip(weights, weights[1:]))


def widen(attempt: Attempt, *, size: float | None = None) -> Attempt:
    return replace(attempt, size=size or attempt.size)
