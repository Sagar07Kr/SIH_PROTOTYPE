// pdf.js needs its worker as a static asset. Copying it at install time keeps
// the viewer working offline -- no CDN, which matters because the whole demo
// must run with the network off.
import { copyFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";

const candidates = [
  "node_modules/pdfjs-dist/build/pdf.worker.min.mjs",
  "node_modules/pdfjs-dist/build/pdf.worker.mjs",
];
const dest = resolve("public/pdf.worker.min.mjs");
const src = candidates.map((c) => resolve(c)).find((p) => existsSync(p));
if (!src) {
  console.warn("[pdf.js] worker not found; the viewer will fall back to server-rendered pages");
} else {
  await mkdir(dirname(dest), { recursive: true });
  await copyFile(src, dest);
  console.log("[pdf.js] worker copied to public/");
}
