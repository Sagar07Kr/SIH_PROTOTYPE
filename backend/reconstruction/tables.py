"""Table reconstruction (§4.5).

Translate cell by cell; never move a ruling line. Column widths are fixed, so
the fit ladder runs inside each cell with a lower size floor (0.75x) -- readers
tolerate smaller type in a table than in prose, and the alternative is either
clipping or moving the grid.

Row growth is measured but not applied to the ruling lines: this prototype
keeps the grid exactly where the source drew it and reports the cells that
needed the extra room, which is the honest failure mode. Splitting a table
across a page boundary is not implemented; see docs/LIMITATIONS.md.
"""
from __future__ import annotations

from backend.config import settings
from backend.parsers.model import Table
from backend.reconstruction.placer import PageContext, Placement, Placer


def place_table(placer: Placer, ctx: PageContext, table: Table,
                translations: dict[str, str], target_lang: str,
                script_change: bool = True) -> list[Placement]:
    out: list[Placement] = []
    for cell in sorted(table.cells, key=lambda c: (c.row or 0, c.col or 0)):
        text = translations.get(cell.id)
        if not text or not text.strip():
            continue
        out.append(placer.place(ctx, cell, text, target_lang,
                               size_floor=settings.cell_size_floor,
                               allow_grow=False, script_change=script_change))
    return out
