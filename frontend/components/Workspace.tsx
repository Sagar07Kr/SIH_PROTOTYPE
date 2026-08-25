"use client";
import {
  AlertTriangle, Download, FileDown, History, RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ComparisonViewer } from "@/components/ComparisonViewer";
import { Inspector, ScorePanel } from "@/components/Inspector";
import {
  Badge, Button, Panel, Tabs, TabsContent, TabsList, TabsTrigger,
} from "@/components/ui/primitives";
import { ApiFailure, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { Diff, Validation, VersionPayload } from "@/types/api";

export function Workspace({ versionId }: { versionId: string }) {
  const [payload, setPayload] = useState<VersionPayload | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<{ id: string; label: string; number: number }[]>([]);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [currentId, setCurrentId] = useState(versionId);

  const load = useCallback(async (id: string) => {
    setError(null);
    try {
      const p = await api.version(id);
      setPayload(p);
      setValidation(p.validation);
      setHistory((h) => (h.some((v) => v.id === id)
        ? h
        : [...h, { id, label: p.version.label, number: p.version.number }]
          .sort((a, b) => a.number - b.number)));
    } catch (e) {
      setError(e instanceof ApiFailure ? `${e.code}: ${e.message}` : String(e));
    }
  }, []);

  useEffect(() => { load(currentId); }, [currentId, load]);

  const segments = payload?.segments ?? [];
  const selectedSegment = useMemo(
    () => segments.find((s) => s.element_id === selected) ?? null,
    [segments, selected],
  );

  const pageIssues = useMemo(() => {
    const map = new Map<number, { errors: number; warnings: number }>();
    for (const s of segments) {
      const entry = map.get(s.page) ?? { errors: 0, warnings: 0 };
      for (const i of s.issues) {
        if (i.severity === "ERROR") entry.errors += 1;
        else if (i.severity === "WARNING") entry.warnings += 1;
      }
      map.set(s.page, entry);
    }
    return map;
  }, [segments]);

  async function afterChange(newVersionId: string) {
    const previous = currentId;
    setCurrentId(newVersionId);
    try {
      setDiff(await api.diff(previous, newVersionId));
    } catch {
      setDiff(null);
    }
  }

  async function edit(segmentId: string, text: string) {
    setBusy(true);
    try {
      const res = await api.editSegment(segmentId, text);
      await afterChange(res.version.id);
    } catch (e) {
      setError(e instanceof ApiFailure ? `${e.code}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function regenerateSegment(segmentId: string) {
    setBusy(true);
    try {
      const res = await api.regenerateSegment(segmentId);
      await afterChange(res.version.id);
    } catch (e) {
      setError(e instanceof ApiFailure ? `${e.code}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function regeneratePage() {
    if (!payload) return;
    setBusy(true);
    try {
      const res = await api.regeneratePage(payload.version.id, page);
      await afterChange(res.version.id);
    } catch (e) {
      setError(e instanceof ApiFailure ? `${e.code}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function exportAs(format: "pdf" | "txt" | "md" | "json") {
    if (!payload) return;
    const res = await fetch(api.exportUrl(payload.version.id), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format }),
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `translated-v${payload.version.number}.${format}`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!payload) {
    return (
      <div className="panel rounded p-4 text-xs text-muted">
        {error ? <span className="text-[var(--bad)]">{error}</span> : "Loading version…"}
      </div>
    );
  }

  const pages = Array.from({ length: payload.document.page_count }, (_, i) => i);

  return (
    <div className="flex flex-col gap-3">
      <div className="panel flex flex-wrap items-center justify-between gap-3 rounded px-3 py-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-medium">{payload.document.filename}</span>
          <Badge>{payload.document.source_lang} → {payload.version.target_lang}</Badge>
          <Badge tone="accent">version {payload.version.number}</Badge>
          {payload.version.label && <Badge>{payload.version.label}</Badge>}
          {payload.version.changed_pages.length > 0 && (
            <Badge tone="warn">
              re-rendered page{payload.version.changed_pages.length > 1 ? "s" : ""}{" "}
              {payload.version.changed_pages.map((p) => p + 1).join(", ")}
            </Badge>
          )}
          {busy && <span className="text-2xs text-muted">working…</span>}
        </div>
        <div className="flex items-center gap-1.5">
          <Button size="xs" onClick={() => exportAs("pdf")}>
            <Download size={11} /> PDF
          </Button>
          <Button size="xs" variant="outline" onClick={() => exportAs("md")}>
            <FileDown size={11} /> MD
          </Button>
          <Button size="xs" variant="outline" onClick={() => exportAs("json")}>
            <FileDown size={11} /> JSON
          </Button>
          <Button size="xs" variant="ghost" disabled={busy} onClick={regeneratePage}>
            <RefreshCw size={11} /> Regenerate page {page + 1}
          </Button>
        </div>
      </div>

      {error && (
        <p className="rounded border border-[var(--bad)] px-2 py-1.5 text-2xs text-[var(--bad)]">
          {error}
        </p>
      )}

      <div className="grid min-h-[70vh] gap-3
                      lg:grid-cols-[150px_minmax(0,1fr)_360px]">
        <Panel title="Pages" bodyClassName="p-2">
          <ol className="flex flex-col gap-2">
            {pages.map((p) => {
              const issues = pageIssues.get(p) ?? { errors: 0, warnings: 0 };
              return (
                <li key={p}>
                  <button
                    onClick={() => setPage(p)}
                    className={cn("relative block w-full overflow-hidden rounded border",
                      p === page ? "border-[var(--accent)]" : "border-[var(--line)]")}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={api.versionPageUrl(payload.version.id, p, 72)}
                      alt={`page ${p + 1}`}
                      className="block w-full bg-white"
                    />
                    <span className="absolute left-1 top-1 rounded-sm bg-[var(--panel)]/90
                                     px-1 text-2xs tabular">{p + 1}</span>
                    <span className="absolute right-1 top-1 flex gap-0.5">
                      {issues.errors > 0 && (
                        <span className="rounded-sm bg-[var(--bad)] px-1 text-2xs text-white tabular">
                          {issues.errors}
                        </span>
                      )}
                      {issues.warnings > 0 && (
                        <span className="rounded-sm bg-[var(--warn)] px-1 text-2xs text-white tabular">
                          {issues.warnings}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </Panel>

        <Panel title={`Comparison — page ${page + 1}`}
               bodyClassName="flex min-h-0 flex-col">
          <ComparisonViewer payload={payload} page={page} selected={selected}
                            onSelect={setSelected} />
        </Panel>

        <Panel title="Inspector" bodyClassName="min-h-0">
          <Tabs defaultValue="scores" className="flex min-h-0 flex-col">
            <div className="border-b border-[var(--line)] px-3 py-1.5">
              <TabsList>
                <TabsTrigger value="scores">Scores</TabsTrigger>
                <TabsTrigger value="selection">Selection</TabsTrigger>
                <TabsTrigger value="history">History</TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="scores" className="min-h-0 overflow-auto">
              <ScorePanel validation={validation} />
            </TabsContent>
            <TabsContent value="selection" className="min-h-0 overflow-auto">
              <Inspector segment={selectedSegment} busy={busy} onEdit={edit}
                         onRegenerate={regenerateSegment} />
            </TabsContent>
            <TabsContent value="history" className="min-h-0 overflow-auto">
              <div className="flex flex-col gap-2 p-3">
                <div className="flex items-center gap-1.5 text-2xs text-muted">
                  <History size={12} /> versions in this session
                </div>
                <ul className="flex flex-col gap-1">
                  {history.map((v) => (
                    <li key={v.id}>
                      <button
                        onClick={() => setCurrentId(v.id)}
                        className={cn("w-full rounded border px-2 py-1 text-left text-2xs",
                          v.id === currentId
                            ? "border-[var(--accent)]"
                            : "border-[var(--line)] hover:border-[var(--accent)]")}
                      >
                        v{v.number} · {v.label || "initial"}
                      </button>
                    </li>
                  ))}
                </ul>
                {diff && (
                  <div className="mt-2 flex flex-col gap-1 border-t border-[var(--line)] pt-2">
                    <span className="text-2xs uppercase tracking-wider text-muted">
                      last change · v{diff.from_number} → v{diff.to_number}
                    </span>
                    <span className="text-2xs tabular">
                      pages re-rendered: {diff.changed_pages.map((p) => p + 1).join(", ") || "none"}
                    </span>
                    <span className="text-2xs tabular">
                      blocks changed: {diff.changed_blocks.length} · rungs changed:{" "}
                      {diff.layout_deltas.rungs_changed} · boxes moved:{" "}
                      {diff.layout_deltas.blocks_moved}
                    </span>
                    {diff.changed_blocks.slice(0, 6).map((c) => (
                      <div key={c.element_id}
                           className="rounded border border-[var(--line)] p-1.5 text-2xs">
                        <div className="text-muted line-through">{c.before?.slice(0, 90)}</div>
                        <div>{c.after?.slice(0, 90)}</div>
                        <div className="mt-0.5 text-muted tabular">
                          rung {c.rung_before} → {c.rung_after}
                          {c.size_before && c.size_after
                            ? ` · ${c.size_before.toFixed(1)}pt → ${c.size_after.toFixed(1)}pt`
                            : ""}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                <div className="mt-2 flex items-start gap-1.5 border-t border-[var(--line)]
                                pt-2 text-2xs leading-relaxed text-muted">
                  <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                  <span>
                    Versions are immutable. Editing a paragraph writes version
                    N+1 that shares every unchanged segment and re-renders only
                    the affected page.
                  </span>
                </div>
              </div>
            </TabsContent>
          </Tabs>
        </Panel>
      </div>

      <Panel title="Observability" bodyClassName="grid gap-3 p-3 sm:grid-cols-4">
        <Stat label="Provider" value={payload.job.provider} />
        <Stat label="Tokens in / out"
              value={`${payload.job.tokens.input} / ${payload.job.tokens.output}`} />
        <Stat label="OCR / translate / reconstruct"
              value={`${payload.job.timings_ms.ocr} / ${payload.job.timings_ms.translate} / ${payload.job.timings_ms.reconstruct} ms`} />
        <Stat label="Segments" value={String(segments.length)} />
      </Panel>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-[var(--line)] p-2">
      <div className="text-2xs uppercase tracking-wider text-muted">{label}</div>
      <div className="mt-0.5 text-xs tabular">{value}</div>
    </div>
  );
}
