"""Tesseract OCR with deskew and binarisation (§4.6).

Pipeline: render the page at the *native* resolution of its raster (falling
back to 300 DPI), estimate skew with a Hough transform, rotate, read with PSM 3
and TSV output so every word arrives with a bounding box and a confidence, and
re-read a Sauvola-binarised copy when the first pass comes back weak.

Two of those choices were measured rather than assumed, on the bundled scanned
invoice:

* Rendering a 200 DPI scan at 300 DPI *lowers* mean confidence from 90 to 74 --
  interpolated edges are worse for Tesseract than honest pixels. So the native
  raster resolution is used when it can be determined.
* Sauvola binarisation also lowers confidence on a clean scan (88 vs 90), but
  it is what rescues a low-contrast one, so it runs as a second pass and the
  better-scoring result wins.

Coordinates stay in *deskewed* space, and `OcrPage.angle_deg` records how to
get back. That matters more than it sounds: on a page skewed by one degree, a
table row drifts nine points of height across its width, so grouping rows in
original-page space assigns the left-hand cells of one row to the row above.
Structure is therefore recovered on the straightened page, and the resulting
boxes are rotated back only once the layout is known (see
PdfParser._parse_scanned).

OCR error is the largest error source in the whole pipeline, so confidences are
kept per word, averaged per block, and surfaced -- never laundered into a
confident-looking translation.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pymupdf as fitz

from backend.config import settings
from backend.ocr.base import OcrPage, OcrWord
from backend.utils.errors import OcrUnavailable

TESS_LANG = {"en": "eng", "de": "deu", "fr": "fra", "es": "spa", "hi": "hin",
             "ar": "ara", "ja": "jpn", "zh": "chi_sim"}
MIN_WORD_CONF = 0.0          # keep everything; filtering happens downstream


class TesseractEngine:
    name = "tesseract"

    def __init__(self):
        self._langs: list[str] | None = None

    # ------------------------------------------------------------------
    def available(self) -> bool:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def languages(self) -> list[str]:
        if self._langs is None:
            try:
                import pytesseract
                self._langs = list(pytesseract.get_languages(config=""))
            except Exception:
                self._langs = []
        return self._langs

    def resolve_lang(self, lang: str) -> str:
        want = TESS_LANG.get((lang or "en").lower()[:2], "eng")
        have = self.languages()
        if want in have:
            return want
        if "eng" in have:
            return "eng"
        if have:
            return have[0]
        raise OcrUnavailable(
            "Tesseract has no language data installed. Install "
            "tesseract-ocr-eng (and the packs for your source languages).")

    # ------------------------------------------------------------------
    def read_page(self, page: fitz.Page, *, lang: str = "en",
                  dpi: int | None = None) -> OcrPage:
        if not self.available():
            raise OcrUnavailable(
                "Tesseract is not installed or not on PATH; scanned pages "
                "cannot be read. Install tesseract-ocr to enable the OCR path.")
        import pytesseract
        from PIL import Image

        dpi = dpi or native_dpi(page) or settings.render_dpi_ocr
        t0 = time.perf_counter()
        tess_lang = self.resolve_lang(lang)
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        grey = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height,
                                                                 pix.width)
        angle = estimate_skew(grey)
        # `estimate_skew` reports the rotation that straightens the page, so it
        # is applied as given. Getting this sign wrong doubles the skew instead
        # of removing it, which is invisible in the OCR text and catastrophic
        # for table structure -- verified by re-estimating the residual skew,
        # which is 0.00 with this sign and 2.28 with the other.
        work = rotate(grey, angle) if abs(angle) > 0.08 else grey

        def read(image: np.ndarray) -> dict:
            try:
                return pytesseract.image_to_data(
                    Image.fromarray(image), lang=tess_lang, config="--psm 3",
                    output_type=pytesseract.Output.DICT)
            except Exception as exc:
                raise OcrUnavailable(f"Tesseract failed on this page: {exc}",
                                     retryable=True)

        tsv = read(work)
        if _mean_conf(tsv) < settings.ocr_min_confidence:
            alt = read(binarise(work))
            if _mean_conf(alt) > _mean_conf(tsv):
                tsv = alt

        scale = 72.0 / dpi
        words: list[OcrWord] = []
        confs: list[float] = []
        n = len(tsv.get("text", []))
        for i in range(n):
            text = (tsv["text"][i] or "").strip()
            if not text:
                continue
            try:
                conf = float(tsv["conf"][i])
            except (TypeError, ValueError):
                conf = -1.0
            if conf < MIN_WORD_CONF:
                continue
            x, y = float(tsv["left"][i]), float(tsv["top"][i])
            bw, bh = float(tsv["width"][i]), float(tsv["height"][i])
            bbox = (x * scale, y * scale, (x + bw) * scale, (y + bh) * scale)
            words.append(OcrWord(
                text=text, bbox=bbox, confidence=conf,
                line_id=(int(tsv["block_num"][i]), int(tsv["par_num"][i]),
                         int(tsv["line_num"][i]))))
            confs.append(conf)
        return OcrPage(words=words, angle_deg=angle, dpi=dpi,
                       language=tess_lang,
                       mean_confidence=float(np.mean(confs)) if confs else 0.0,
                       ms=int((time.perf_counter() - t0) * 1000))


def _mean_conf(tsv: dict) -> float:
    vals = [float(c) for c, t in zip(tsv.get("conf", []), tsv.get("text", []))
            if str(t).strip() and float(c) >= 0]
    return float(np.mean(vals)) if vals else 0.0


def native_dpi(page: fitz.Page, lo: int = 150, hi: int = 400) -> int | None:
    """The scan's own resolution, from the pixel size of its largest image.

    Re-sampling a scan to a rounder number throws away the only real
    information on the page; when the raster covers most of the sheet its pixel
    grid *is* the correct rendering resolution.
    """
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return None
    best = None
    page_area = max(1.0, page.rect.width * page.rect.height)
    for info in infos:
        bbox = fitz.Rect(info["bbox"])
        if bbox.get_area() < 0.4 * page_area:
            continue
        width_in = max(0.01, bbox.width / 72.0)
        dpi = int(round(info["width"] / width_in))
        if best is None or dpi > best:
            best = dpi
    if best is None:
        return None
    return max(lo, min(hi, best))


# ---------------------------------------------------------------- imaging

def estimate_skew(grey: np.ndarray, max_deg: float = 6.0) -> float:
    """Skew angle in degrees, positive = content rotated clockwise.

    Hough on Canny edges, restricted to near-horizontal lines: text baselines
    and table rules dominate that band, and their common angle is the skew.
    """
    try:
        from skimage.feature import canny
        from skimage.transform import hough_line, hough_line_peaks
    except Exception:                       # pragma: no cover
        return 0.0
    small = grey[::2, ::2]
    edges = canny(small.astype(float) / 255.0, sigma=2.0)
    if not edges.any():
        return 0.0
    span = math.radians(max_deg)
    angles = np.linspace(np.pi / 2 - span, np.pi / 2 + span, 241)
    hspace, theta, dist = hough_line(edges, theta=angles)
    try:
        _, peak_theta, _ = hough_line_peaks(hspace, theta, dist,
                                            num_peaks=40, threshold=0.25 *
                                            float(hspace.max()))
    except Exception:                       # pragma: no cover
        return 0.0
    if len(peak_theta) == 0:
        return 0.0
    deg = np.degrees(np.median(peak_theta) - np.pi / 2)
    if not np.isfinite(deg) or abs(deg) > max_deg:
        return 0.0
    return float(deg)


def rotate(grey: np.ndarray, deg: float) -> np.ndarray:
    from skimage.transform import rotate as sk_rotate
    out = sk_rotate(grey, deg, resize=False, mode="edge", preserve_range=True)
    return out.astype(np.uint8)


def rotate_point(x: float, y: float, w: int, h: int, deg: float
                 ) -> tuple[float, float]:
    """Rotate a point about the centre of a w x h raster by `deg` degrees."""
    rad = math.radians(deg)
    cx, cy = w / 2.0, h / 2.0
    dx, dy = x - cx, y - cy
    return (cx + dx * math.cos(rad) - dy * math.sin(rad),
            cy + dx * math.sin(rad) + dy * math.cos(rad))


def binarise(grey: np.ndarray) -> np.ndarray:
    """Sauvola local threshold; falls back to a fixed cut if unavailable."""
    try:
        from skimage.filters import threshold_sauvola
        window = max(15, (min(grey.shape) // 60) | 1)
        thresh = threshold_sauvola(grey, window_size=window, k=0.2)
        return np.where(grey > thresh, np.uint8(255), np.uint8(0))
    except Exception:                       # pragma: no cover
        return np.where(grey > 160, np.uint8(255), np.uint8(0))


_engine: TesseractEngine | None = None


def default_engine() -> TesseractEngine:
    global _engine
    if _engine is None:
        _engine = TesseractEngine()
    return _engine
