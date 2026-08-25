#!/usr/bin/env python3
"""Generate the four bundled sample PDFs into sample-data/ (§10).

Content is fictional: no real agency, person, company or logo appears. Each
sample is paired with a `.expected.json` recording what the generator
*authored* -- page geometry, element counts by type, source language. Those
files are ground truth for the parser tests, written by the producer rather
than derived from the parser, so a parser regression cannot quietly rewrite its
own exam.

Every sample targets a specific weakness of the reconstruction engine:

  govt-notice.pdf      Hindi source, vector seal, footer page numbers
  research-paper.pdf   two columns, table, figure caption, footnotes, citations
  technical-report.pdf German compounds, merged table cells, code block, lists
  scanned-invoice.pdf  no text layer at all -- forces OCR + inpainting
"""
from __future__ import annotations

import json
import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pymupdf as fitz  # noqa: E402
from PIL import Image  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.utils.io import save_pdf, write_bytes  # noqa: E402

OUT = Path(settings.sample_dir)
FONTS = Path(settings.fonts_dir)
A4 = (595.0, 842.0)
LETTER = (612.0, 792.0)
INK = (0.11, 0.12, 0.14)
GREY = (0.42, 0.44, 0.47)
RULE = (0.62, 0.64, 0.67)


@dataclass
class Authored:
    """What the generator put on the page."""
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, kind: str, n: int = 1) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + n


class Sheet:
    """Thin drawing helper that also tallies authored elements."""

    def __init__(self, size=A4):
        self.doc = fitz.open()
        self.size = size
        self.authored = Authored()
        self._registered: set[tuple[int, str]] = set()

    # -- fonts
    def font(self, page: fitz.Page, key: str) -> str:
        """`key` is either a builtin PyMuPDF alias or a vendored file stem."""
        builtin = {"helv", "hebo", "heit", "hebi", "tiro", "tibo", "tiit", "tibi",
                   "cour", "cobo", "coit"}
        if key in builtin:
            return key
        alias = f"s-{key.lower()}"
        tag = (page.number, alias)
        if tag not in self._registered:
            page.insert_font(fontname=alias, fontfile=str(FONTS / f"{key}.ttf"))
            self._registered.add(tag)
        return alias

    def new_page(self) -> fitz.Page:
        return self.doc.new_page(width=self.size[0], height=self.size[1])

    # -- text
    def block(self, page, rect, text, *, font="helv", size=10.0, leading=1.32,
              align=fitz.TEXT_ALIGN_LEFT, color=INK, kind="paragraph",
              tally=True) -> float:
        fname = self.font(page, font)
        # Devanagari and CJK need more vertical room than a Latin line of the
        # same point size; rather than hand-tuning every rect, grow the box
        # downward in small steps until the writer accepts it.
        x0, y0, x1, y1 = rect
        limit = y1 + 0.6 * (y1 - y0)
        rc = -1.0
        while True:
            rc = page.insert_textbox(fitz.Rect(x0, y0, x1, y1), text,
                                     fontname=fname, fontsize=size,
                                     lineheight=leading, align=align, color=color)
            if rc >= 0 or y1 >= limit:
                break
            y1 = min(limit, y1 + 4.0)
        if rc < 0:
            raise RuntimeError(f"sample text did not fit in {rect}: {text[:40]!r}")
        rect = (x0, y0, x1, y1)
        if tally:
            self.authored.add(kind)
        return rect[3] - rc          # y of the last baseline area

    def line_of(self, page, xy, text, *, font="helv", size=9.0, color=INK,
               kind=None) -> None:
        page.insert_text(fitz.Point(*xy), text, fontname=self.font(page, font),
                         fontsize=size, color=color)
        if kind:
            self.authored.add(kind)

    def rule(self, page, x0, y, x1, w=0.7, color=RULE) -> None:
        page.draw_line(fitz.Point(x0, y), fitz.Point(x1, y), color=color, width=w)

    def save(self, path: Path, expected: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        save_pdf(self.doc, path)
        sizes = [[round(p.rect.width, 2), round(p.rect.height, 2)]
                 for p in self.doc]
        expected = dict(expected)
        expected.update(page_count=self.doc.page_count, page_sizes=sizes,
                        authored_elements=self.authored.counts)
        write_bytes(path.with_suffix(".expected.json"),
                    json.dumps(expected, indent=2,
                               ensure_ascii=False).encode("utf-8"))
        self.doc.close()
        print(f"[samples] {path.name}: {expected['page_count']}pp "
              f"{self.authored.counts}")


# --------------------------------------------------------------- sample 1
HINDI_CLAUSES = [
    "यह अधिसूचना राज्य के समस्त अधिसूचित औद्योगिक क्षेत्रों में जल संरक्षण उपायों "
    "के अनुपालन से संबंधित है। प्रत्येक इकाई को निर्धारित प्रपत्र में त्रैमासिक "
    "विवरण प्रस्तुत करना अनिवार्य होगा।",
    "अनुपालन प्रतिवेदन प्रत्येक तिमाही की समाप्ति के 30 दिनों के भीतर संबंधित "
    "क्षेत्रीय कार्यालय में जमा किया जाएगा। विलंब की स्थिति में प्रति सप्ताह "
    "₹2,500 का शुल्क देय होगा।",
    "जल पुनर्चक्रण संयंत्र की क्षमता कुल खपत के 40% से कम नहीं होनी चाहिए। "
    "माप उपकरणों का अंशांकन प्रमाणपत्र निरीक्षण के समय प्रस्तुत करना होगा।",
    "इस अधिसूचना के प्रावधानों का उल्लंघन करने पर अधिनियम की धारा 17(2) के "
    "अंतर्गत कार्यवाही की जाएगी। अपील प्राधिकरण का निर्णय अंतिम होगा।",
    "यह आदेश दिनांक 01-04-2026 से प्रभावी होगा और अगली अधिसूचना तक लागू रहेगा। "
    "स्पष्टीकरण के लिए helpdesk@jalpradhikaran.example पर संपर्क करें।",
]


def govt_notice() -> None:
    s = Sheet(A4)
    dev, devb = "NotoSansDevanagari-Regular", "NotoSansDevanagari-Bold"
    for pno in (1, 2):
        page = s.new_page()
        # letterhead: vector seal (concentric circles + radial ticks + star)
        cx, cy, r = 78, 74, 26
        page.draw_circle(fitz.Point(cx, cy), r, color=INK, width=1.1)
        page.draw_circle(fitz.Point(cx, cy), r - 5, color=INK, width=0.5)
        for i in range(24):
            a = i * math.pi / 12
            page.draw_line(fitz.Point(cx + (r - 5) * math.cos(a),
                                      cy + (r - 5) * math.sin(a)),
                           fitz.Point(cx + r * math.cos(a), cy + r * math.sin(a)),
                           color=INK, width=0.4)
        for i in range(5):
            a1, a2 = i * 4 * math.pi / 5, (i * 4 + 2) * math.pi / 5
            page.draw_line(fitz.Point(cx + 12 * math.cos(a1), cy + 12 * math.sin(a1)),
                           fitz.Point(cx + 12 * math.cos(a2), cy + 12 * math.sin(a2)),
                           color=INK, width=0.6)
        # A letterhead title recurs on every page inside the top band, so the
        # spec's recurrence rule calls it a header, not a heading (§4.1).
        s.block(page, (118, 50, 545, 74), "राज्य जल संसाधन प्राधिकरण",
                font=devb, size=17, kind="header")
        # below the 8% band -> an ordinary paragraph by the same rule
        s.block(page, (118, 76, 545, 96),
                "क्षेत्रीय कार्यालय — उत्तरी मंडल · पत्र संख्या JSP/2026/1184",
                font=dev, size=9, color=GREY, kind="paragraph")
        s.rule(page, 50, 108, 545, 0.9, INK)

        if pno == 1:
            s.block(page, (50, 126, 545, 150), "अधिसूचना — जल संरक्षण अनुपालन",
                    font=devb, size=13, align=fitz.TEXT_ALIGN_CENTER,
                    kind="heading")
            s.block(page, (50, 156, 545, 176),
                    "दिनांक: 12 मार्च 2026 · संदर्भ: अधिनियम 1998, धारा 17(2)",
                    font=dev, size=9, color=GREY)
            y = 190
            for i, clause in enumerate(HINDI_CLAUSES[:3], start=1):
                s.line_of(page, (52, y + 10), f"{i}.", font=dev, size=10.5,
                          kind="list_marker")
                s.block(page, (72, y, 545, y + 92), clause, font=dev, size=10.5,
                        align=fitz.TEXT_ALIGN_JUSTIFY, kind="list_item")
                y += 100
        else:
            y = 130
            for i, clause in enumerate(HINDI_CLAUSES[3:], start=4):
                s.line_of(page, (52, y + 10), f"{i}.", font=dev, size=10.5,
                          kind="list_marker")
                s.block(page, (72, y, 545, y + 92), clause, font=dev, size=10.5,
                        align=fitz.TEXT_ALIGN_JUSTIFY, kind="list_item")
                y += 100
            # signature block
            s.rule(page, 360, y + 46, 545, 0.6)
            s.block(page, (360, y + 50, 545, y + 96),
                    "अधिशासी अभियंता\nराज्य जल संसाधन प्राधिकरण",
                    font=dev, size=10, kind="signature")
        # footer (recurs on every page -> header/footer detection)
        s.rule(page, 50, 792, 545, 0.5)
        s.block(page, (50, 798, 545, 816),
                f"पृष्ठ {pno} / 2 · JSP/2026/1184", font=dev, size=8,
                color=GREY, align=fitz.TEXT_ALIGN_CENTER, kind="footer")
    s.save(OUT / "govt-notice.pdf",
           {"source_lang": "hi", "notes": "vector seal must survive redaction (I3)"})


# --------------------------------------------------------------- sample 2
ABSTRACT = (
    "We report a controlled evaluation of layout-preserving document translation "
    "across five target languages. Our method reconstructs each text block in "
    "place, measured against a pixel-level fidelity criterion, and reports the "
    "typographic concession made whenever translated text does not fit the "
    "original frame. On a corpus of 412 synthetic pages the approach preserves "
    "graphics fidelity at 0.994 masked SSIM while leaving 2.1% of blocks flagged "
    "for human review [3], [7]."
)
BODY = [
    "Machine translation of documents is routinely evaluated on plain text, yet "
    "the artefact a reader receives is a page. A page carries information in its "
    "geometry: column boundaries signal reading order, indentation signals "
    "hierarchy, and proximity binds a caption to the figure it describes [1]. "
    "Translating the words while discarding that geometry produces a document "
    "that is technically correct and practically unusable.",
    "Prior systems fall into two families. Reflow-based tools extract text, "
    "translate it, and re-typeset the result, which reliably destroys the "
    "original design. Overlay tools paint translated strings on top of the "
    "source page, which preserves geometry but clips any string that grew, and "
    "German targets grow by roughly a third [2].",
    "Our contribution is narrower and, we argue, more useful: a reconstruction "
    "loop that erases only the text layer of a block, then re-places the "
    "translated string in the same rectangle under an explicit ladder of "
    "typographic concessions, recording which rung was required. The ladder is "
    "ordered by perceptual cost, so the cheapest change that fits is the change "
    "that is made [4], [5].",
    "Section 3 defines the fit ladder and its bounds. Section 4 describes the "
    "protection scheme that keeps numbers, identifiers and citations verbatim "
    "through the translation step. Section 5 reports measured fidelity on the "
    "evaluation corpus, and Section 6 discusses the failure modes we could not "
    "eliminate, chiefly font substitution on branded material.",
    "The evaluation corpus consists of synthetic pages generated with known "
    "ground truth, which lets us separate parsing error from translation error. "
    "Each page records its authored element inventory, so a parse can be scored "
    "directly rather than judged by inspection [6].",
    "Table 1 reports fidelity by target language. Contraction (Chinese, "
    "Japanese) is uniformly easier than expansion (German, French): a shorter "
    "string always fits, so the ladder is never entered, and the residual error "
    "is confined to line-breaking quality inside the original frame.",
    "Expansion behaves differently. The mean concession rises with the "
    "expansion ratio, and the tail matters more than the mean: a single clipped "
    "clause in a legal document is worse than a page set 4% smaller. We "
    "therefore report the worst rung reached per page alongside the mean.",
    "Threats to validity: synthetic pages under-represent the designed layouts "
    "found in magazines and marketing material, where text frames are irregular "
    "and often overlap artwork. We expect materially worse reflow there and say "
    "so rather than extrapolating from the corpus we measured.",
]


def research_paper() -> None:
    s = Sheet(LETTER)
    L, R = (54, 300), (312, 558)          # column x-ranges
    body_i = 0
    for pno in range(6):
        page = s.new_page()
        s.line_of(page, (54, 34), "Proceedings of the Workshop on Document "
                  "Reconstruction, 2026", font="tiro", size=7.5, color=GREY,
                  kind="header")
        s.line_of(page, (500, 34), f"{11 + pno}", font="tiro", size=7.5,
                  color=GREY, kind="page_number")
        s.rule(page, 54, 40, 558, 0.4)
        top = 60
        if pno == 0:
            s.block(page, (54, 56, 558, 96),
                    "Measured Layout Preservation in Document Translation",
                    font="tibo", size=17, align=fitz.TEXT_ALIGN_CENTER,
                    kind="heading")
            s.block(page, (54, 100, 558, 118),
                    "A. Verma, L. Okafor, M. Reinholt · Institute for Document "
                    "Systems", font="tiro", size=9,
                    align=fitz.TEXT_ALIGN_CENTER)
            s.block(page, (96, 126, 516, 138), "Abstract", font="tibo", size=10,
                    align=fitz.TEXT_ALIGN_CENTER, kind="heading")
            s.block(page, (96, 140, 516, 214), ABSTRACT, font="tiro", size=9.2,
                    align=fitz.TEXT_ALIGN_JUSTIFY)
            top = 232
        for cx0, cx1 in (L, R):
            y = top
            if pno == 0 and (cx0, cx1) == L:
                s.block(page, (cx0, y, cx1, y + 14), "1  Introduction",
                        font="tibo", size=11, kind="heading")
                y += 22
            if pno == 1 and (cx0, cx1) == L:
                s.block(page, (cx0, y, cx1, y + 14), "3  The fit ladder",
                        font="tibo", size=11, kind="heading")
                y += 22
                # a displayed equation, in italics, on its own line
                s.block(page, (cx0, y, cx1, y + 26),
                        "cost(r) = w_r · area(b) / area(page),   w = "
                        "(0,1,2,3,5,8,13)", font="tiit", size=9.5,
                        align=fitz.TEXT_ALIGN_CENTER, kind="equation")
                y += 34
            if pno == 3 and (cx0, cx1) == L:
                # figure: vector artwork + caption below (caption detection)
                fr = fitz.Rect(cx0, y, cx1, y + 120)
                page.draw_rect(fr, color=RULE, width=0.6)
                for i, h in enumerate((0.24, 0.51, 0.68, 0.83, 0.95)):
                    bx = fitz.Rect(cx0 + 18 + i * 46, fr.y1 - 12 - h * 92,
                                   cx0 + 52 + i * 46, fr.y1 - 12)
                    page.draw_rect(bx, color=None, fill=(0.55, 0.58, 0.62))
                s.authored.add("figure")
                y += 126
                s.block(page, (cx0, y, cx1, y + 26),
                        "Figure 1: Mean concession by target language, mock "
                        "provider, n = 412 pages.", font="tiit", size=8,
                        kind="caption")
                y += 34
            if pno == 4 and (cx0, cx1) == R:
                y = _results_table(s, page, cx0, cx1, y)
            while y < 700:
                # the corpus is short; cycle it so all six pages carry text
                para = BODY[body_i % len(BODY)]
                h = 12 + 5.6 * len(para) / (cx1 - cx0) * 10
                h = min(h, 700 - y)
                if h < 40:
                    break
                s.block(page, (cx0, y, cx1, y + h), para, font="tiro", size=9.4,
                        align=fitz.TEXT_ALIGN_JUSTIFY)
                y += h + 8
                body_i += 1
            if pno == 5 and (cx0, cx1) == R:
                s.rule(page, cx0, y + 6, cx0 + 90, 0.5)
                s.block(page, (cx0, y + 10, cx1, y + 46),
                        "1. Ground truth for the corpus is published with the "
                        "generator script.\n2. Ratios measured on in-domain "
                        "prose, not headlines.", font="tiro", size=7.6,
                        color=GREY, kind="footnote")
    s.save(OUT / "research-paper.pdf",
           {"source_lang": "en", "columns": 2,
            "notes": "two-column reading order and table structure"})


def _results_table(s: Sheet, page, x0: float, x1: float, y: float) -> float:
    rows = [("Target", "Ratio", "SSIM", "Rung>3", "Flag %"),
            ("German", "1.32", "0.993", "38", "2.9"),
            ("French", "1.22", "0.994", "21", "1.8"),
            ("Hindi", "1.18", "0.995", "17", "1.5"),
            ("Japanese", "0.68", "0.997", "2", "0.4")]
    colw = [(x1 - x0) * f for f in (0.34, 0.16, 0.18, 0.16, 0.16)]
    rh = 15.5
    top = y
    for ri, row in enumerate(rows):
        cy = top + ri * rh
        cx = x0
        for ci, cell in enumerate(row):
            fnt = "tibo" if ri == 0 else "tiro"
            align = fitz.TEXT_ALIGN_LEFT if ci == 0 else fitz.TEXT_ALIGN_RIGHT
            s.block(page, (cx + 2, cy + 2.5, cx + colw[ci] - 2, cy + rh - 1),
                    cell, font=fnt, size=8.4, align=align, leading=1.0,
                    kind="table_cell")
            cx += colw[ci]
        if ri in (0, len(rows) - 1):
            s.rule(page, x0, cy + rh, x1, 0.8, INK)
    s.rule(page, x0, top, x1, 0.8, INK)
    s.authored.add("table")
    y = top + len(rows) * rh + 4
    s.block(page, (x0, y, x1, y + 24),
            "Table 1: Fidelity by target language. Ratio is mean character "
            "expansion.", font="tiit", size=8, kind="caption")
    return y + 32


# --------------------------------------------------------------- sample 3
GERMAN = {
    "title": "Technischer Zwischenbericht — Bestandsdatenverwaltung",
    "h1": "1  Ausgangslage und Aufgabenstellung",
    "h2": "2  Schnittstellenbeschreibung",
    "h3": "3  Messergebnisse",
    "p1": ("Die Bestandsdatenverwaltungsschnittstelle wurde im Berichtszeitraum "
           "vollständig überarbeitet. Wesentliche Änderungen betreffen die "
           "Zugriffsberechtigungsprüfung, die Protokollierungstiefe sowie die "
           "Rückmeldung von Verarbeitungsfehlern an das aufrufende System."),
    "p2": ("Die bisherige Umsetzung führte bei Massenverarbeitungsvorgängen zu "
           "einer Überschreitung der vereinbarten Antwortzeit. Ursächlich war "
           "eine nicht zwischengespeicherte Berechtigungsabfrage je Datensatz "
           "anstelle einer einmaligen Sammelabfrage."),
    "p3": ("Die Umstellung auf eine Sammelabfrage reduziert die mittlere "
           "Antwortzeit um 62 %. Die Fehlerbehandlung unterscheidet nunmehr "
           "zwischen fachlichen Ablehnungen und technischen Störungen, was die "
           "Nachvollziehbarkeit im Betriebsprotokoll erheblich verbessert."),
    "p4": ("Offen bleibt die Behandlung von Teilausfällen: Ein Stapel mit "
           "fehlerhaften Einzeldatensätzen wird derzeit vollständig "
           "zurückgewiesen. Eine datensatzweise Quittierung ist vorgesehen, "
           "erfordert jedoch eine Anpassung des Übergabeformats."),
}
CODE = ("POST /v2/bestand/stapel\n"
        "Content-Type: application/json\n"
        "{\n"
        '  \"mandant\": \"NORD-04\",\n'
        '  \"datensaetze\": 2500,\n'
        '  \"quittierung\": \"datensatzweise\"\n'
        "}")


def technical_report() -> None:
    s = Sheet(A4)
    reg, bold = "NotoSans-Regular", "NotoSans-Bold"
    for pno in range(4):
        page = s.new_page()
        s.line_of(page, (50, 36), "Bestandsdatenverwaltung · Zwischenbericht "
                  "4/2026", font=reg, size=7.5, color=GREY, kind="header")
        s.line_of(page, (500, 36), f"Seite {pno + 1}", font=reg, size=7.5,
                  color=GREY, kind="header")
        s.rule(page, 50, 42, 545, 0.4)
        s.rule(page, 50, 806, 545, 0.4)
        s.line_of(page, (50, 818), "Vertraulich — nur zur internen Verwendung",
                  font=reg, size=7, color=GREY, kind="footer")
        if pno == 0:
            s.block(page, (50, 62, 545, 92), GERMAN["title"], font=bold,
                    size=16, kind="heading")
            s.block(page, (50, 96, 545, 112),
                    "Verfasser: Abteilung Systemtechnik · Stand: 12.03.2026",
                    font=reg, size=8.5, color=GREY)
            s.block(page, (50, 128, 545, 144), GERMAN["h1"], font=bold, size=11.5,
                    kind="heading")
            s.block(page, (50, 150, 545, 212), GERMAN["p1"], font=reg, size=10,
                    align=fitz.TEXT_ALIGN_JUSTIFY)
            s.block(page, (50, 220, 545, 282), GERMAN["p2"], font=reg, size=10,
                    align=fitz.TEXT_ALIGN_JUSTIFY)
            # bulleted list
            y = 296
            for item in ("Sammelabfrage der Zugriffsberechtigungen",
                         "Trennung fachlicher und technischer Fehlerbilder",
                         "Erweiterte Protokollierung der Verarbeitungsdauer"):
                s.line_of(page, (54, y + 9), "•", font=reg, size=10,
                          kind="list_marker")
                s.block(page, (68, y, 545, y + 16), item, font=reg, size=10,
                        leading=1.15, kind="list_item")
                y += 18
            y = _merged_table(s, page, 50, 545, y + 14)
        elif pno == 1:
            s.block(page, (50, 62, 545, 78), GERMAN["h2"], font=bold, size=11.5,
                    kind="heading")
            # two columns
            for cx0, cx1, para in ((50, 292, GERMAN["p3"]),
                                   (303, 545, GERMAN["p4"])):
                s.block(page, (cx0, 86, cx1, 200), para, font=reg, size=9.6,
                        align=fitz.TEXT_ALIGN_JUSTIFY)
            # code block on a tinted panel, monospaced -> mono substitution path
            panel = fitz.Rect(50, 214, 545, 316)
            page.draw_rect(panel, color=RULE, fill=(0.955, 0.96, 0.965), width=0.5)
            s.block(page, (60, 222, 535, 310), CODE, font="cour", size=8.6,
                    leading=1.28, kind="code")
            y = 330
            for i, item in enumerate((
                    "Stapelgröße auf 2.500 Datensätze begrenzen.",
                    "Quittierung datensatzweise anfordern.",
                    "Wiederholungsversuche nach 30 Sekunden.")):
                s.line_of(page, (54, y + 9), f"{i + 1}.", font=reg, size=10,
                          kind="list_marker")
                s.block(page, (72, y, 545, y + 16), item, font=reg, size=10,
                        leading=1.15, kind="list_item")
                y += 18
            _plain_table(s, page, 50, 545, y + 16,
                         header=("Feld", "Typ", "Pflicht", "Bemerkung"),
                         rows=[("mandant", "string(8)", "ja", "Mandantenkürzel"),
                               ("datensaetze", "integer", "ja", "max. 2.500"),
                               ("quittierung", "enum", "nein", "Standard: stapelweise"),
                               ("zeitstempel", "ISO-8601", "ja", "UTC")])
        elif pno == 2:
            s.block(page, (50, 62, 545, 78), GERMAN["h3"], font=bold, size=11.5,
                    kind="heading")
            s.block(page, (50, 86, 545, 150),
                    GERMAN["p3"] + " " + GERMAN["p2"], font=reg, size=10,
                    align=fitz.TEXT_ALIGN_JUSTIFY)
            _plain_table(s, page, 50, 545, 168,
                         header=("Vorgang", "vorher (ms)", "nachher (ms)",
                                 "Änderung"),
                         rows=[("Einzelabfrage", "148", "132", "-11 %"),
                               ("Stapel 500", "6.410", "2.480", "-61 %"),
                               ("Stapel 2.500", "31.980", "11.720", "-63 %"),
                               ("Fehlerfall", "212", "196", "-8 %")])
            s.block(page, (50, 300, 545, 364), GERMAN["p4"], font=reg, size=10,
                    align=fitz.TEXT_ALIGN_JUSTIFY)
        else:
            s.block(page, (50, 62, 545, 78), "4  Nächste Schritte", font=bold,
                    size=11.5, kind="heading")
            s.block(page, (50, 86, 545, 168), GERMAN["p1"] + " " + GERMAN["p4"],
                    font=reg, size=10, align=fitz.TEXT_ALIGN_JUSTIFY)
            s.rule(page, 360, 240, 545, 0.6)
            s.block(page, (360, 244, 545, 280),
                    "Abteilungsleitung Systemtechnik", font=reg, size=9.5,
                    kind="signature")
    s.save(OUT / "technical-report.pdf",
           {"source_lang": "de", "notes": "German compounds contract; merged "
            "cells and ruling lines must not move"})


def _plain_table(s: Sheet, page, x0, x1, y, header, rows) -> float:
    colw = [(x1 - x0) * f for f in (0.28, 0.2, 0.16, 0.36)]
    rh = 18.0
    page.draw_rect(fitz.Rect(x0, y, x1, y + rh), color=None,
                   fill=(0.93, 0.94, 0.95))
    for ri, row in enumerate([header, *rows]):
        cy = y + ri * rh
        cx = x0
        for ci, cell in enumerate(row):
            s.block(page, (cx + 4, cy + 4, cx + colw[ci] - 3, cy + rh - 2),
                    str(cell), font="NotoSans-Bold" if ri == 0 else
                    "NotoSans-Regular", size=8.8, leading=1.0, kind="table_cell")
            cx += colw[ci]
        s.rule(page, x0, cy, x1, 0.5)
    bottom = y + (len(rows) + 1) * rh
    s.rule(page, x0, bottom, x1, 0.8, INK)
    cx = x0
    for w in colw[:-1]:
        cx += w
        page.draw_line(fitz.Point(cx, y), fitz.Point(cx, bottom), color=RULE,
                       width=0.5)
    page.draw_line(fitz.Point(x0, y), fitz.Point(x0, bottom), color=RULE, width=0.5)
    page.draw_line(fitz.Point(x1, y), fitz.Point(x1, bottom), color=RULE, width=0.5)
    s.authored.add("table")
    return bottom


def _merged_table(s: Sheet, page, x0, x1, y) -> float:
    """Two-level header: the first header cell spans two columns (merged)."""
    colw = [(x1 - x0) * f for f in (0.3, 0.22, 0.24, 0.24)]
    rh = 18.0
    # merged header cell across columns 1-2
    page.draw_rect(fitz.Rect(x0, y, x0 + colw[0] + colw[1], y + rh),
                   color=RULE, fill=(0.9, 0.92, 0.94), width=0.5)
    page.draw_rect(fitz.Rect(x0 + colw[0] + colw[1], y, x1, y + rh),
                   color=RULE, fill=(0.9, 0.92, 0.94), width=0.5)
    s.block(page, (x0 + 4, y + 4, x0 + colw[0] + colw[1] - 4, y + rh - 2),
            "Schnittstellenkennung", font="NotoSans-Bold", size=8.8,
            leading=1.0, kind="table_cell")
    s.block(page, (x0 + colw[0] + colw[1] + 4, y + 4, x1 - 4, y + rh - 2),
            "Verarbeitungsmerkmale", font="NotoSans-Bold", size=8.8,
            leading=1.0, align=fitz.TEXT_ALIGN_CENTER, kind="table_cell")
    rows = [("Kennung", "Version", "Stapelgröße", "Quittierung"),
            ("BDV-01", "2.4.0", "2.500", "datensatzweise"),
            ("BDV-02", "2.4.0", "500", "stapelweise"),
            ("BDV-09", "1.9.3", "100", "stapelweise")]
    for ri, row in enumerate(rows):
        cy = y + (ri + 1) * rh
        cx = x0
        for ci, cell in enumerate(row):
            s.block(page, (cx + 4, cy + 4, cx + colw[ci] - 3, cy + rh - 2), cell,
                    font="NotoSans-Bold" if ri == 0 else "NotoSans-Regular",
                    size=8.8, leading=1.0, kind="table_cell")
            cx += colw[ci]
        s.rule(page, x0, cy, x1, 0.5)
    bottom = y + (len(rows) + 1) * rh
    s.rule(page, x0, bottom, x1, 0.8, INK)
    cx = x0
    for w in colw[:-1]:
        cx += w
        page.draw_line(fitz.Point(cx, y + rh), fitz.Point(cx, bottom),
                       color=RULE, width=0.5)
    for xx in (x0, x1):
        page.draw_line(fitz.Point(xx, y), fitz.Point(xx, bottom), color=RULE,
                       width=0.5)
    s.authored.add("table")
    s.authored.add("merged_cell")
    return bottom


# --------------------------------------------------------------- sample 4
def scanned_invoice() -> None:
    """Render a clean invoice, then degrade it into a 200 DPI scan: skew,
    speckle, slight blur and a warm paper cast. The output PDF has no text
    layer at all, so the pipeline must take the OCR branch."""
    src = fitz.open()
    page = src.new_page(width=LETTER[0], height=LETTER[1])
    sheet = Sheet()          # only used for its font registration + tally
    sheet.doc.close()
    sheet.doc = src

    def txt(rect, text, font="helv", size=10, align=fitz.TEXT_ALIGN_LEFT,
            kind="paragraph", color=INK):
        sheet.block(page, rect, text, font=font, size=size, align=align,
                    kind=kind, color=color)

    txt((54, 54, 320, 84), "NORTHGATE SUPPLY CO.", font="hebo", size=16,
        kind="heading")
    txt((54, 86, 320, 132), "42 Kiln Row, Dunmore Industrial Park\n"
        "VAT 88-2213-04 · accounts@northgate.example", size=8.6, color=GREY)
    txt((360, 54, 558, 84), "INVOICE", font="hebo", size=20,
        align=fitz.TEXT_ALIGN_RIGHT, kind="heading")
    txt((360, 88, 558, 140), "Invoice no.  NG-2026-0417\n"
        "Date         14 February 2026\nTerms        Net 30\n"
        "Order ref.   PO-77812", size=8.8, align=fitz.TEXT_ALIGN_RIGHT)
    txt((54, 150, 320, 200), "Billed to:\nHarrow & Vale Fabrication\n"
        "Unit 6, Lowfield Trading Estate\nBrackenhurst BR7 2QD", size=9)

    rows = [("Item", "Description", "Qty", "Unit", "Amount"),
            ("A-1180", "Cold-rolled steel sheet, 1.2 mm", "40", "18.50", "740.00"),
            ("A-1184", "Cold-rolled steel sheet, 2.0 mm", "25", "24.10", "602.50"),
            ("F-0221", "Zinc-plated fasteners, box of 500", "12", "9.75", "117.00"),
            ("S-0044", "Cutting service, per linear metre", "86", "3.40", "292.40"),
            ("D-0007", "Delivery, two pallets", "1", "48.00", "48.00")]
    colw = [66, 232, 40, 58, 68]
    y0 = 224
    rh = 20.0
    x0 = 54
    for ri, row in enumerate(rows):
        cy = y0 + ri * rh
        cx = x0
        for ci, cell in enumerate(row):
            align = (fitz.TEXT_ALIGN_RIGHT if ci >= 2 else fitz.TEXT_ALIGN_LEFT)
            sheet.block(page, (cx + 4, cy + 5, cx + colw[ci] - 4, cy + rh - 2),
                        cell, font="hebo" if ri == 0 else "helv", size=8.8,
                        leading=1.0, align=align, kind="table_cell")
            cx += colw[ci]
        page.draw_line(fitz.Point(x0, cy), fitz.Point(x0 + sum(colw), cy),
                       color=(0.3, 0.3, 0.3), width=0.6)
    bottom = y0 + len(rows) * rh
    page.draw_line(fitz.Point(x0, bottom), fitz.Point(x0 + sum(colw), bottom),
                   color=(0.2, 0.2, 0.2), width=0.9)
    cx = x0
    for w in [0, *colw]:
        cx += w
        page.draw_line(fitz.Point(cx, y0), fitz.Point(cx, bottom),
                       color=(0.35, 0.35, 0.35), width=0.6)
    sheet.authored.add("table")
    txt((330, bottom + 10, 558, bottom + 74),
        "Subtotal      1,799.90\nVAT 20%         359.98\n"
        "Total due     2,159.88", size=9.4, align=fitz.TEXT_ALIGN_RIGHT)
    txt((54, bottom + 96, 558, bottom + 130),
        "Payment within 30 days to Northgate Supply Co., account 4471 0092, "
        "sort 20-14-88. Late payment attracts interest at 4% above base rate.",
        size=8.4, color=GREY)

    # --- degrade into a scan
    pix = page.get_pixmap(dpi=200)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    rng = np.random.default_rng(20260214)
    img = img.rotate(-1.15, resample=Image.BICUBIC, expand=False,
                     fillcolor=(252, 251, 248))
    arr = np.asarray(img).astype(np.float32)
    arr *= np.array([1.0, 0.995, 0.965])                      # warm paper cast
    speckle = rng.normal(0.0, 7.5, arr.shape[:2])[..., None]
    arr = arr + speckle
    pepper = rng.random(arr.shape[:2]) < 0.0007
    arr[pepper] = rng.integers(20, 90, size=(pepper.sum(), 1))
    # faint horizontal scanner banding
    band = (np.sin(np.arange(arr.shape[0]) / 37.0) * 1.6)[:, None, None]
    arr = np.clip(arr + band, 0, 255).astype(np.uint8)
    scan = Image.fromarray(arr)

    out = fitz.open()
    p2 = out.new_page(width=LETTER[0], height=LETTER[1])
    import io
    buf = io.BytesIO()
    scan.save(buf, format="JPEG", quality=82)
    p2.insert_image(p2.rect, stream=buf.getvalue())
    path = OUT / "scanned-invoice.pdf"
    save_pdf(out, path)
    text_chars = len(p2.get_text().strip())
    out.close()
    src.close()
    expected = {
        "source_lang": "en", "is_scanned": True, "dpi": 200,
        "skew_deg": -1.15, "extractable_chars": text_chars,
        "page_count": 1, "page_sizes": [[LETTER[0], LETTER[1]]],
        "authored_elements": sheet.authored.counts,
        "notes": "image-only page: forces OCR + inpainting reconstruction",
    }
    write_bytes(path.with_suffix(".expected.json"),
                json.dumps(expected, indent=2).encode("utf-8"))
    print(f"[samples] {path.name}: 1pp scanned, {text_chars} extractable chars")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    random.seed(7)
    govt_notice()
    research_paper()
    technical_report()
    scanned_invoice()
    return 0


if __name__ == "__main__":
    sys.exit(main())
