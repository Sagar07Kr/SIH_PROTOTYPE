"use client";
import { Columns2, Layers, TextQuote, ZoomIn, ZoomOut } from "lucide-react";
import { useMemo, useState } from "react";

import { PdfPage } from "@/components/PdfPage";
import {
  Badge, Button, Slider, Tabs, TabsContent, TabsList, TabsTrigger,
} from "@/components/ui/primitives";
import { RUNG_LABELS, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { Segment, VersionPayload } from "@/types/api";

const WIDTHS = [340, 420, 520, 640, 780, 940];

export function ComparisonViewer({
  payload, page, selected, onSelect,
}: {
  payload: VersionPayload;
  page: number;
  selected: string | null;
  onSelect: (elementId: string | null) => void;
}) {
  const [zoom, setZoom] = useState(2);
  const [opacity, setOpacity] = useState(60);
  const [difference, setDifference] = useState(false);
  const [geometry, setGeometry] = useState({ widthPt: 595, heightPt: 842, scale: 1 });
  const width = WIDTHS[zoom];

  const srcPdf = api.documentPdfUrl(payload.document.id);
  const dstPdf = api.versionPdfUrl(payload.version.id);
  const pageSegments = useMemo(
    () => payload.segments.filter((s) => s.page === page && s.bbox),
    [payload.segments, page],
  );

  return (
    <Tabs defaultValue="side" className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b
                      border-[var(--line)] px-3 py-1.5">
        <TabsList>
          <TabsTrigger value="side"><Columns2 size={11} className="mr-1" />Side-by-side</TabsTrigger>
          <TabsTrigger value="overlay"><Layers size={11} className="mr-1" />Overlay</TabsTrigger>
          <TabsTrigger value="text"><TextQuote size={11} className="mr-1" />Text</TabsTrigger>
        </TabsList>
        <div className="flex items-center gap-1.5">
          <Button variant="ghost" size="xs" onClick={() => setZoom((z) => Math.max(0, z - 1))}>
            <ZoomOut size={12} />
          </Button>
          <span className="w-10 text-center text-2xs tabular text-muted">
            {Math.round((width / geometry.widthPt) * 100)}%
          </span>
          <Button variant="ghost" size="xs"
                  onClick={() => setZoom((z) => Math.min(WIDTHS.length - 1, z + 1))}>
            <ZoomIn size={12} />
          </Button>
        </div>
      </div>

      <TabsContent value="side" className="min-h-0 flex-1 overflow-auto p-3">
        {/* One scroll container holds both pages, so scroll and zoom are locked
            by construction rather than by synchronising two listeners. */}
        <div className="flex items-start gap-3">
          <PageFrame label={`source · ${payload.document.source_lang ?? "?"}`}>
            <PdfPage url={srcPdf} page={page} width={width}
                     fallbackSrc={api.documentPageUrl(payload.document.id, page, 150)}
                     onGeometry={setGeometry} />
          </PageFrame>
          <PageFrame label={`translated · ${payload.version.target_lang}`}>
            <div className="relative">
              <PdfPage url={dstPdf} page={page} width={width}
                       fallbackSrc={api.versionPageUrl(payload.version.id, page, 150)} />
              <BlockOverlay segments={pageSegments} geometry={geometry}
                            selected={selected} onSelect={onSelect} />
            </div>
          </PageFrame>
        </div>
      </TabsContent>

      <TabsContent value="overlay" className="min-h-0 flex-1 overflow-auto p-3">
        <div className="mb-3 flex max-w-md items-center gap-3">
          <span className="w-24 text-2xs uppercase tracking-wider text-muted">
            Translated
          </span>
          <Slider value={[opacity]} min={0} max={100} step={1}
                  onValueChange={(v) => setOpacity(v[0] ?? 0)} />
          <span className="w-10 text-2xs tabular text-muted">{opacity}%</span>
          <Button size="xs" variant={difference ? "accent" : "outline"}
                  onClick={() => setDifference((d) => !d)}>
            difference
          </Button>
        </div>
        <PageFrame label="registration check">
          <div className="relative">
            <PdfPage url={srcPdf} page={page} width={width}
                     fallbackSrc={api.documentPageUrl(payload.document.id, page, 150)}
                     onGeometry={setGeometry} />
            <div className="absolute inset-0"
                 style={{ opacity: opacity / 100,
                          mixBlendMode: difference ? "difference" : "normal" }}>
              <PdfPage url={dstPdf} page={page} width={width}
                       fallbackSrc={api.versionPageUrl(payload.version.id, page, 150)} />
            </div>
            <BlockOverlay segments={pageSegments} geometry={geometry}
                          selected={selected} onSelect={onSelect} />
          </div>
        </PageFrame>
        <p className="mt-2 max-w-prose text-2xs leading-relaxed text-muted">
          Images and ruling lines should stay put as the slider moves; in
          difference mode everything that survived reconstruction turns black.
        </p>
      </TabsContent>

      <TabsContent value="text" className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 bg-[var(--panel)] text-2xs uppercase
                            tracking-wider text-muted">
            <tr>
              <th className="border-b border-[var(--line)] px-3 py-1.5 text-left">Source</th>
              <th className="border-b border-[var(--line)] px-3 py-1.5 text-left">Target</th>
              <th className="border-b border-[var(--line)] px-2 py-1.5 text-right">Fit</th>
              <th className="border-b border-[var(--line)] px-2 py-1.5 text-right">Conf.</th>
            </tr>
          </thead>
          <tbody>
            {payload.segments.filter((s) => s.page === page).map((s) => (
              <tr key={s.id}
                  onClick={() => onSelect(s.element_id)}
                  className={cn("cursor-pointer align-top",
                    selected === s.element_id
                      ? "bg-[var(--surface)] outline outline-1 outline-[var(--accent)]"
                      : "hover:bg-[var(--surface)]")}>
                <td className="border-b border-[var(--line)] px-3 py-2 text-muted">
                  {s.list_marker && <span className="mr-1 font-mono">{s.list_marker}</span>}
                  {s.source}
                </td>
                <td className="border-b border-[var(--line)] px-3 py-2">{s.target}</td>
                <td className="border-b border-[var(--line)] px-2 py-2 text-right">
                  <RungBadge rung={s.fit_rung} />
                </td>
                <td className={cn("border-b border-[var(--line)] px-2 py-2 text-right tabular",
                  s.confidence < 0.8 ? "text-[var(--warn)]" : "text-muted")}>
                  {(s.confidence * 100).toFixed(0)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TabsContent>
    </Tabs>
  );
}

function PageFrame({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <figure className="flex flex-col gap-1">
      <figcaption className="text-2xs uppercase tracking-wider text-muted">
        {label}
      </figcaption>
      <div className="hairline bg-white p-0 shadow-sm">{children}</div>
    </figure>
  );
}

export function RungBadge({ rung }: { rung: number | null }) {
  if (rung === null || rung === undefined) return <span className="text-muted">—</span>;
  const tone = rung >= 6 ? "bad" : rung >= 4 ? "warn" : rung >= 1 ? "accent" : "ok";
  return <Badge tone={tone} title={RUNG_LABELS[rung]}>r{rung}</Badge>;
}

function BlockOverlay({ segments, geometry, selected, onSelect }: {
  segments: Segment[];
  geometry: { widthPt: number; heightPt: number; scale: number };
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  return (
    <div className="absolute inset-0">
      {segments.map((s) => {
        const [x0, y0, x1, y1] = s.bbox as [number, number, number, number];
        const isSelected = selected === s.element_id;
        const flagged = (s.fit_rung ?? 0) >= 4 || s.status !== "OK"
          || s.issues.some((i) => i.severity === "ERROR");
        return (
          <button
            key={s.id}
            onClick={(e) => { e.stopPropagation(); onSelect(s.element_id); }}
            title={`${s.type} · ${RUNG_LABELS[s.fit_rung ?? 0]}`}
            className={cn("absolute border",
              isSelected
                ? "border-[var(--accent)] bg-[var(--accent)]/10"
                : flagged
                  ? "border-[var(--warn)]/70 bg-transparent hover:bg-[var(--warn)]/10"
                  : "border-transparent hover:border-[var(--line)]")}
            style={{
              left: x0 * geometry.scale, top: y0 * geometry.scale,
              width: Math.max(2, (x1 - x0) * geometry.scale),
              height: Math.max(2, (y1 - y0) * geometry.scale),
            }}
          />
        );
      })}
    </div>
  );
}
