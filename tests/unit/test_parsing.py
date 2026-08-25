"""Parser tests against the generator's own ground truth.

The `.expected.json` files are written by scripts/make_samples.py -- the
producer, not the parser -- so a parser regression cannot rewrite its own exam.
Two divergences are intentional and asserted as such:

* the research paper's centred bold "Abstract" heading is counted as a
  paragraph, because it is neither larger than body text nor followed by extra
  leading (the rule in §4.1 needs one of those);
* "figure" is an authored concept with no parser equivalent: vector artwork is
  reported as drawings, not as an element type.
"""
from __future__ import annotations

import pytest

from backend.parsers.classify import (is_marker_only, normalise_recurrence_key,
                                      split_marker)
from backend.parsers.model import ElementType

TOLERANCE = {
    "govt-notice": {},
    "technical-report": {},
    "research-paper": {"heading": 1},      # the "Abstract" case above
    "scanned-invoice": {"table_cell": 4, "heading": 4, "paragraph": 4,
                        "header": 2, "footer": 2},   # OCR grouping is fuzzier
}
IGNORED_TYPES = {"figure", "merged_cell", "image", "table", "list_marker",
                 "signature", "equation", "footnote", "caption", "code"}


def test_page_geometry_matches(parsed_samples, expected) -> None:
    for name, doc in parsed_samples.items():
        exp = expected[name]
        assert doc.page_count == exp["page_count"], name
        for page, (w, h) in zip(doc.pages, exp["page_sizes"]):
            assert round(page.width_pt, 1) == pytest.approx(w, abs=0.1), name
            assert round(page.height_pt, 1) == pytest.approx(h, abs=0.1), name


def test_element_counts_match_authored_ground_truth(parsed_samples, expected) -> None:
    for name, doc in parsed_samples.items():
        counts = doc.element_counts()
        authored = expected[name]["authored_elements"]
        slack = TOLERANCE[name]
        for kind, n in authored.items():
            if kind in IGNORED_TYPES:
                continue
            got = counts.get(kind, 0)
            assert abs(got - n) <= slack.get(kind, 0), \
                f"{name}: {kind} parsed {got}, authored {n}"


def test_two_column_detection(parsed_samples) -> None:
    paper = parsed_samples["research-paper"]
    assert all(len(p.columns) == 2 for p in paper.pages), \
        [len(p.columns) for p in paper.pages]
    # a table's internal whitespace must not be mistaken for a gutter
    report = parsed_samples["technical-report"]
    assert [len(p.columns) for p in report.pages] == [1, 1, 1, 1]


def test_reading_order_is_column_major(parsed_samples) -> None:
    page = parsed_samples["research-paper"].pages[1]
    body = [b for b in page.blocks if b.column_index in (0, 1)]
    order = [b.column_index for b in sorted(body, key=lambda b: b.reading_order)]
    # every left-column block precedes every right-column block in one band
    first_right = order.index(1) if 1 in order else len(order)
    assert all(c == 0 for c in order[:first_right])


def test_header_footer_recurrence(parsed_samples) -> None:
    notice = parsed_samples["govt-notice"]
    kinds = [b.type for p in notice.pages for b in p.blocks]
    assert kinds.count(ElementType.HEADER) == 2      # letterhead, both pages
    assert kinds.count(ElementType.FOOTER) == 2      # page-number footer
    report = parsed_samples["technical-report"]
    page_numbers = [b.text for p in report.pages for b in p.blocks
                    if b.type in (ElementType.HEADER, ElementType.FOOTER)]
    assert any("Seite" in t for t in page_numbers)


def test_list_markers_are_excluded_from_translatable_text(parsed_samples) -> None:
    report = parsed_samples["technical-report"]
    items = [b for p in report.pages for b in p.blocks
             if b.type is ElementType.LIST_ITEM]
    markers = [b for p in report.pages for b in p.blocks
               if b.type is ElementType.LIST_MARKER]
    assert len(items) == 6 and len(markers) == 6
    assert all(b.list_marker for b in items)
    assert all(not b.translatable for b in markers)
    assert all(not b.text.strip().startswith(("•", "1.")) for b in items)


def test_marker_helpers() -> None:
    assert split_marker("• item") == ("•", "item")
    assert split_marker("12. item")[0] == "12."
    assert split_marker("(3) item")[0] == "(3)"
    assert split_marker("plain sentence") == (None, "plain sentence")
    assert is_marker_only("iv)") and is_marker_only("•")
    assert not is_marker_only("12")            # a bare number is a page number
    assert normalise_recurrence_key("Page 3 / 12") == \
        normalise_recurrence_key("Page 11 / 12")


def test_table_structure_is_preserved(parsed_samples) -> None:
    report = parsed_samples["technical-report"]
    tables = [t for p in report.pages for t in p.tables]
    assert len(tables) == 3
    assert sum(len(t.cells) for t in tables) == 58
    merged = [c for t in tables for c in t.cells if c.col_span > 1]
    assert merged, "the merged header cell was lost"
    paper_table = parsed_samples["research-paper"].pages[4].tables[0]
    assert len(paper_table.cells) == 25
    assert any(c.numeric for c in paper_table.cells)


def test_scanned_page_takes_the_ocr_branch(parsed_samples) -> None:
    invoice = parsed_samples["scanned-invoice"]
    page = invoice.pages[0]
    assert invoice.is_scanned and page.is_scanned
    assert page.extractable_chars < 20
    assert page.ocr_mean_confidence and page.ocr_mean_confidence > 60
    assert any(b.ocr_confidence is not None for b in page.all_blocks)
    table = page.tables[0]
    assert len(table.col_bounds) - 1 == 5
    text = " ".join(c.text for c in table.cells)
    assert "A-1180" in text and "740.00" in text
