# Limitations

Written honestly. A prototype that names its own failure modes is easier to
trust than one that claims none, and every item here is something a reviewer
would otherwise discover by accident.

## Typography

**Font substitution is visible on branded documents.** The original typeface is
almost never available for the target script, so a Devanagari or Arabic target
is set in Noto. The x-height correction keeps the optical weight right, but a
letterhead set in a corporate face comes back in a different voice. Every
substitution is listed in the validation report; none is hidden. There is
currently no attempt to reuse a face embedded in the source PDF even when it
does cover the target script — that is the obvious next improvement.

**Line breaks move even in an identity round-trip.** Substituting Times with
Noto Serif changes advance widths, so justified paragraphs re-wrap at different
words. Masked SSIM on the bundled samples is 1.000 (the artwork does not move);
unmasked SSIM is 0.67–0.95 (the words do). If line-for-line identity matters
more than script coverage, the source font has to be reused, which is not
implemented.

**Italics disappear in Devanagari, Arabic and CJK.** Those Noto families ship no
italic cut, and synthetic slanting is worse than losing the distinction. The
loss is recorded per segment.

**Tracking is unavailable for CJK.** Rung 2 needs CSS letter-spacing, which
means the HTML writer, which re-embeds a 17–25MB CJK face on every layout pass.
CJK therefore skips rung 2 and goes from tightened leading straight to a size
reduction.

**`₹` and a few other symbols render as empty boxes in CJK and Arabic targets.**
Noto Naskh Arabic and Noto Sans CJK have no rupee sign. Where a fallback chain
can rescue a character the block is routed through the HTML writer; where it
cannot, the characters are reported as `MISSING_GLYPHS` rather than silently
boxed. One block in the Hindi→Japanese sample is affected.

**CJK output PDFs are large — about 17MB per document.** The CJK faces are
CID-keyed CFF fonts and are embedded whole. Subsetting them (either with
fontTools before embedding, or with `Document.subset_fonts()` after) renumbers
the charset and MuPDF then draws the *wrong glyph for every codepoint* — kana
come out as unrelated ideographs. That failure is silent and plausible-looking,
so the faces stay intact and the repo pays the size.

**Arabic output carries presentation forms in its text layer.** Shaping is
applied before placement, so copying text out of an Arabic output PDF yields
U+FE70-FEFF presentation forms rather than base letters. It renders correctly
and searches poorly. Routing Arabic through `insert_htmlbox` would keep the
logical characters but costs a re-embed per layout pass.

## Layout

**Heavily designed layouts will show reflow.** Magazines, infographics and
marketing material use irregular frames that overlap artwork, and text there is
often part of the artwork. The engine assumes rectangular text blocks with
recoverable reading order. Expect materially worse results than the synthetic
corpus suggests, and do not extrapolate from the measured numbers here.

**Tables never grow.** Row height growth is measured but not applied: the grid
stays exactly where the source drew it, and a cell whose translation does not
fit at 0.75× is reported as overflow instead. Splitting a long table across a
page boundary with a repeated header row is specified in the design and **not
implemented**.

**A rung-6 overflow is left as text that does not fit.** Nothing is clipped — the
block is placed at the floor size and flagged — but at 0.82× a badly expanding
German heading in a tight box can still be visibly cramped. The alternative
(clipping, or moving the grid) is worse, and the honest report is the point. On
the bundled samples, English targets from Hindi source hit this on 2 of 19
blocks and the scanned invoice on 5 of 37, all in table headers and totals.

**Column detection can be fooled.** It requires a clear, uncrossed, *parallel*
corridor. A page with a genuinely two-column band that is only a few
centimetres tall reads as single-column; grouping survives that (streams are
tracked independently) but reading order degrades to top-to-bottom.

**Rotated text is placed upright.** Blocks whose rotation is not a multiple of
90° cannot be written by either PyMuPDF writer, so vertical stamps and angled
watermarks are left in the source language rather than reflowed badly.

## Scanned documents

**OCR accuracy caps everything downstream.** The bundled invoice reads at ~93%
mean word confidence with `eng`, and one price still comes back as `24:10`
instead of `24.10`. That error is then translated faithfully. Blocks under 75%
are flagged, but a confident OCR mistake is invisible to the pipeline.

**Skewed scans are reconstructed with horizontal text.** Structure is recovered
on the straightened page, so rows and columns are correct, but neither writer
accepts an arbitrary rotation — only quarter turns. Text is therefore drawn
horizontally over a slanted background: about 4pt of residual slant across a
200pt block at one degree.

**Inpainting is a flat fill.** The median colour of a 3px ring is the right
answer on plain paper and the wrong one over a gradient, a photograph or a
watermark, where the patch will be visible.

**Language packs must be installed.** Tesseract with the wrong language model
produces confident noise. The Docker image installs the eight packs; a bare
checkout may have only `eng`, and the API reports which are available.

## Translation

**The mock provider is not a translator.** It transliterates source syllables
into plausible target-script sequences with realistic length ratios. It exists
to stress the layout engine offline, and its output is meaningless as language.
Every number in this repository's demo output is a *layout* measurement, not a
translation-quality measurement.

**Term consistency is enforced by substitution, not by instruction.** A locked
term is masked as a protected span and restored to its target string, so a model
cannot ignore it — and equally cannot inflect it. In languages with rich
morphology a locked term will sometimes appear in the wrong case.

**Protected spans are pattern-based.** URLs, numbers, citations, code, dates and
3+-letter acronyms are masked. A proper noun that matches none of those patterns
is sent to the model and may be translated. The glossary is the escape hatch.

**Context is one unit either side.** Document-level coherence beyond that — a
term introduced on page 2 and referenced on page 30 — relies on the term memory,
which only catches phrases of two or more words that recur at least three times.

## Product scope

Deliberately absent, per the brief: authentication, RBAC, user management,
billing, DOCX/PPTX/image input, cloud storage, analytics dashboards, and any
chat affordance. `DocumentParser`, `StorageBackend`, `OCREngine`, `AIProvider`
and `FontResolver` exist as interfaces with one implementation each so those can
be added without reshaping the engine.

**Regenerating a page can move its neighbours onto a different fit rung.**
Placement is page-scoped and order-dependent: a shorter edited paragraph frees
space that the block below may then use. The version diff reports those rung
changes explicitly, so the effect is visible rather than surprising, but it does
mean "regenerate one paragraph" is not always a strictly local change.

**Schema is created on startup; there are no migrations.** Fine for a
prototype, not for anything that has to keep data across versions.

**Jobs live in one process.** The worker is an asyncio task and the client
polls. Restarting the API loses in-flight jobs. Nothing here scales past one
machine, and that was the explicit design constraint.

**No claim is made about classified or regulated data.** The security posture is
prototype architecture designed for future enterprise controls: server-side AI
calls, env-based secrets, magic-byte upload validation, size caps, temp-file
cleanup and an append-only audit log. Authentication is an unimplemented
interface, not a feature.
