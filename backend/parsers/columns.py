"""Column detection and reading order (§4.1).

The rule from the spec: project span x-intervals onto the x-axis and look for
gutters -- x-ranges with zero text coverage that stay clear over most of the
page's text height. Two details make or break it:

* Full-width elements (a title, an abstract, a wide table) cross every gutter.
  They are excluded from the projection, then used to cut the page into
  horizontal bands so reading order stays correct.
* A gutter must be *tall*, not merely empty somewhere. We measure the fraction
  of the text height at which the corridor is crossed by any line at all.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.parsers.model import Line, TextBlock
from backend.utils.geometry import Box, union

BIN = 1.0                   # x-projection resolution, points
MIN_GUTTER_PT = 7.0         # narrower than this is word spacing, not a gutter
MAX_CROSS_FRACTION = 0.40   # clear over >= 60% of the text height (spec)
WIDE_LINE_FRACTION = 0.60   # a line wider than this share of the text width
                            # is "full width" and cannot define a gutter
CLEAR_COVERAGE = 0.08       # a corridor may carry this much stray ink and
                            # still count as clear (one centred author line)
MIN_PARALLEL_FRACTION = 0.40  # columns are *parallel* text streams
MIN_COLUMN_PT = 60.0
BAND = 0.08                 # header/footer band excluded from the projection


@dataclass
class ColumnLayout:
    columns: list[tuple[float, float]]
    text_area: Box | None

    @property
    def count(self) -> int:
        return len(self.columns)


def detect_columns(lines: list[Line], page_width: float, page_height: float,
                   rtl: bool = False, graphics: list[Box] | None = None
                   ) -> ColumnLayout:
    """Find column x-ranges.

    Three tests must all pass before an empty corridor is called a gutter:

      1. it is clear -- almost no ink at any x inside it;
      2. it is rarely crossed -- full-width elements may cut across it, but
         only over a minority of the text height;
      3. there is text on *both* sides of it at the same heights over a large
         part of the page.

    Graphics count towards test 3.

    Test 3 is what separates a real gutter from the whitespace between the
    columns of a table, which is clear and uncrossed but only spans the few
    centimetres the table occupies.
    """
    lines = [ln for ln in lines if ln.text.strip() and not ln.rotated]
    if not lines:
        return ColumnLayout([(0.0, page_width)], None)
    text_area = union([ln.bbox for ln in lines])
    tx0, ty0, tx1, ty1 = text_area
    text_w = max(1.0, tx1 - tx0)
    text_h = max(1.0, ty1 - ty0)

    body = [ln for ln in lines
            if BAND * page_height < (ln.bbox[1] + ln.bbox[3]) / 2
            < (1 - BAND) * page_height]
    narrow = [ln for ln in body
              if (ln.bbox[2] - ln.bbox[0]) < WIDE_LINE_FRACTION * text_w]
    if len(narrow) < 6:
        return ColumnLayout([(tx0, tx1)], text_area)

    nbins = max(1, int(text_w / BIN) + 1)
    per_bin: list[list[tuple[float, float]]] = [[] for _ in range(nbins)]
    for ln in narrow:
        a = max(0, int((ln.bbox[0] - tx0) / BIN))
        b = min(nbins - 1, int((ln.bbox[2] - tx0) / BIN))
        iv = (ln.bbox[1], ln.bbox[3])
        for i in range(a, b + 1):
            per_bin[i].append(iv)
    clear = [_union_length(iv) <= CLEAR_COVERAGE * text_h for iv in per_bin]

    runs: list[tuple[int, int]] = []
    i = 0
    while i < nbins:
        if not clear[i]:
            i += 1
            continue
        j = i
        while j < nbins and clear[j]:
            j += 1
        if (j - i) * BIN >= MIN_GUTTER_PT:
            runs.append((i, j))
        i = j
    runs = [(a, b) for a, b in runs if a > 0 and b < nbins]

    gutters: list[tuple[float, float]] = []
    for a, b in runs:
        gx0, gx1 = tx0 + a * BIN, tx0 + b * BIN
        crossing = [ln.bbox for ln in lines
                    if ln.bbox[0] < gx1 - 0.5 and ln.bbox[2] > gx0 + 0.5]
        if _covered_height(crossing) / text_h > MAX_CROSS_FRACTION:
            continue
        # A figure fills its column as effectively as text does; without this
        # a page whose left column holds a large figure reads as one column.
        occ = [ln.bbox for ln in body] + [g for g in (graphics or [])]
        left = [(o[1], o[3]) for o in occ if o[2] <= gx0 + 1]
        right = [(o[1], o[3]) for o in occ if o[0] >= gx1 - 1]
        if _overlap_length(_merge(left), _merge(right)) / text_h < MIN_PARALLEL_FRACTION:
            continue
        gutters.append((gx0, gx1))

    if not gutters:
        return ColumnLayout([(tx0, tx1)], text_area)

    bounds = [tx0]
    for gx0, gx1 in sorted(gutters):
        bounds.extend([gx0, gx1])
    bounds.append(tx1)
    cols = [(bounds[k], bounds[k + 1]) for k in range(0, len(bounds) - 1, 2)]
    cols = [c for c in cols if c[1] - c[0] >= MIN_COLUMN_PT]
    if len(cols) < 2:
        return ColumnLayout([(tx0, tx1)], text_area)
    if rtl:
        cols = list(reversed(cols))
    return ColumnLayout(cols, text_area)


def _merge(ivs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for y0, y1 in sorted(ivs):
        if out and y0 <= out[-1][1]:
            out[-1][1] = max(out[-1][1], y1)
        else:
            out.append([y0, y1])
    return [(a, b) for a, b in out]


def _union_length(ivs: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in _merge(ivs))


def _overlap_length(a: list[tuple[float, float]],
                    b: list[tuple[float, float]]) -> float:
    total, i, j = 0.0, 0, 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if hi > lo:
            total += hi - lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return total


def _covered_height(boxes: list[Box]) -> float:
    """Union length of the y-intervals of `boxes`."""
    ivs = sorted((b[1], b[3]) for b in boxes)
    total, cur0, cur1 = 0.0, None, None
    for y0, y1 in ivs:
        if cur0 is None:
            cur0, cur1 = y0, y1
            continue
        if y0 > cur1:
            total += cur1 - cur0
            cur0, cur1 = y0, y1
        else:
            cur1 = max(cur1, y1)
    if cur0 is not None:
        total += cur1 - cur0
    return total


def assign_columns(blocks: list[TextBlock], layout: ColumnLayout) -> None:
    """column_index per block; -1 marks a block spanning more than one column."""
    cols = layout.columns
    for b in blocks:
        if len(cols) == 1:
            b.column_index = 0
            continue
        x0, x1 = b.bbox[0], b.bbox[2]
        overlaps = []
        for idx, (cx0, cx1) in enumerate(cols):
            ov = min(x1, cx1) - max(x0, cx0)
            if ov > 0:
                overlaps.append((idx, ov))
        wide = [idx for idx, ov in overlaps if ov > 0.55 * (cols[idx][1] - cols[idx][0])]
        if len(wide) > 1:
            b.column_index = -1                      # spans columns
        elif overlaps:
            b.column_index = max(overlaps, key=lambda t: t[1])[0]
        else:
            b.column_index = 0


def reading_order(blocks: list[TextBlock], layout: ColumnLayout,
                  rtl: bool = False) -> list[TextBlock]:
    """Band-aware ordering.

    Full-width blocks split the page into bands; inside a band, columns are
    read in order (reversed for RTL) and blocks top-to-bottom, ties broken by
    x0. This is what keeps a two-column paper with a spanning title and a
    spanning table from being read as one scrambled stream.
    """
    if not blocks:
        return []
    spanning = sorted([b for b in blocks if b.column_index == -1],
                      key=lambda b: b.bbox[1])
    columned = [b for b in blocks if b.column_index != -1]
    if len(layout.columns) == 1 or not columned:
        ordered = sorted(blocks, key=lambda b: (round(b.bbox[1], 1),
                                                -b.bbox[0] if rtl else b.bbox[0]))
        for i, b in enumerate(ordered):
            b.reading_order = i
        return ordered

    cuts = [(-1e9, 1e9)]
    edges = [0.0]
    for sb in spanning:
        edges.append(sb.bbox[1])
        edges.append(sb.bbox[3])
    edges.append(1e9)
    bands = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]

    ordered: list[TextBlock] = []
    for band_top, band_bottom in bands:
        band_span = [b for b in spanning
                     if abs(b.bbox[1] - band_top) < 0.01 and b.bbox[3] <= band_bottom + 0.01]
        ordered.extend(sorted(band_span, key=lambda b: b.bbox[1]))
        inband = [b for b in columned
                  if band_top - 0.01 <= (b.bbox[1] + b.bbox[3]) / 2 < band_bottom]
        col_ids = sorted({b.column_index for b in inband}, reverse=rtl)
        for cid in col_ids:
            ordered.extend(sorted([b for b in inband if b.column_index == cid],
                                  key=lambda b: (round(b.bbox[1], 1), b.bbox[0])))
    # anything not placed (degenerate bands) goes last, in geometric order
    placed = {id(b) for b in ordered}
    rest = [b for b in blocks if id(b) not in placed]
    ordered.extend(sorted(rest, key=lambda b: (round(b.bbox[1], 1), b.bbox[0])))
    for i, b in enumerate(ordered):
        b.reading_order = i
    _ = cuts
    return ordered
