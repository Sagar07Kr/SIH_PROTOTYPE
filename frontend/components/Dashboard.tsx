"use client";
import { FileText, FlaskConical, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { StageMachine } from "@/components/StageMachine";
import {
  Badge, Button, Field, Panel, Select, Tooltip,
} from "@/components/ui/primitives";
import { ApiFailure, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import type {
  Analysis, DocumentInfo, Health, Job, SampleInfo,
} from "@/types/api";

const STYLES = ["neutral", "formal", "plain", "technical"];

export function Dashboard() {
  const router = useRouter();
  const [health, setHealth] = useState<Health | null>(null);
  const [samples, setSamples] = useState<SampleInfo[]>([]);
  const [doc, setDoc] = useState<DocumentInfo | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [sourceLang, setSourceLang] = useState("en");
  const [targetLang, setTargetLang] = useState("hi");
  const [style, setStyle] = useState("neutral");
  const [options, setOptions] = useState({
    preserve_tables: true, preserve_lists: true, preserve_headers_footers: true,
    protect_numbers: true, ocr_scanned_pages: true,
  });
  const [glossaryText, setGlossaryText] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.samples().then((r) => setSamples(r.samples)).catch(() => setSamples([]));
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
  }, []);

  const ingest = useCallback(async (loader: () => Promise<DocumentInfo>) => {
    setError(null);
    setJob(null);
    setBusy("Reading the document");
    try {
      const uploaded = await loader();
      setDoc(uploaded);
      setBusy("Analyzing layout" + (uploaded.is_scanned ? " and running OCR" : ""));
      const res = await api.analyze(uploaded.id);
      setDoc(res.document);
      setAnalysis(res.analysis);
      setSourceLang(res.analysis.source_lang);
      if (res.analysis.source_lang === targetLang) {
        setTargetLang(res.analysis.source_lang === "en" ? "de" : "en");
      }
    } catch (e) {
      setError(e instanceof ApiFailure ? `${e.code}: ${e.message}` : String(e));
    } finally {
      setBusy(null);
    }
  }, [targetLang]);

  async function start() {
    if (!doc) return;
    setError(null);
    try {
      setBusy("Queueing");
      const project = await api.createProject(doc.filename);
      const glossary: Record<string, string> = {};
      glossaryText.split("\n").forEach((line) => {
        const [a, b] = line.split("=");
        if (a && b) glossary[a.trim()] = b.trim();
      });
      const started = await api.translate(project.id, {
        document_id: doc.id, target_lang: targetLang, style,
        glossary, options,
      });
      poll(started.job_id);
    } catch (e) {
      setError(e instanceof ApiFailure ? `${e.code}: ${e.message}` : String(e));
    } finally {
      setBusy(null);
    }
  }

  function poll(jobId: string) {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      try {
        const j = await api.job(jobId);
        setJob(j);
        if (j.progress.stage === "DONE" && j.progress.version_id) {
          window.clearInterval(pollRef.current!);
          router.push(`/workspace/${j.progress.version_id}`);
        }
        if (j.progress.stage === "FAILED") window.clearInterval(pollRef.current!);
      } catch {
        /* keep polling; a transient failure should not kill the view */
      }
    }, 400);
  }

  const langs = health?.languages ?? {};
  const swap = () => { setSourceLang(targetLang); setTargetLang(sourceLang); };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Panel title="Document" bodyClassName="p-4">
          <DropZone
            onFile={(file) => ingest(() => api.upload(file))}
            disabled={Boolean(busy)}
            limitMb={health?.limits.max_upload_mb ?? 50}
          />
          {doc && (
            <div className="mt-4 flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <FileText size={14} className="text-muted" />
                <span className="font-medium">{doc.filename}</span>
                <Badge>{doc.page_count} pages</Badge>
                <Badge>{(doc.size_bytes / 1024).toFixed(0)} KB</Badge>
                {doc.is_scanned && <Badge tone="warn">scanned · OCR</Badge>}
                <span className="font-mono text-2xs text-muted">
                  {doc.sha256.slice(0, 12)}
                </span>
              </div>
              <div className="grid gap-3 sm:grid-cols-[1fr_auto_1fr]">
                <Field
                  label="Source language"
                  hint={analysis
                    ? `detected ${(analysis.source_lang_confidence * 100).toFixed(0)}% confident — override if wrong`
                    : undefined}
                >
                  <Select value={sourceLang}
                          onChange={(e) => setSourceLang(e.target.value)}>
                    {Object.entries(langs).map(([code, l]) => (
                      <option key={code} value={code}>{l.name}</option>
                    ))}
                  </Select>
                </Field>
                <div className="flex items-end pb-0.5">
                  <Button variant="outline" onClick={swap} title="Swap languages">⇄</Button>
                </div>
                <Field label="Target language"
                       hint={langs[targetLang]
                         ? `mean expansion ×${langs[targetLang].expansion}${langs[targetLang].rtl ? " · right-to-left" : ""}`
                         : undefined}>
                  <Select value={targetLang}
                          onChange={(e) => setTargetLang(e.target.value)}>
                    {Object.entries(langs).map(([code, l]) => (
                      <option key={code} value={code}>{l.name}</option>
                    ))}
                  </Select>
                </Field>
              </div>

              <button
                className="self-start text-2xs uppercase tracking-wider text-muted underline decoration-dotted"
                onClick={() => setSettingsOpen((v) => !v)}
              >
                {settingsOpen ? "Hide" : "Show"} settings
              </button>
              {settingsOpen && (
                <div className="grid gap-3 rounded border border-[var(--line)] p-3 sm:grid-cols-2">
                  <Field label="Style">
                    <Select value={style} onChange={(e) => setStyle(e.target.value)}>
                      {STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
                    </Select>
                  </Field>
                  <Field label="Glossary" hint="one term=translation per line; locked verbatim">
                    <textarea
                      value={glossaryText}
                      onChange={(e) => setGlossaryText(e.target.value)}
                      rows={3}
                      className="rounded hairline bg-[var(--panel)] p-2 text-xs font-mono"
                      placeholder="API=API&#10;Layout Preservation=Layouterhaltung"
                    />
                  </Field>
                  <div className="sm:col-span-2 flex flex-wrap gap-3">
                    {Object.entries(options).map(([key, value]) => (
                      <label key={key} className="flex items-center gap-1.5 text-2xs">
                        <input
                          type="checkbox"
                          checked={value}
                          onChange={(e) =>
                            setOptions((o) => ({ ...o, [key]: e.target.checked }))}
                        />
                        {key.replace(/_/g, " ")}
                      </label>
                    ))}
                  </div>
                </div>
              )}

              {analysis && (
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(analysis.element_counts)
                    .filter(([, n]) => n > 0)
                    .sort((a, b) => b[1] - a[1])
                    .map(([kind, n]) => (
                      <Badge key={kind}>{kind.replace(/_/g, " ")} {n}</Badge>
                    ))}
                </div>
              )}

              <div className="flex items-center gap-2">
                <Button variant="accent" size="md" onClick={start}
                        disabled={Boolean(busy) || !doc.page_count}>
                  Translate
                </Button>
                {busy && <span className="text-2xs text-muted">{busy}…</span>}
              </div>
            </div>
          )}
          {error && (
            <p className="mt-3 rounded border border-[var(--bad)] px-2 py-1.5 text-2xs text-[var(--bad)]">
              {error}
            </p>
          )}
          {job && (
            <div className="mt-4 border-t border-[var(--line)] pt-3">
              <StageMachine progress={job.progress} />
            </div>
          )}
        </Panel>

        <Panel title="Environment" bodyClassName="p-3">
          {!health && <p className="text-2xs text-muted">Backend unreachable.</p>}
          {health && (
            <dl className="flex flex-col gap-1.5 text-2xs">
              <Row label="Provider" value={health.provider} />
              <Row label="Fonts" value={`${health.fonts.faces} faces`}
                   tone={health.fonts.missing_scripts.length ? "bad" : "ok"} />
              {health.fonts.missing_scripts.length > 0 && (
                <Row label="Missing scripts"
                     value={health.fonts.missing_scripts.join(", ")} tone="bad" />
              )}
              <Row label="OCR"
                   value={health.ocr.available
                     ? `tesseract · ${health.ocr.languages.join(", ")}`
                     : "unavailable"}
                   tone={health.ocr.available ? "ok" : "warn"} />
              <Row label="Limits"
                   value={`${health.limits.max_upload_mb}MB · ${health.limits.max_pages} pages`} />
            </dl>
          )}
        </Panel>
      </div>

      <Panel title="Bundled samples"
             right={<span className="text-2xs text-muted">
               no API key required — the mock provider stresses the layout engine
             </span>}
             bodyClassName="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-4">
        {samples.map((s) => (
          <article key={s.name}
                   className="flex flex-col gap-2 rounded border border-[var(--line)] p-3">
            <div className="flex items-start justify-between gap-2">
              <h3 className="text-xs font-semibold">{s.stem.replace(/-/g, " ")}</h3>
              <FlaskConical size={13} className="text-muted" />
            </div>
            <div className="flex flex-wrap gap-1">
              {s.page_count && <Badge>{s.page_count}pp</Badge>}
              {s.source_lang && <Badge tone="accent">{s.source_lang}</Badge>}
              {s.is_scanned && <Badge tone="warn">scanned</Badge>}
            </div>
            <p className="text-2xs leading-relaxed text-muted">{s.notes}</p>
            <Button className="self-start" size="xs"
                    disabled={Boolean(busy)}
                    onClick={() => ingest(() => api.fromSample(s.name))}>
              <Upload size={11} /> TRY DEMO
            </Button>
          </article>
        ))}
        {samples.length === 0 && (
          <p className="text-2xs text-muted">
            No samples on disk. Run <code className="font-mono">make samples</code>.
          </p>
        )}
      </Panel>
    </div>
  );
}

function Row({ label, value, tone }: {
  label: string; value: string; tone?: "ok" | "warn" | "bad";
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd className={cn("text-right",
        tone === "ok" && "text-[var(--ok)]",
        tone === "warn" && "text-[var(--warn)]",
        tone === "bad" && "text-[var(--bad)]")}>{value}</dd>
    </div>
  );
}

function DropZone({ onFile, disabled, limitMb }: {
  onFile: (file: File) => void; disabled?: boolean; limitMb: number;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const file = e.dataTransfer.files?.[0];
        if (file) onFile(file);
      }}
      className={cn(
        "flex flex-col items-center justify-center gap-1.5 rounded border border-dashed",
        "px-6 py-8 text-center",
        over ? "border-[var(--accent)] bg-[var(--surface)]" : "border-[var(--line)]",
        disabled && "opacity-50",
      )}
    >
      <Upload size={16} className="text-muted" />
      <p className="text-xs">
        Drop a PDF here, or{" "}
        <button className="underline decoration-dotted"
                onClick={() => input.current?.click()} disabled={disabled}>
          choose a file
        </button>
      </p>
      <p className="text-2xs text-muted">
        PDF only · up to {limitMb}MB · scanned pages go through OCR
      </p>
      <input
        ref={input} type="file" accept="application/pdf" className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onFile(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}
