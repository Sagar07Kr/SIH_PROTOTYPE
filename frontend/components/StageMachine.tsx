"use client";
import { Check, CircleDot, Minus, X } from "lucide-react";

import { cn } from "@/lib/cn";
import type { JobProgress } from "@/types/api";

// The job's own state machine, rendered directly. There is no generic spinner
// anywhere in this application: a reader can see that OCR ran, which page
// translation is on, and that validation has not started yet.
export function StageMachine({ progress }: { progress: JobProgress }) {
  return (
    <div className="flex flex-col gap-2">
      <ol className="flex flex-wrap gap-1">
        {progress.stages.map((s) => (
          <li
            key={s.stage}
            className={cn(
              "flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-2xs tabular",
              s.status === "done" && "border-[var(--ok)] text-[var(--ok)]",
              s.status === "active" && "border-[var(--accent)] text-[var(--accent)]",
              s.status === "failed" && "border-[var(--bad)] text-[var(--bad)]",
              s.status === "skipped" && "border-[var(--line)] text-muted opacity-70",
              s.status === "pending" && "border-[var(--line)] text-muted",
            )}
            title={s.status + (s.ms ? ` · ${s.ms}ms` : "")}
          >
            {s.status === "done" && <Check size={10} />}
            {s.status === "active" && <CircleDot size={10} />}
            {s.status === "failed" && <X size={10} />}
            {s.status === "skipped" && <Minus size={10} />}
            <span>{s.stage.toLowerCase().replace(/_/g, " ")}</span>
            {typeof s.ms === "number" && s.ms > 0 && (
              <span className="opacity-60">{s.ms}ms</span>
            )}
          </li>
        ))}
      </ol>
      <div className="flex items-center justify-between gap-3 text-2xs text-muted">
        <span>{progress.message ?? progress.stage}</span>
        {progress.segments_total > 0 && (
          <span className="tabular">
            {progress.segments_done}/{progress.segments_total} segments
          </span>
        )}
      </div>
      {progress.segments_total > 0 && (
        <div className="h-[3px] w-full rounded bg-[var(--line)]">
          <div
            className="h-full rounded bg-[var(--accent)]"
            style={{
              width: `${Math.min(100, Math.round(
                (100 * progress.segments_done) / Math.max(1, progress.segments_total)))}%`,
            }}
          />
        </div>
      )}
      {progress.error && (
        <p className="rounded border border-[var(--bad)] px-2 py-1.5 text-2xs text-[var(--bad)]">
          <span className="font-mono">{progress.error.code}</span> — {progress.error.message}
          {progress.error.retryable && " (retryable)"}
        </p>
      )}
    </div>
  );
}
