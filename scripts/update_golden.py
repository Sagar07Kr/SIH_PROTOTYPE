#!/usr/bin/env python3
"""Regenerate the golden layout reference.

Prints the SSIM delta against the current reference first, so the person
running it can see what they are about to bless.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from skimage.metrics import structural_similarity as ssim  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.utils.io import write_bytes  # noqa: E402
from tests.integration.test_golden_layout import (GOLDEN_DIR, SAMPLE, TARGET,
                                                  render_translation)


def main() -> int:
    sample = Path(settings.sample_dir) / SAMPLE
    if not sample.exists():
        print(f"[golden] {sample} missing; run scripts/make_samples.py")
        return 1
    pages = render_translation(sample, TARGET)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for i, page in enumerate(pages):
        path = GOLDEN_DIR / f"page-{i:02d}.png"
        if path.exists():
            old = np.asarray(Image.open(path).convert("L"))
            if old.shape == page.shape:
                print(f"[golden] page {i + 1}: SSIM vs existing reference "
                      f"{ssim(page, old, data_range=255):.5f}")
            else:
                print(f"[golden] page {i + 1}: size changed "
                      f"{old.shape} -> {page.shape}")
        import io
        buf = io.BytesIO()
        Image.fromarray(page).save(buf, format="PNG", optimize=True)
        write_bytes(path, buf.getvalue())
    print(f"[golden] wrote {len(pages)} reference pages to {GOLDEN_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
