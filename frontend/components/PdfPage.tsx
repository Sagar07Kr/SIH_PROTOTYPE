"use client";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { useEffect, useRef, useState } from "react";

import { loadPdf } from "@/lib/pdf";

/** One rendered PDF page. Falls back to the server's PNG renderer if pdf.js
 *  cannot start (no worker asset, unsupported browser), so the comparison view
 *  never degrades to an empty box. */
export function PdfPage({
  url, page, width, fallbackSrc, className, onGeometry,
}: {
  url: string;
  page: number;
  width: number;
  fallbackSrc?: string;
  className?: string;
  onGeometry?: (g: { widthPt: number; heightPt: number; scale: number }) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  const [height, setHeight] = useState<number>(Math.round(width * 1.414));

  useEffect(() => {
    let cancelled = false;
    let doc: PDFDocumentProxy | null = null;

    (async () => {
      try {
        doc = await loadPdf(url);
        if (cancelled) return;
        const pdfPage = await doc.getPage(page + 1);
        const base = pdfPage.getViewport({ scale: 1 });
        const scale = width / base.width;
        const viewport = pdfPage.getViewport({ scale });
        const el = canvas.current;
        if (!el) return;
        const ratio = window.devicePixelRatio || 1;
        el.width = Math.floor(viewport.width * ratio);
        el.height = Math.floor(viewport.height * ratio);
        el.style.width = `${viewport.width}px`;
        el.style.height = `${viewport.height}px`;
        setHeight(viewport.height);
        const ctx = el.getContext("2d");
        if (!ctx) return;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        await pdfPage.render({ canvasContext: ctx, viewport }).promise;
        onGeometry?.({ widthPt: base.width, heightPt: base.height, scale });
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      doc?.destroy().catch(() => undefined);
    };
  }, [url, page, width, onGeometry]);

  if (failed && fallbackSrc) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={fallbackSrc} alt={`page ${page + 1}`} width={width}
           className={className} />
    );
  }
  return (
    <canvas ref={canvas} className={className}
            style={{ width, height, background: "#fff" }} />
  );
}
