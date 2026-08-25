"use client";
// Minimal UI primitives in the shadcn/ui idiom: Radix behaviour, Tailwind
// styling, no component generator. Kept in one file because there are six of
// them and they are not going to grow.
import * as SliderPrimitive from "@radix-ui/react-slider";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import * as React from "react";

import { cn } from "@/lib/cn";

export function Button({
  className, variant = "default", size = "sm", ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "ghost" | "outline" | "accent";
  size?: "sm" | "xs" | "md";
}) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded font-medium",
        "disabled:opacity-45 disabled:pointer-events-none select-none",
        "focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-[var(--accent)]",
        size === "xs" && "h-6 px-2 text-2xs",
        size === "sm" && "h-7 px-2.5 text-xs",
        size === "md" && "h-9 px-3.5 text-sm",
        variant === "default" &&
          "bg-[var(--panel)] hairline hover:border-[var(--accent)] text-ink",
        variant === "outline" && "hairline bg-transparent hover:bg-[var(--panel)]",
        variant === "ghost" && "hover:bg-[var(--panel)] text-muted hover:text-ink",
        variant === "accent" &&
          "bg-[var(--accent)] text-white hover:opacity-90 border border-transparent",
        className,
      )}
      {...props}
    />
  );
}

export function Badge({
  className, tone = "neutral", children, ...rest
}: React.HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "ok" | "warn" | "bad" | "accent";
}) {
  return (
    <span
      {...rest}
      className={cn(
        "inline-flex items-center rounded-sm px-1.5 py-0.5 text-2xs font-medium",
        "border tabular",
        tone === "neutral" && "border-[var(--line)] text-muted",
        tone === "ok" && "border-[var(--ok)] text-[var(--ok)]",
        tone === "warn" && "border-[var(--warn)] text-[var(--warn)]",
        tone === "bad" && "border-[var(--bad)] text-[var(--bad)]",
        tone === "accent" && "border-[var(--accent)] text-[var(--accent)]",
        className,
      )}
    >
      {children}
    </span>
  );
}

export const Tabs = TabsPrimitive.Root;

export function TabsList({ className, ...props }: TabsPrimitive.TabsListProps) {
  return (
    <TabsPrimitive.List
      className={cn("inline-flex h-7 items-center gap-0.5 rounded border",
        "border-[var(--line)] bg-[var(--panel)] p-0.5", className)}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: TabsPrimitive.TabsTriggerProps) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "rounded-sm px-2.5 text-xs h-6 text-muted",
        "data-[state=active]:bg-[var(--surface)] data-[state=active]:text-ink",
        "data-[state=active]:shadow-[inset_0_0_0_1px_var(--line)]",
        className)}
      {...props}
    />
  );
}

export const TabsContent = TabsPrimitive.Content;

export function Slider({ className, ...props }: SliderPrimitive.SliderProps) {
  return (
    <SliderPrimitive.Root
      className={cn("relative flex h-4 w-full touch-none items-center", className)}
      {...props}
    >
      <SliderPrimitive.Track className="relative h-[3px] w-full grow rounded bg-[var(--line)]">
        <SliderPrimitive.Range className="absolute h-full rounded bg-[var(--accent)]" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb
        aria-label="value"
        className="block h-3 w-3 rounded-full border border-[var(--accent)] bg-[var(--panel)]
                   focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--accent)]"
      />
    </SliderPrimitive.Root>
  );
}

export function Tooltip({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={120}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content
            sideOffset={6}
            className="z-50 max-w-sm rounded border border-[var(--line)] bg-[var(--panel)]
                       px-2.5 py-2 text-2xs leading-relaxed text-ink shadow-sm"
          >
            {label}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

export function Panel({
  title, right, children, className, bodyClassName,
}: {
  title?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode;
  className?: string; bodyClassName?: string;
}) {
  return (
    <section className={cn("panel rounded flex flex-col min-h-0", className)}>
      {title !== undefined && (
        <header className="flex items-center justify-between gap-2 border-b
                           border-[var(--line)] px-3 py-1.5">
          <h2 className="text-2xs font-semibold uppercase tracking-wider text-muted">
            {title}
          </h2>
          {right}
        </header>
      )}
      <div className={cn("min-h-0 flex-1 overflow-auto", bodyClassName)}>{children}</div>
    </section>
  );
}

export function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-2xs font-medium uppercase tracking-wider text-muted">
        {label}
      </span>
      {children}
      {hint && <span className="text-2xs text-muted">{hint}</span>}
    </label>
  );
}

export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn("h-8 rounded hairline bg-[var(--panel)] px-2 text-xs text-ink",
        className)}
      {...props}
    />
  );
}
