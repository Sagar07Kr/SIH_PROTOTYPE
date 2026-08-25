"""Table detection (§4.5).

Ruling lines first: real tables in real documents draw their own grid, and a
grid is far more reliable than any text-alignment heuristic. Borderless tables
fall back to alignment evidence -- at least three rows sharing at least two
consistent x-boundaries.

The output is deliberately geometric: row bounds, column bounds, and one cell
block per occupied cell, with `col_span` set where the grid says a separator is
missing. The reconstruction engine then translates cell by cell and never moves
a ruling line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.parsers.model import (BlockStyle, ElementType, Line, Table,
                                   TextBlock, block_bbox, element_id)
from backend.utils.geometry import Box, height, union, width

H_TOL = 1.6           # y tolerance when clustering horizontal rules
V_TOL = 1.6
THIN = 2.2            # a rect thinner than this is a rule, not a panel
MIN_RULE_LEN = 24.0
MAX_RULE_GAP = 120.0   # vertical distance two rules of one table may span
NUMERIC_RE = re.compile(r"^[\s(]*[-+]?[\d.,]+\s*(%|€|\$|£|₹|ms|s|kg|m|mm|pt|"
                        r"[A-Za-z]{0,3})?[\s)]*$")


@dataclass
class Segment:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def horizontal(self) -> bool:
        return abs(self.y1 - self.y0) <= 0.9 and abs(self.x1 - self.x0) >= MIN_RULE_LEN

    @property
    def vertical(self) -> bool:
        return abs(self.x1 - self.x0) <= 0.9 and abs(self.y1 - self.y0) >= 6.0


def segments_from_drawings(drawings: list[dict]) -> list[Segment]:
    segs: list[Segment] = []
    for d in drawings:
        stroked = d.get("color") is not None
        for item in d.get("items", []):
            kind = item[0]
            if kind == "l":
                p1, p2 = item[1], item[2]
                segs.append(Segment(min(p1.x, p2.x), min(p1.y, p2.y),
                                    max(p1.x, p2.x), max(p1.y, p2.y)))
            elif kind == "re":
                r = item[1]
                if r.height <= THIN:
                    segs.append(Segment(r.x0, (r.y0 + r.y1) / 2, r.x1,
                                        (r.y0 + r.y1) / 2))
                elif r.width <= THIN:
                    segs.append(Segment((r.x0 + r.x1) / 2, r.y0,
                                        (r.x0 + r.x1) / 2, r.y1))
                elif stroked:
                    segs.extend([Segment(r.x0, r.y0, r.x1, r.y0),
                                 Segment(r.x0, r.y1, r.x1, r.y1),
                                 Segment(r.x0, r.y0, r.x0, r.y1),
                                 Segment(r.x1, r.y0, r.x1, r.y1)])
    return segs


def _cluster(values: list[tuple[float, float, float]], tol: float,
             join_gap: float = 3.0) -> list[tuple[float, float, float]]:
    """Merge (pos, lo, hi) triples into rules.

    Two segments merge only when they are both nearly collinear *and*
    touching. Merging every segment that shares an x (or y) coordinate would
    fuse a panel border with the border of an unrelated table further down the
    page into one impossibly tall rule, which then breaks grid detection.
    """
    out: list[list[float]] = []
    for pos, lo, hi in sorted(values):
        merged = False
        for cand in out:
            if abs(pos - cand[0]) <= tol and lo <= cand[2] + join_gap and \
                    hi >= cand[1] - join_gap:
                cand[1] = min(cand[1], lo)
                cand[2] = max(cand[2], hi)
                cand[0] = (cand[0] + pos) / 2
                merged = True
                break
        if not merged:
            out.append([pos, lo, hi])
    return [(p, a, b) for p, a, b in out]


def detect_tables(lines: list[Line], drawings: list[dict], page_index: int,
                  page_width: float, modal_size: float
                  ) -> tuple[list[Table], set[int]]:
    """Returns (tables, ids of consumed text lines).

    Evidence is ranked, because weaker evidence produces spectacular false
    positives (a tinted code panel and the table below it look like one big
    table if you only group horizontal rules by proximity):

      1. a *grid*: two or more vertical rules with a shared y-range, crossed by
         two or more horizontal rules. The region is the grid, so neighbouring
         boxes on the page cannot be absorbed into it.
      2. rules-only: three or more horizontal rules sharing an x-extent, with
         column boundaries recovered from whitespace (the classic three-rule
         "booktabs" table).
      3. alignment only: see `_borderless`.
    """
    segs = segments_from_drawings(drawings)
    hrules = _cluster([(s.y0, s.x0, s.x1) for s in segs if s.horizontal], H_TOL)
    vrules = _cluster([(s.x0, s.y0, s.y1) for s in segs if s.vertical], V_TOL)
    tables: list[Table] = []
    consumed: set[int] = set()

    regions = _grid_regions(hrules, vrules)
    for cand in _rule_only_regions(hrules, page_width):
        if not any(_overlaps(cand[0], r[0]) for r in regions):
            regions.append(cand)
    for region, rule_ys in regions:
        inner_v = [v for v in vrules
                   if region[0] - 2 <= v[0] <= region[2] + 2
                   and v[1] < region[3] - 1 and v[2] > region[1] + 1]
        tl = [ln for ln in lines if _inside(ln.bbox, region) and ln.text.strip()]
        if len(tl) < 4:
            continue
        row_bounds = _row_bounds(rule_ys, tl, region)
        col_bounds = _col_bounds(inner_v, tl, region)
        if len(row_bounds) < 3 or len(col_bounds) < 3:
            continue
        table = _build(region, row_bounds, col_bounds, tl, inner_v, page_index,
                       modal_size, ruled=True)
        if len(table.cells) >= 4:
            tables.append(table)
            consumed.update(id(ln) for ln in tl)

    if not tables:
        t = _borderless(lines, page_index, modal_size, page_width)
        if t is not None:
            tables.append(t)
            consumed.update(id(ln) for ln in lines if _inside(ln.bbox, t.bbox))
    return tables, consumed


def _grid_regions(hrules: list[tuple[float, float, float]],
                  vrules: list[tuple[float, float, float]]
                  ) -> list[tuple[Box, list[float]]]:
    """Regions bounded by an actual grid of rules."""
    out: list[tuple[Box, list[float]]] = []
    used_v = [False] * len(vrules)
    for i, (x, y0, y1) in enumerate(vrules):
        if used_v[i]:
            continue
        group = [(x, y0, y1)]
        used_v[i] = True
        for j in range(i + 1, len(vrules)):
            if used_v[j]:
                continue
            xj, yj0, yj1 = vrules[j]
            gy0 = max(min(g[1] for g in group), yj0)
            gy1 = min(max(g[2] for g in group), yj1)
            span = max(y1 - y0, yj1 - yj0)
            if gy1 - gy0 > 0.6 * span:
                group.append((xj, yj0, yj1))
                used_v[j] = True
        if len(group) < 2:
            continue
        gx0 = min(g[0] for g in group)
        gx1 = max(g[0] for g in group)
        gy0 = min(g[1] for g in group)
        gy1 = max(g[2] for g in group)
        if gx1 - gx0 < 40 or gy1 - gy0 < 12:
            continue
        inner_h = [h for h in hrules
                   if gy0 - 2 <= h[0] <= gy1 + 2
                   and min(h[2], gx1) - max(h[1], gx0) > 0.6 * (gx1 - gx0)]
        if len(inner_h) < 2:
            continue
        # A stroked panel (a tinted code box) is also two verticals crossed by
        # two horizontals. What distinguishes a table is interior structure:
        # at least one vertical strictly inside, and enough rules overall to
        # describe more than a single cell.
        iv = sum(1 for g in group if gx0 + 2 < g[0] < gx1 - 2)
        ih = sum(1 for h in inner_h if gy0 + 2 < h[0] < gy1 - 2)
        if iv < 1 or (iv + ih) < 3:
            continue
        out.append(((gx0, gy0, gx1, gy1), sorted(h[0] for h in inner_h)))
    return out


def _rule_only_regions(hrules: list[tuple[float, float, float]],
                       page_width: float) -> list[tuple[Box, list[float]]]:
    """Three or more horizontal rules sharing an x-extent and stacking closely."""
    out: list[tuple[Box, list[float]]] = []
    used = [False] * len(hrules)
    order = sorted(range(len(hrules)), key=lambda i: hrules[i][0])
    for pos, i in enumerate(order):
        if used[i]:
            continue
        y, x0, x1 = hrules[i]
        group = [(y, x0, x1)]
        used[i] = True
        for j in order[pos + 1:]:
            if used[j]:
                continue
            yj, xj0, xj1 = hrules[j]
            gx0 = min(g[1] for g in group)
            gx1 = max(g[2] for g in group)
            overlap = min(gx1, xj1) - max(gx0, xj0)
            span = max(gx1 - gx0, xj1 - xj0)
            last_y = max(g[0] for g in group)
            if overlap > 0.7 * span and 0 < yj - last_y <= MAX_RULE_GAP:
                group.append((yj, xj0, xj1))
                used[j] = True
        if len(group) < 3:
            continue
        gx0 = min(g[1] for g in group)
        gx1 = max(g[2] for g in group)
        ys = sorted(g[0] for g in group)
        if gx1 - gx0 < 0.15 * page_width or ys[-1] - ys[0] < 12:
            continue
        out.append(((gx0, ys[0], gx1, ys[-1]), ys))
    return out


def _overlaps(a: Box, b: Box) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) > 0
            and min(a[3], b[3]) - max(a[1], b[1]) > 0)


def _inside(box: Box, region: Box, pad: float = 1.5) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return (region[0] - pad <= cx <= region[2] + pad
            and region[1] - pad <= cy <= region[3] + pad)


def _row_bounds(rule_ys: list[float], lines: list[Line], region: Box) -> list[float]:
    """Rules give row boundaries; where rules are sparse (a table with only a
    head rule and a foot rule) the text itself supplies the interior rows."""
    ys = [y for y in rule_ys if region[1] - 1 <= y <= region[3] + 1]
    rows = _text_rows(lines)
    if len(ys) >= len(rows):
        bounds = sorted(set(round(y, 1) for y in ys))
    else:
        bounds = row_bounds_from_spans(lines, region)
        # keep any real rule that is not already close to a derived boundary
        for y in ys:
            if all(abs(y - b) > 3 for b in bounds):
                bounds.append(y)
        bounds = sorted(set(round(b, 1) for b in bounds))
    return bounds


def row_bounds_from_spans(lines: list[Line], region: Box) -> list[float]:
    """Row boundaries from the vertical centres of individual spans.

    Line boxes are the wrong input here: an OCR line box is tall and loose and
    routinely overlaps its neighbour, so midpoints between line boxes cut
    straight through the next row. Span centres cluster tightly around each
    baseline, which is where the rows actually are.
    """
    centres = sorted(((sp.bbox[1] + sp.bbox[3]) / 2, sp.bbox[3] - sp.bbox[1])
                     for ln in lines for sp in ln.spans if sp.text.strip())
    if not centres:
        return [region[1], region[3]]
    heights = sorted(h for _, h in centres)
    med_h = heights[len(heights) // 2] or 10.0
    tol = max(2.0, 0.55 * med_h)
    bands: list[list[float]] = [[centres[0][0]]]
    for cy, _h in centres[1:]:
        if cy - bands[-1][-1] <= tol:
            bands[-1].append(cy)
        else:
            bands.append([cy])
    means = [sum(b) / len(b) for b in bands]
    bounds = [region[1]]
    for a, b in zip(means, means[1:]):
        bounds.append((a + b) / 2)
    bounds.append(region[3])
    return sorted(set(round(b, 2) for b in bounds))


def _text_rows(lines: list[Line]) -> list[tuple[float, float]]:
    """Group lines into rows by vertical overlap.

    The overlap has to be *substantial* (half the shorter line's height), not
    merely non-zero. OCR line boxes are loose and routinely graze the row above
    them; with a 1pt rule every row of a scanned table collapsed into a single
    row and the table stopped being a table.
    """
    rows: list[list[float]] = []
    for ln in sorted(lines, key=lambda l: l.bbox[1]):
        y0, y1 = ln.bbox[1], ln.bbox[3]
        if rows:
            prev = rows[-1]
            overlap = min(prev[1], y1) - max(prev[0], y0)
            shorter = max(1.0, min(prev[1] - prev[0], y1 - y0))
            if overlap > 0.5 * shorter:
                prev[0] = min(prev[0], y0)
                prev[1] = max(prev[1], y1)
                continue
        rows.append([y0, y1])
    return [(a, b) for a, b in rows]


def _col_bounds(vrules: list[tuple[float, float, float]], lines: list[Line],
                region: Box) -> list[float]:
    xs = sorted({round(v[0], 1) for v in vrules})
    xs = [x for x in xs if region[0] - 2 <= x <= region[2] + 2]
    if len(xs) >= 3:
        bounds = xs
        if abs(bounds[0] - region[0]) > 2:
            bounds = [region[0], *bounds]
        if abs(bounds[-1] - region[2]) > 2:
            bounds = [*bounds, region[2]]
        return sorted(set(bounds))
    return _alignment_columns(lines, region)


def _alignment_columns(lines: list[Line], region: Box) -> list[float]:
    """Column boundaries from whitespace: x positions where no line has ink,
    consistently, across the rows of the region."""
    x0, x1 = region[0], region[2]
    nbins = max(1, int(x1 - x0))
    cover = [0] * nbins
    for ln in lines:
        for sp in ln.spans:
            a = max(0, int(sp.bbox[0] - x0))
            b = min(nbins - 1, int(sp.bbox[2] - x0))
            for i in range(a, b + 1):
                cover[i] += 1
    gaps: list[tuple[int, int]] = []
    i = 0
    while i < nbins:
        if cover[i]:
            i += 1
            continue
        j = i
        while j < nbins and not cover[j]:
            j += 1
        if j - i >= 5:
            gaps.append((i, j))
        i = j
    interior = [(a, b) for a, b in gaps if a > 2 and b < nbins - 2]
    bounds = [x0]
    for a, b in interior:
        bounds.append(x0 + (a + b) / 2)
    bounds.append(x1)
    return sorted(set(round(b, 1) for b in bounds))


def _build(region: Box, row_bounds: list[float], col_bounds: list[float],
           lines: list[Line], vrules: list[tuple[float, float, float]],
           page_index: int, modal: float, ruled: bool) -> Table:
    """Fill the grid.

    Assignment happens at *span* level, not line level. A single text line
    often crosses several cells -- always in OCR output, where one scanned row
    comes back as one line, and often in born-digital tables too -- so
    bucketing whole lines put an entire row into its first cell and lost the
    table.
    """
    table = Table(id=element_id("table", page_index,
                                [round(v, 1) for v in region],
                                len(row_bounds), len(col_bounds)),
                  bbox=region, row_bounds=row_bounds,
                  col_bounds=col_bounds, ruled=ruled)
    grid: dict[tuple[int, int], list[tuple[int, object]]] = {}
    for li, ln in enumerate(lines):
        for sp in ln.spans:
            if not sp.text.strip():
                continue
            cy = (sp.bbox[1] + sp.bbox[3]) / 2
            cx = (sp.bbox[0] + sp.bbox[2]) / 2
            r = _bucket(cy, row_bounds)
            c = _bucket(cx, col_bounds)
            if r is None or c is None:
                continue
            grid.setdefault((r, c), []).append((li, sp))

    for (r, c), items in sorted(grid.items()):
        spans = [sp for _, sp in items]
        cell_lines = _lines_from_spans(items)
        bbox = (col_bounds[c], row_bounds[r], col_bounds[c + 1], row_bounds[r + 1])
        text_box = union([sp.bbox for sp in spans]) or bbox
        span = 1
        # merged cell: no vertical rule at the next boundary within this row,
        # and the text actually crosses it
        while c + span < len(col_bounds) - 1:
            bx = col_bounds[c + span]
            has_rule = any(abs(v[0] - bx) <= 1.6 and v[1] <= row_bounds[r] + 2
                           and v[2] >= row_bounds[r + 1] - 2 for v in vrules)
            if has_rule or text_box[2] <= bx + 0.5:
                break
            span += 1
            bbox = (bbox[0], bbox[1],
                    col_bounds[min(c + span, len(col_bounds) - 1)], bbox[3])
        style = _cell_style(cell_lines, modal)
        text = " ".join(ln.text.strip() for ln in cell_lines if ln.text.strip())

        blk = TextBlock(id=element_id(table.id, r, c, text[:60]),
                        type=ElementType.TABLE_CELL,
                        bbox=bbox, lines=cell_lines, style=style,
                        table_id=table.id, row=r, col=c, col_span=span,
                        source_page=page_index)
        blk.numeric = bool(NUMERIC_RE.match(text)) and any(ch.isdigit()
                                                          for ch in text)
        table.cells.append(blk)
    # header rows: leading rows whose cells are all bold
    header = 0
    for r in range(len(row_bounds) - 1):
        cells = [c for c in table.cells if c.row == r]
        if cells and all(c.style.bold for c in cells):
            header = r + 1
        else:
            break
    table.header_rows = max(1, header)
    return table


def _lines_from_spans(items: list[tuple[int, object]]) -> list[Line]:
    """Rebuild one Line per source line from the spans landing in a cell."""
    by_line: dict[int, list] = {}
    for li, sp in items:
        by_line.setdefault(li, []).append(sp)
    out: list[Line] = []
    for li in sorted(by_line):
        spans = sorted(by_line[li], key=lambda s: s.bbox[0])
        out.append(Line(spans=spans, bbox=union([s.bbox for s in spans]) or
                        (0.0, 0.0, 0.0, 0.0)))
    return out


def _bucket(v: float, bounds: list[float]) -> int | None:
    for i in range(len(bounds) - 1):
        if bounds[i] - 1.0 <= v < bounds[i + 1] + 1.0:
            return i
    return None


def _cell_style(lines: list[Line], modal: float) -> BlockStyle:
    spans = [sp for ln in lines for sp in ln.spans if sp.text.strip()]
    if not spans:
        return BlockStyle(size=modal)
    ref = max(spans, key=lambda s: len(s.text))
    return BlockStyle(font=ref.font, size=ref.size, bold=ref.bold,
                      italic=ref.italic, mono=ref.mono, color=ref.rgb,
                      line_height=ref.size * 1.15, leading=1.15)


def _borderless(lines: list[Line], page_index: int, modal: float,
                page_width: float) -> Table | None:
    """Alignment-only fallback (spec): at least three consecutive rows sharing
    at least two consistent interior x-boundaries.

    Two boundaries -- not one. A single shared boundary is what a two-column
    page looks like, and calling that a table would turn every paper into a
    grid of nonsense.
    """
    rows = _text_rows([ln for ln in lines if ln.text.strip()])
    if len(rows) < 3:
        return None
    row_data: list[tuple[float, float, list[float]]] = []
    for a, b in rows:
        row_lines = [ln for ln in lines
                     if ln.bbox[1] >= a - 1 and ln.bbox[3] <= b + 1 and ln.text.strip()]
        clusters = _x_clusters(row_lines)
        if len(clusters) < 3:            # >= 3 cells => >= 2 interior boundaries
            row_data.append((a, b, []))
            continue
        mids = [(clusters[i][1] + clusters[i + 1][0]) / 2
                for i in range(len(clusters) - 1)]
        row_data.append((a, b, mids))

    # Maximal runs of consecutive candidate rows
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(row_data):
        if not row_data[i][2]:
            i += 1
            continue
        j = i
        while j + 1 < len(row_data) and row_data[j + 1][2]:
            gap = row_data[j + 1][0] - row_data[j][1]
            row_h = max(4.0, row_data[j][1] - row_data[j][0])
            if gap > 2.2 * row_h:
                break
            j += 1
        if j - i + 1 >= 3:
            runs.append((i, j))
        i = j + 1

    best: tuple[int, int, list[float]] | None = None
    for i, j in runs:
        rows_in = row_data[i:j + 1]
        boundaries = _supported_boundaries([r[2] for r in rows_in])
        if len(boundaries) < 2:
            continue
        if best is None or (j - i) > (best[1] - best[0]):
            best = (i, j, boundaries)

    if best is None:
        return None
    i, j, shared = best
    band = (row_data[i][0], row_data[j][1])
    inband = [ln for ln in lines if ln.bbox[1] >= band[0] - 1
              and ln.bbox[3] <= band[1] + 1 and ln.text.strip()]
    region = union([ln.bbox for ln in inband])
    if region is None or width(region) < 0.25 * page_width:
        return None
    cols = sorted({round(region[0], 1), *(round(m, 1) for m in shared),
                   round(region[2], 1)})
    rows_b = row_bounds_from_spans(inband, region)
    if len(rows_b) < 4 or len(cols) < 3:
        return None
    return _build(region, sorted(set(rows_b)), cols, inband, [], page_index,
                  modal, ruled=False)


def _supported_boundaries(per_row: list[list[float]], tol: float = 8.0,
                          support: float = 0.6) -> list[float]:
    """Cluster candidate column boundaries across rows and keep the ones most
    rows agree on.

    Pairwise equality within a few points is too brittle for OCR output, where
    a column boundary wanders with the width of the text in the cell above it.
    Clustering with support counting recovers all five columns of a scanned
    invoice where pairwise matching found two.
    """
    flat = sorted(m for row in per_row for m in row)
    if not flat:
        return []
    clusters: list[list[float]] = [[flat[0]]]
    for m in flat[1:]:
        if m - clusters[-1][-1] <= tol:
            clusters[-1].append(m)
        else:
            clusters.append([m])
    needed = max(2, int(round(support * len(per_row))))
    out = [sum(c) / len(c) for c in clusters if len(c) >= needed]
    return out


def _x_clusters(lines: list[Line], min_gap: float = 8.0
                ) -> list[tuple[float, float]]:
    """Horizontal ink clusters in one row -- candidate cells."""
    spans = sorted(((sp.bbox[0], sp.bbox[2]) for ln in lines for sp in ln.spans
                    if sp.text.strip()))
    out: list[list[float]] = []
    for x0, x1 in spans:
        if out and x0 - out[-1][1] < min_gap:
            out[-1][1] = max(out[-1][1], x1)
        else:
            out.append([x0, x1])
    return [(a, b) for a, b in out]
