"""One test per sample, over the real HTTP surface, asserting I1-I8.

These run offline with the mock provider. Each case walks the same path a
browser walks: upload, analyze, translate, poll, read the version, validate,
export.
"""
from __future__ import annotations

import time

import pymupdf as fitz
import pytest

from backend.config import settings

CASES = [
    # sample, target, expectations
    ("govt-notice.pdf", "en", {"scanned": False}),
    ("research-paper.pdf", "hi", {"scanned": False}),
    ("technical-report.pdf", "en", {"scanned": False, "min_rung3": 1}),
    ("scanned-invoice.pdf", "de", {"scanned": True}),
]


def _run(client, sample: str, target: str) -> dict:
    doc = client.post("/api/documents/from-sample", params={"name": sample})
    assert doc.status_code == 200, doc.text
    doc = doc.json()
    analysis = client.post(f"/api/documents/{doc['id']}/analyze")
    assert analysis.status_code == 200, analysis.text
    project = client.post("/api/projects", json={"name": sample}).json()
    started = client.post(f"/api/projects/{project['id']}/translate",
                          json={"document_id": doc["id"], "target_lang": target})
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    deadline = time.time() + 300
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["progress"]["stage"] in ("DONE", "FAILED"):
            break
        time.sleep(0.2)
    assert job["progress"]["stage"] == "DONE", job["progress"].get("error")
    payload = client.get(f"/api/versions/{job['progress']['version_id']}").json()
    payload["analysis"] = analysis.json()["analysis"]
    payload["document_pdf"] = client.get(f"/api/documents/{doc['id']}/pdf").content
    payload["version_pdf"] = client.get(
        f"/api/versions/{payload['version']['id']}/pdf").content
    return payload


@pytest.mark.parametrize("sample,target,expect", CASES)
def test_sample_round_trip(client, sample, target, expect) -> None:
    payload = _run(client, sample, target)
    metrics = {m["key"]: m["value"] for m in payload["validation"]["metrics"]}
    segments = payload["segments"]

    # I1 / I2 -- page count and per-page geometry
    src = fitz.open(stream=payload["document_pdf"], filetype="pdf")
    dst = fitz.open(stream=payload["version_pdf"], filetype="pdf")
    try:
        assert dst.page_count == src.page_count
        for i in range(src.page_count):
            assert dst[i].rect.width == pytest.approx(src[i].rect.width, abs=0.01)
            assert dst[i].rect.height == pytest.approx(src[i].rect.height, abs=0.01)
    finally:
        src.close()
        dst.close()
    assert metrics["geometry_integrity"] is True

    # I3 -- graphics survive redaction
    assert metrics["graphics_fidelity"] >= settings.graphics_ssim_target

    # I4 -- nothing is clipped: every unresolved block is recorded as overflow
    for s in segments:
        if s["fit_rung"] == 6:
            assert any(i["code"] == "OVERFLOW" for i in s["issues"]), s["element_id"]
        if s["status"] not in ("PROTECTION_FAILURE", "PROVIDER_FAILURE"):
            assert s["target"].strip() or not s["source"].strip()

    # I5 -- no overlapping placements
    assert metrics["overlap_violations"] == 0

    # I6 -- protected spans appear verbatim
    for s in segments:
        for token, value in (s["placeholders"].get("tokens") or {}).items():
            assert value in s["target"], f"{value!r} lost from {s['element_id']}"

    # I7 -- this whole test ran on the mock provider with no key
    assert payload["job"]["provider"] == "mock"

    # I8 -- every score traces to the metrics
    for name, score in payload["validation"]["scores"].items():
        total = sum(t["contribution"] for t in score["terms"])
        assert score["value"] == pytest.approx(total, abs=0.15), name

    assert metrics["text_coverage"] == 1.0
    assert payload["analysis"]["is_scanned"] is expect["scanned"]
    if expect["scanned"]:
        assert any(s["target"] for s in segments)
        assert payload["analysis"]["pages"][0]["ocr_mean_confidence"] > 60

    if "min_rung3" in expect:
        reduced = sum(1 for s in segments if s["fit_rung"] == 3)
        assert reduced >= expect["min_rung3"], f"only {reduced} size reductions"


def test_german_target_exercises_the_ladder(client) -> None:
    """The P4 gate: a German target must produce real rung-3 and rung-6 events,
    which is the proof that the fit ladder is doing measured work."""
    payload = _run(client, "govt-notice.pdf", "de")
    rungs = [s["fit_rung"] for s in payload["segments"] if s["fit_rung"] is not None]
    assert sum(1 for r in rungs if r == 3) >= 2, rungs
    assert sum(1 for r in rungs if r == 6) >= 1, rungs
    # ... and nothing may be clipped to achieve it
    metrics = {m["key"]: m["value"] for m in payload["validation"]["metrics"]}
    assert metrics["overlap_violations"] == 0
    assert metrics["text_coverage"] == 1.0


def test_exports_and_partial_regeneration(client) -> None:
    payload = _run(client, "technical-report.pdf", "en")
    version_id = payload["version"]["id"]
    for fmt in ("txt", "md", "json"):
        res = client.post(f"/api/versions/{version_id}/export", json={"format": fmt})
        assert res.status_code == 200 and len(res.content) > 100, fmt

    target = next(s for s in payload["segments"]
                  if s["type"] == "paragraph" and s["page"] == 1 and s["target"])
    before = client.get(f"/api/versions/{version_id}/pdf").content
    res = client.patch(f"/api/segments/{target['id']}",
                       json={"text": "A short hand-written replacement."})
    assert res.status_code == 200, res.text
    new_version = res.json()["version"]
    assert new_version["number"] == 2
    assert new_version["changed_pages"] == [target["page"]]

    after = client.get(f"/api/versions/{new_version['id']}/pdf").content
    a = fitz.open(stream=before, filetype="pdf")
    b = fitz.open(stream=after, filetype="pdf")
    try:
        changed = [i for i in range(a.page_count)
                   if a[i].get_pixmap(dpi=72).samples != b[i].get_pixmap(dpi=72).samples]
        assert changed == [target["page"]], f"pages re-rendered: {changed}"
    finally:
        a.close()
        b.close()

    diff = client.get(f"/api/versions/{version_id}/diff/{new_version['id']}").json()
    assert diff["changed_pages"] == [target["page"]]
    # exactly one block changed *text*; re-placing a page can legitimately move
    # a neighbour onto a different fit rung, and the diff reports that too
    text_changes = [c for c in diff["changed_blocks"] if c.get("text_changed")]
    assert len(text_changes) == 1
    assert text_changes[0]["element_id"] == target["element_id"]

    # the earlier version is untouched -- rollback is a selection
    old = client.get(f"/api/versions/{version_id}").json()
    assert any(s["target"] == target["target"] for s in old["segments"])


def test_errors_are_typed_not_stack_traces(client) -> None:
    res = client.post("/api/documents",
                      files={"file": ("not.pdf", b"hello world", "application/pdf")})
    assert res.status_code == 415
    body = res.json()
    assert body["code"] == "NOT_A_PDF" and "retryable" in body
    assert "Traceback" not in body["message"]

    assert client.get("/api/versions/deadbeef").status_code == 404
    doc = client.post("/api/documents/from-sample",
                      params={"name": "govt-notice.pdf"}).json()
    project = client.post("/api/projects", json={"name": "x"}).json()
    bad = client.post(f"/api/projects/{project['id']}/translate",
                      json={"document_id": doc["id"], "target_lang": "xx"})
    assert bad.status_code == 400 and bad.json()["code"] == "BAD_REQUEST"
