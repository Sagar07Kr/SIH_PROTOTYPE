"""Inpainting for scanned pages (§4.6).

A scanned page has no text layer to redact -- the words are pixels in a
photograph. Redaction would either do nothing or destroy the raster, so each
text region is instead covered with the background colour sampled from just
outside its own bounding box, and the translation is drawn on top. Everywhere
else the scan is left exactly as it was: the paper texture, the ruled table,
the skew and the speckle all survive.

Sampling a *ring* around the box rather than a global page colour matters on
real scans, where illumination varies across the sheet and a single "white"
leaves visible patches.
"""
from __future__ import annotations

import numpy as np
import pymupdf as fitz

from backend.utils.geometry import Box

RING_PT = 3.0
SAMPLE_DPI = 110


class BackgroundSampler:
    """Caches one render of the page and answers colour queries against it."""

    def __init__(self, page: fitz.Page, dpi: int = SAMPLE_DPI):
        self.dpi = dpi
        self.scale = dpi / 72.0
        pix = page.get_pixmap(dpi=dpi)
        self.rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)[:, :, :3].astype(np.float32)

    def ring_colour(self, bbox: Box, ring_pt: float = RING_PT
                    ) -> tuple[float, float, float]:
        h, w, _ = self.rgb.shape
        x0 = int(max(0, (bbox[0] - ring_pt) * self.scale))
        y0 = int(max(0, (bbox[1] - ring_pt) * self.scale))
        x1 = int(min(w, (bbox[2] + ring_pt) * self.scale) + 1)
        y1 = int(min(h, (bbox[3] + ring_pt) * self.scale) + 1)
        ix0 = int(max(0, bbox[0] * self.scale))
        iy0 = int(max(0, bbox[1] * self.scale))
        ix1 = int(min(w, bbox[2] * self.scale) + 1)
        iy1 = int(min(h, bbox[3] * self.scale) + 1)
        if x1 <= x0 or y1 <= y0:
            return (1.0, 1.0, 1.0)
        outer = self.rgb[y0:y1, x0:x1]
        mask = np.ones(outer.shape[:2], dtype=bool)
        mask[max(0, iy0 - y0):max(0, iy1 - y0),
             max(0, ix0 - x0):max(0, ix1 - x0)] = False
        pixels = outer[mask]
        if pixels.size == 0:
            pixels = outer.reshape(-1, 3)
        # median, not mean: a ring that clips a ruling line or a neighbouring
        # glyph must not drag the fill colour grey
        med = np.median(pixels, axis=0) / 255.0
        return (float(med[0]), float(med[1]), float(med[2]))


def inpaint(page: fitz.Page, boxes: list[Box], sampler: BackgroundSampler,
            pad_pt: float = 0.8) -> list[dict]:
    """Cover each box with its local background colour. Returns the fills used
    so the audit trail can show what was painted over."""
    done: list[dict] = []
    for b in boxes:
        colour = sampler.ring_colour(b)
        rect = fitz.Rect(b[0] - pad_pt, b[1] - pad_pt, b[2] + pad_pt,
                         b[3] + pad_pt)
        if rect.is_empty:
            continue
        page.draw_rect(rect, color=None, fill=colour, width=0)
        done.append({"rect": [round(v, 2) for v in rect],
                     "fill": [round(c, 4) for c in colour]})
    return done
