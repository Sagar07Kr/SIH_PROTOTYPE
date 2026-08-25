#!/usr/bin/env python3
"""Vendor the Noto font set into backend/assets/fonts/.

LayoutLoom cannot substitute fonts it does not have. A Devanagari or Arabic
glyph rendered in Helvetica is a row of empty boxes, so this script is a hard
prerequisite for the reconstruction engine (build gate P1).

Three sources are tried per family, in order:

  1. LOCAL   -- Noto faces already installed on the machine (incl. the
                NotoSansCJK/NotoSerifCJK .ttc collections shipped by the
                fonts-noto-cjk package). CJK is always taken from here when
                available because the fontsource CJK packages are split into
                ~100 unicode-range chunks.
  2. NPM     -- the @fontsource/* packages, which ship per-script woff2
                subsets. Subsets for one family are converted to TTF and
                merged (e.g. devanagari + latin + latin-ext) so a single face
                covers the target script plus embedded Latin runs.
  3. HTTPS   -- notofonts.github.io release TTFs (canonical upstream).

All output is OFL-1.1; attribution is written to THIRD_PARTY_NOTICES.md.

Run:  python scripts/fetch_fonts.py [--force] [--verify]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backend" / "assets" / "fonts"
MANIFEST = OUT / "manifest.json"

LOCAL_FONT_DIRS = [
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts"),
]

# CJK faces are vendored WHOLE, deliberately. Noto Sans/Serif CJK are
# CID-keyed CFF fonts; running them through a subsetter renumbers the charset
# and MuPDF then draws the wrong glyph for every codepoint (kana come out as
# unrelated ideographs). It is silent, it looks plausible, and it is exactly
# the class of bug this project exists to avoid -- so the faces stay intact and
# the repo pays ~17MB each. See docs/LIMITATIONS.md.
CJK_KEEP = None


@dataclass
class FaceSpec:
    """One output file."""
    out: str                       # filename written into assets/fonts
    script: str                    # logical script key
    serif: bool
    weight: int
    italic: bool = False
    local: list[str] = field(default_factory=list)   # local basenames to look for
    ttc_face: str | None = None                      # name(4) inside a .ttc
    npm_pkg: str | None = None                       # @fontsource package
    npm_subsets: list[str] = field(default_factory=list)
    url: str | None = None
    subset_unicodes: str | None = None
    optional: bool = False


def _sty(w: int, it: bool) -> str:
    if w >= 700:
        return "BoldItalic" if it else "Bold"
    return "Italic" if it else "Regular"


def _latin_family(family: str, serif: bool, weights=(400, 700), italics=True) -> list[FaceSpec]:
    subs = ["latin", "latin-ext", "cyrillic", "cyrillic-ext", "greek", "greek-ext"]
    pkg = "noto-serif" if serif else "noto-sans"
    specs = []
    for w in weights:
        for it in ([False, True] if italics else [False]):
            base = f"{family}-{_sty(w, it)}"
            specs.append(FaceSpec(
                out=f"{base}.ttf", script="latin", serif=serif, weight=w, italic=it,
                local=[f"{base}.ttf", f"{base}.otf"],
                npm_pkg=pkg, npm_subsets=subs,
                url=(f"https://github.com/notofonts/notofonts.github.io/raw/main/fonts/"
                     f"{family}/hinted/ttf/{base}.ttf"),
                optional=it,
            ))
    return specs


SPECS: list[FaceSpec] = []
SPECS += _latin_family("NotoSans", serif=False)
SPECS += _latin_family("NotoSerif", serif=True)

for _w in (400, 700):
    SPECS.append(FaceSpec(
        out=f"NotoSansDevanagari-{_sty(_w, False)}.ttf", script="devanagari",
        serif=False, weight=_w,
        local=[f"NotoSansDevanagari-{_sty(_w, False)}.ttf"],
        npm_pkg="noto-sans-devanagari", npm_subsets=["devanagari", "latin", "latin-ext"],
        url=("https://github.com/notofonts/notofonts.github.io/raw/main/fonts/"
             f"NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-{_sty(_w, False)}.ttf"),
    ))
    SPECS.append(FaceSpec(
        out=f"NotoSerifDevanagari-{_sty(_w, False)}.ttf", script="devanagari",
        serif=True, weight=_w, optional=True,
        local=[f"NotoSerifDevanagari-{_sty(_w, False)}.ttf"],
        npm_pkg="noto-serif-devanagari", npm_subsets=["devanagari", "latin", "latin-ext"],
        url=("https://github.com/notofonts/notofonts.github.io/raw/main/fonts/"
             f"NotoSerifDevanagari/hinted/ttf/NotoSerifDevanagari-{_sty(_w, False)}.ttf"),
    ))
    SPECS.append(FaceSpec(
        out=f"NotoNaskhArabic-{_sty(_w, False)}.ttf", script="arabic",
        serif=False, weight=_w,
        local=[f"NotoNaskhArabic-{_sty(_w, False)}.ttf"],
        npm_pkg="noto-naskh-arabic", npm_subsets=["arabic", "latin", "latin-ext"],
        url=("https://github.com/notofonts/notofonts.github.io/raw/main/fonts/"
             f"NotoNaskhArabic/hinted/ttf/NotoNaskhArabic-{_sty(_w, False)}.ttf"),
    ))
    for _lang, _tag in (("JP", "JP"), ("SC", "SC")):
        for _serif in (False, True):
            _fam = f"NotoSerif{_lang}" if _serif else f"NotoSans{_lang}"
            _coll = "NotoSerifCJK" if _serif else "NotoSansCJK"
            SPECS.append(FaceSpec(
                out=f"{_fam}-{_sty(_w, False)}.ttf", script=_lang.lower(),
                serif=_serif, weight=_w,
                local=[f"{_fam}-{_sty(_w, False)}.otf", f"{_coll}-{_sty(_w, False)}.ttc"],
                ttc_face=f"Noto {'Serif' if _serif else 'Sans'} CJK {_tag}",
                subset_unicodes=CJK_KEEP,   # None: never subset CID-keyed CFF
                optional=_serif,
            ))


# ---------------------------------------------------------------- helpers

def _log(msg: str) -> None:
    print(f"[fonts] {msg}", flush=True)


def _iter_local() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in LOCAL_FONT_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() in (".ttf", ".otf", ".ttc") and p.name not in found:
                found[p.name] = p
    return found


def _save_ttf(font, dest: Path, unicodes: str | None = None) -> None:
    from fontTools import subset as ft_subset

    font.flavor = None
    if unicodes:
        opts = ft_subset.Options()
        opts.layout_features = ["*"]
        opts.name_IDs = ["*"]
        opts.name_legacy = True
        opts.notdef_outline = True
        opts.recalc_bounds = True
        opts.drop_tables = []
        sub = ft_subset.Subsetter(options=opts)
        sub.populate(unicodes=ft_subset.parse_unicodes(unicodes))
        sub.subset(font)
    dest.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(dest))


def _from_local(spec: FaceSpec, local: dict[str, Path]) -> bool:
    from fontTools.ttLib import TTFont, TTCollection

    for name in spec.local:
        src = local.get(name)
        if not src:
            continue
        try:
            if src.suffix.lower() == ".ttc":
                coll = TTCollection(str(src))
                # Match font names flexibly
                want_bold = spec.weight >= 550
                target_tag = spec.script.upper() if spec.script in ("jp", "sc") else spec.script
                for f in coll.fonts:
                    debug_name = f["name"].getDebugName(4) or ""
                    is_bold = "Bold" in debug_name or "bold" in debug_name
                    if spec.ttc_face and (debug_name in (spec.ttc_face, f"{spec.ttc_face} Regular", f"{spec.ttc_face} Bold")):
                        _save_ttf(f, OUT / spec.out, spec.subset_unicodes)
                        _log(f"{spec.out} <- local {src.name} [{debug_name}]")
                        return True
                    if target_tag in debug_name.upper() and (is_bold == want_bold):
                        _save_ttf(f, OUT / spec.out, spec.subset_unicodes)
                        _log(f"{spec.out} <- local {src.name} [{debug_name}]")
                        return True
            else:
                _save_ttf(TTFont(str(src)), OUT / spec.out, spec.subset_unicodes)
                _log(f"{spec.out} <- local {src}")
                return True
        except Exception as exc:  # pragma: no cover - source specific
            _log(f"local read failed for {name}: {exc}")
    return False


_NPM_DIR: Path | None = None


def _npm_root(packages: list[str]) -> Path | None:
    global _NPM_DIR
    if _NPM_DIR is not None:
        return _NPM_DIR
    if not shutil.which("npm"):
        return None
    work = Path(tempfile.mkdtemp(prefix="layoutloom-fonts-"))
    cmd = ["npm", "install", "--no-audit", "--no-fund", "--silent",
           *[f"@fontsource/{p}" for p in packages]]
    try:
        subprocess.run(cmd, cwd=work, check=True, timeout=600,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception as exc:  # pragma: no cover
        _log(f"npm install failed: {exc}")
        return None
    _NPM_DIR = work / "node_modules" / "@fontsource"
    return _NPM_DIR


def _from_npm(spec: FaceSpec, npm_root: Path | None) -> bool:
    if not (spec.npm_pkg and npm_root):
        return False
    from fontTools.ttLib import TTFont
    from fontTools import merge as ft_merge

    files = npm_root / spec.npm_pkg / "files"
    if not files.is_dir():
        return False
    style = "italic" if spec.italic else "normal"
    parts: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="ll-sub-"))
    for sub in spec.npm_subsets:
        cand = files / f"{spec.npm_pkg}-{sub}-{spec.weight}-{style}.woff2"
        if not cand.exists():
            cand = files / f"{spec.npm_pkg}-{sub}-{spec.weight}-{style}.woff"
        if not cand.exists():
            continue
        try:
            f = TTFont(str(cand))
            f.flavor = None
            p = tmp / (cand.stem + ".ttf")
            f.save(str(p))
            parts.append(str(p))
        except Exception as exc:  # pragma: no cover
            _log(f"woff decode failed {cand.name}: {exc}")
    if not parts:
        return False
    try:
        if len(parts) == 1:
            merged = TTFont(parts[0])
        else:
            merged = ft_merge.Merger().merge(parts)
        # merge() rewrites the name table; restore a sane family/style name.
        _rename(merged, spec)
        _save_ttf(merged, OUT / spec.out, spec.subset_unicodes)
        _log(f"{spec.out} <- npm @fontsource/{spec.npm_pkg} ({len(parts)} subsets)")
        return True
    except Exception as exc:  # pragma: no cover
        _log(f"merge failed for {spec.out}: {exc}")
        return False


def _rename(font, spec: FaceSpec) -> None:
    family, _, style = spec.out.rsplit(".", 1)[0].partition("-")
    full = f"{family} {style}"
    try:
        nt = font["name"]
        for nid, val in ((1, family), (2, style), (3, f"LayoutLoom:{full}"),
                         (4, full), (6, f"{family}-{style}")):
            nt.setName(val, nid, 3, 1, 0x409)
            nt.setName(val, nid, 1, 0, 0)
    except Exception:
        pass


def _from_url(spec: FaceSpec) -> bool:
    if not spec.url or os.environ.get("LAYOUTLOOM_OFFLINE") == "1":
        return False
    try:
        req = urllib.request.Request(spec.url, headers={"User-Agent": "layoutloom/1.0"})
        with urllib.request.urlopen(req, timeout=45) as r:
            data = r.read()
        if len(data) < 20_000:
            return False
        (OUT / spec.out).write_bytes(data)
        _log(f"{spec.out} <- {spec.url}")
        return True
    except Exception as exc:
        _log(f"download failed for {spec.out}: {exc}")
        return False


SMOKE = {
    "latin": "Größe Ærø — Fjord 123",
    "devanagari": "नमस्ते दुनिया",
    "arabic": "مرحبا بالعالم",
    "jp": "こんにちは世界",
    "sc": "你好世界",
}


def verify(strict: bool = True) -> dict:
    """P1 gate: every required script must render real glyphs, not tofu."""
    from fontTools.ttLib import TTFont

    report: dict[str, dict] = {}
    ok = True
    for spec in SPECS:
        path = OUT / spec.out
        entry = {"present": path.exists(), "optional": spec.optional}
        if path.exists():
            try:
                f = TTFont(str(path))
                cmap = f.getBestCmap()
                text = SMOKE.get(spec.script, "")
                missing = [c for c in text
                           if not c.isspace() and c not in ("—",) and ord(c) not in cmap]
                entry.update(glyphs=len(f.getGlyphOrder()), cmap=len(cmap),
                             missing_smoke_chars="".join(missing),
                             has_gsub="GSUB" in f)
                if missing:
                    ok = False
            except Exception as exc:
                entry["error"] = str(exc)
                ok = False
        elif not spec.optional:
            ok = False
        report[spec.out] = entry
    required_missing = [k for k, v in report.items()
                        if not v["present"] and not v["optional"]]
    if strict and (required_missing or not ok):
        _log(f"VERIFY FAILED; missing required faces: {required_missing}")
    return {"ok": ok and not required_missing, "faces": report}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-fetch faces already present")
    ap.add_argument("--verify", action="store_true", help="only run the P1 verification")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    if args.verify:
        rep = verify()
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0 if rep["ok"] else 1

    local = _iter_local()
    npm_pkgs = sorted({s.npm_pkg for s in SPECS if s.npm_pkg})
    npm_root = _npm_root(npm_pkgs) if any(
        not (OUT / s.out).exists() or args.force for s in SPECS if s.npm_pkg) else None

    results = {}
    for spec in SPECS:
        dest = OUT / spec.out
        if dest.exists() and not args.force:
            results[spec.out] = "cached"
            continue
        src = None
        if _from_local(spec, local):
            src = "local"
        elif _from_npm(spec, npm_root):
            src = "npm"
        elif _from_url(spec):
            src = "url"
        results[spec.out] = src or ("missing-optional" if spec.optional else "MISSING")
        if src is None and not spec.optional:
            _log(f"!! required face unavailable: {spec.out}")

    rep = verify()
    MANIFEST.write_text(json.dumps({"sources": results, "verify": rep},
                                   indent=2, ensure_ascii=False))
    total = sum(p.stat().st_size for p in OUT.glob("*.ttf"))
    _log(f"{len(list(OUT.glob('*.ttf')))} faces, {total/1e6:.1f} MB, ok={rep['ok']}")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
