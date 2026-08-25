#!/usr/bin/env python3
"""End-to-end demo over the real HTTP surface, offline, with no API key.

Runs every bundled sample through upload -> analyze -> translate -> validate ->
export and prints the measured numbers. This is what `make demo` runs, and it
is deliberately the same path the browser takes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_TARGETS = {
    "govt-notice.pdf": "en",
    "research-paper.pdf": "hi",
    "technical-report.pdf": "en",
    "scanned-invoice.pdf": "de",
}


def run(targets: dict[str, str], quiet: bool = False) -> int:
    from fastapi.testclient import TestClient

    from backend.main import app

    failures = 0
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        print(f"[demo] provider={health['provider']} "
              f"fonts={health['fonts']['faces']} "
              f"ocr={health['ocr']['available']} "
              f"missing_scripts={health['fonts']['missing_scripts']}")
        project = client.post("/api/projects",
                             json={"name": "demo"}).json()
        for name, target in targets.items():
            t0 = time.perf_counter()
            doc = client.post("/api/documents/from-sample",
                              params={"name": name})
            if doc.status_code != 200:
                print(f"[demo] {name}: upload failed {doc.json()}")
                failures += 1
                continue
            doc = doc.json()
            analysis = client.post(
                f"/api/documents/{doc['id']}/analyze").json()["analysis"]
            started = client.post(f"/api/projects/{project['id']}/translate",
                                  json={"document_id": doc["id"],
                                        "target_lang": target})
            if started.status_code != 200:
                print(f"[demo] {name}: translate rejected {started.json()}")
                failures += 1
                continue
            job_id = started.json()["job_id"]
            job = _wait(client, job_id)
            progress = job["progress"]
            if progress.get("stage") != "DONE":
                print(f"[demo] {name}: job ended in {progress.get('stage')} "
                      f"{progress.get('error')}")
                failures += 1
                continue
            version_id = progress["version_id"]
            payload = client.get(f"/api/versions/{version_id}").json()
            val = payload["validation"] or {}
            metrics = {m["key"]: m["value"] for m in val.get("metrics", [])}
            scores = {k: v["value"] for k, v in (val.get("scores") or {}).items()}
            rungs: dict[str, int] = {}
            for s in payload["segments"]:
                if s["fit_rung"] is not None:
                    rungs[str(s["fit_rung"])] = rungs.get(str(s["fit_rung"]), 0) + 1
            pdf = client.get(f"/api/versions/{version_id}/pdf")
            exported = client.post(f"/api/versions/{version_id}/export",
                                   json={"format": "json"})
            elapsed = time.perf_counter() - t0
            print(f"[demo] {name} -> {target}  {elapsed:5.1f}s  "
                  f"pages={analysis['page_count']} "
                  f"segments={len(payload['segments'])} "
                  f"rungs={dict(sorted(rungs.items()))}")
            print(f"        ssim={metrics.get('graphics_fidelity')} "
                  f"coverage={metrics.get('text_coverage')} "
                  f"overflow={metrics.get('overflow_count')} "
                  f"overlaps={metrics.get('overlap_violations')} "
                  f"geometry={metrics.get('geometry_integrity')} "
                  f"subs={metrics.get('font_substitutions')} "
                  f"budget={metrics.get('adjustment_budget')}")
            print(f"        layout={scores.get('layout_preservation')} "
                  f"text={scores.get('text_fidelity')} "
                  f"typography={scores.get('typographic_fidelity')} "
                  f"confidence={scores.get('translation_confidence')} "
                  f"pdf={len(pdf.content) // 1024}KB "
                  f"export={exported.status_code}")
            if not quiet and val.get("issues"):
                kinds: dict[str, int] = {}
                for i in val["issues"]:
                    kinds[i["code"]] = kinds.get(i["code"], 0) + 1
                print(f"        issues={dict(sorted(kinds.items()))}")
            if metrics.get("text_coverage", 0) < 1.0:
                failures += 1
            if not metrics.get("geometry_integrity", False):
                failures += 1
    print(f"[demo] {'OK' if not failures else str(failures) + ' FAILURE(S)'}")
    return 1 if failures else 0


def _wait(client, job_id: str, timeout_s: float = 300.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        stage = (job.get("progress") or {}).get("stage")
        if stage in ("DONE", "FAILED"):
            return job
        time.sleep(0.25)
    return client.get(f"/api/jobs/{job_id}").json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="append", default=[],
                    help="name.pdf=target_lang (repeatable)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    targets = dict(DEFAULT_TARGETS)
    if args.sample:
        targets = {}
        for spec in args.sample:
            name, _, lang = spec.partition("=")
            targets[name] = lang or "en"
    return run(targets, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
