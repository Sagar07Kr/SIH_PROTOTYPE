"use client";
import { RotateCcw, Save, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";

import { RungBadge } from "@/components/ComparisonViewer";
import { Badge, Button, Tooltip } from "@/components/ui/primitives";
import { RUNG_LABELS, severityClass } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { Segment, Validation } from "@/types/api";

/** Everything known about one block, in plain words, plus the three actions
 *  that can change it. */
export function Inspector({
  segment, busy, onEdit, onRegenerate,
}: {
  segment: Segment | null;
  busy: boolean;
  onEdit: (segmentId: string, text: string) => void;
  onRegenerate: (segmentId: string) => void;
}) {
  const [draft, setDraft] = useState("");
  useEffect(() => { setDraft(segment?.target ?? ""); }, [segment?.id, segment?.target]);

  if (!segment) {
    return (
      <p className="p-3 text-2xs leading-relaxed text-muted">
        Select a block in any panel — thumbnail, page or text list — and it is
        selected in all of them.
      </p>
    );
  }
  const sub = segment.font_substitution;
  const dirty = draft !== segment.target;
  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="accent">{segment.type.replace(/_/g, " ")}</Badge>
        <Badge>page {segment.page + 1}</Badge>
        <RungBadge rung={segment.fit_rung} />
        {segment.edited && <Badge tone="warn">edited</Badge>}
        {segment.status !== "OK" && <Badge tone="warn">{segment.status}</Badge>}
      </div>

      <Detail label="Concession">
        <span className="text-ink">
          {RUNG_LABELS[segment.fit_rung ?? 0]}
          {segment.applied_size && segment.original_size
            ? ` · ${segment.applied_size.toFixed(1)}pt of ${segment.original_size.toFixed(1)}pt`
            : ""}
        </span>
      </Detail>
      <Detail label="Box">
        <span className="font-mono text-2xs">
          {segment.bbox ? segment.bbox.map((v) => v.toFixed(1)).join(", ") : "—"}
        </span>
      </Detail>
      <Detail label="Confidence">
        <span className={cn("tabular",
          segment.confidence < 0.8 && "text-[var(--warn)]")}>
          {(segment.confidence * 100).toFixed(1)}%
        </span>
      </Detail>
      {sub && (
        <Detail label="Font substitution">
          <Tooltip label={
            <div className="flex flex-col gap-1">
              <span>{sub.reason}</span>
              {sub.size_factor && (
                <span>x-height correction ×{sub.size_factor.toFixed(3)}</span>
              )}
              {sub.file && <span className="font-mono">{sub.file}</span>}
            </div>
          }>
            <span className="cursor-help underline decoration-dotted">
              {sub.original} → {sub.replacement ?? sub.family}
            </span>
          </Tooltip>
        </Detail>
      )}
      {segment.placeholders?.kinds &&
        Object.keys(segment.placeholders.kinds).length > 0 && (
        <Detail label="Protected spans">
          <div className="flex flex-wrap gap-1">
            {Object.entries(segment.placeholders.kinds).map(([token, kind]) => (
              <Tooltip key={token}
                       label={`${kind} · ${segment.placeholders.tokens?.[token] ?? ""}`}>
                <span className="cursor-help rounded-sm border border-[var(--line)]
                                 px-1 text-2xs">{kind.toLowerCase()}</span>
              </Tooltip>
            ))}
          </div>
        </Detail>
      )}

      <div className="flex flex-col gap-1">
        <span className="text-2xs font-medium uppercase tracking-wider text-muted">
          Source
        </span>
        <p className="rounded border border-[var(--line)] bg-[var(--surface)] p-2
                      text-xs leading-relaxed text-muted">{segment.source}</p>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-2xs font-medium uppercase tracking-wider text-muted">
          Target
        </span>
        <textarea
          value={draft}
          rows={5}
          onChange={(e) => setDraft(e.target.value)}
          className="rounded hairline bg-[var(--panel)] p-2 text-xs leading-relaxed"
        />
      </div>

      {segment.issues.length > 0 && (
        <ul className="flex flex-col gap-1">
          {segment.issues.map((i, n) => (
            <li key={n} className={cn("text-2xs leading-relaxed", severityClass(i.severity))}>
              <span className="font-mono">{i.code}</span> — {i.message}
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap gap-1.5">
        <Button variant="accent" size="xs" disabled={busy || !dirty}
                onClick={() => onEdit(segment.id, draft)}>
          <Save size={11} /> Save &amp; re-place
        </Button>
        <Button size="xs" disabled={busy}
                onClick={() => onRegenerate(segment.id)}>
          <Wand2 size={11} /> Regenerate
        </Button>
        <Button variant="ghost" size="xs" disabled={busy || !dirty}
                onClick={() => setDraft(segment.target)}>
          <RotateCcw size={11} /> Reset
        </Button>
      </div>
      <p className="text-2xs leading-relaxed text-muted">
        Saving creates a new version and re-renders only page {segment.page + 1}.
      </p>
    </div>
  );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-2xs">
      <span className="uppercase tracking-wider text-muted">{label}</span>
      <span className="text-right">{children}</span>
    </div>
  );
}

export function ScorePanel({ validation }: { validation: Validation | null }) {
  if (!validation) {
    return <p className="p-3 text-2xs text-muted">Validation has not run yet.</p>;
  }
  const errors = validation.issues.filter((i) => i.severity === "ERROR");
  const warnings = validation.issues.filter((i) => i.severity === "WARNING");
  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="grid grid-cols-2 gap-2">
        {Object.entries(validation.scores).map(([key, score]) => (
          <Tooltip key={key} label={
            <div className="flex flex-col gap-1">
              <span className="font-medium">How this is computed</span>
              {score.terms.map((t) => (
                <span key={t.name} className="tabular">
                  {t.name}: {(t.value * 100).toFixed(1)}% × {t.weight} ={" "}
                  {t.contribution.toFixed(2)}
                </span>
              ))}
              <span className="tabular">total = {score.value}</span>
            </div>
          }>
            <div className="cursor-help rounded border border-[var(--line)] p-2">
              <div className="text-lg font-semibold tabular leading-none">
                {score.value}
                <span className="text-xs font-normal text-muted">%</span>
              </div>
              <div className="mt-1 text-2xs uppercase tracking-wider text-muted">
                {key.replace(/_/g, " ")}
              </div>
            </div>
          </Tooltip>
        ))}
      </div>

      <dl className="flex flex-col divide-y divide-[var(--line)] text-2xs">
        {validation.metrics.map((m) => (
          <div key={m.key} className="flex items-baseline justify-between gap-3 py-1">
            <Tooltip label={
              <div className="flex flex-col gap-1">
                <span>{m.derivation}</span>
                {m.target && <span>target {m.target}</span>}
                {Object.keys(m.detail ?? {}).length > 0 && (
                  <span className="font-mono">{JSON.stringify(m.detail).slice(0, 300)}</span>
                )}
              </div>
            }>
              <dt className="cursor-help text-muted underline decoration-dotted">
                {m.label}
              </dt>
            </Tooltip>
            <dd className="tabular">
              {typeof m.value === "boolean"
                ? (m.value ? "yes" : "no")
                : Number.isInteger(m.value)
                  ? m.value
                  : (m.value as number).toFixed(4)}
            </dd>
          </div>
        ))}
      </dl>

      {(errors.length > 0 || warnings.length > 0) && (
        <div className="flex flex-col gap-1">
          <span className="text-2xs font-medium uppercase tracking-wider text-muted">
            Issues
          </span>
          <ul className="flex flex-col gap-1">
            {[...errors, ...warnings].slice(0, 40).map((i, n) => (
              <li key={n} className={cn("text-2xs leading-relaxed", severityClass(i.severity))}>
                <span className="font-mono">{i.code}</span>
                {typeof i.page === "number" && ` p${i.page + 1}`} — {i.message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
