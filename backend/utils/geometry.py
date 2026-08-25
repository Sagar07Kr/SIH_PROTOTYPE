"""Geometry helpers. All coordinates are PDF points in PyMuPDF's top-left
origin space, expressed as 4-tuples (x0, y0, x1, y1)."""
from __future__ import annotations

from typing import Iterable, Sequence

Box = tuple[float, float, float, float]


def norm(b: Sequence[float]) -> Box:
    x0, y0, x1, y1 = b
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def width(b: Sequence[float]) -> float:
    return b[2] - b[0]


def height(b: Sequence[float]) -> float:
    return b[3] - b[1]


def area(b: Sequence[float]) -> float:
    return max(0.0, width(b)) * max(0.0, height(b))


def union(boxes: Iterable[Sequence[float]]) -> Box | None:
    it = list(boxes)
    if not it:
        return None
    return (min(b[0] for b in it), min(b[1] for b in it),
            max(b[2] for b in it), max(b[3] for b in it))


def intersect(a: Sequence[float], b: Sequence[float]) -> Box | None:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def intersection_area(a: Sequence[float], b: Sequence[float]) -> float:
    i = intersect(a, b)
    return area(i) if i else 0.0


def overlap_fraction(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection as a fraction of the *smaller* box's area (I5)."""
    smaller = min(area(a), area(b))
    if smaller <= 0:
        return 0.0
    return intersection_area(a, b) / smaller


def overlap_violations(boxes: Sequence[tuple[str, Box]], tolerance: float = 0.02
                       ) -> list[tuple[str, str, float]]:
    """O(n log n)-ish sweep: sort by x0, compare only while x-ranges touch."""
    items = sorted(boxes, key=lambda kv: kv[1][0])
    out: list[tuple[str, str, float]] = []
    for i, (ka, ba) in enumerate(items):
        for kb, bb in items[i + 1:]:
            if bb[0] >= ba[2]:
                break
            f = overlap_fraction(ba, bb)
            if f > tolerance:
                out.append((ka, kb, f))
    return out


def inflate(b: Sequence[float], dx: float = 0.0, dy: float = 0.0) -> Box:
    return (b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy)


def vertical_free_space(box: Sequence[float], others: Iterable[Sequence[float]],
                        page_bottom: float, pad: float = 1.0) -> float:
    """How far `box` may grow downward before touching anything else."""
    limit = page_bottom - box[3]
    for o in others:
        if o[3] <= box[3] or o[2] <= box[0] or o[0] >= box[2]:
            continue  # above, or no horizontal overlap
        limit = min(limit, max(0.0, o[1] - box[3] - pad))
    return max(0.0, limit)


def horizontal_free_space(box: Sequence[float], others: Iterable[Sequence[float]],
                          page_right: float, pad: float = 1.0) -> float:
    limit = page_right - box[2]
    for o in others:
        if o[2] <= box[2] or o[3] <= box[1] or o[1] >= box[3]:
            continue
        limit = min(limit, max(0.0, o[0] - box[2] - pad))
    return max(0.0, limit)
