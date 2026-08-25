"use client";
// pdf.js setup. The worker is copied into /public at install time so the viewer
// works with the network off -- no CDN, which the offline demo requires.
import type { PDFDocumentProxy } from "pdfjs-dist";
import * as pdfjs from "pdfjs-dist";

let configured = false;

export async function loadPdf(url: string): Promise<PDFDocumentProxy> {
  if (!configured) {
    pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
    configured = true;
  }
  return pdfjs.getDocument({ url, isEvalSupported: false }).promise;
}

export type { PDFDocumentProxy };
