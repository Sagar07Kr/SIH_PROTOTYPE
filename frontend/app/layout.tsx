import type { Metadata } from "next";

import "./globals.css";
import { ThemeToggle } from "@/components/ThemeToggle";

export const metadata: Metadata = {
  title: "LayoutLoom — layout-preserving PDF translation",
  description:
    "Translate a PDF into another language and keep its design, geometry and typographic identity.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen">
        <header className="sticky top-0 z-30 flex h-11 items-center justify-between
                           border-b border-[var(--line)] bg-[var(--panel)] px-4">
          <div className="flex items-baseline gap-2.5">
            <a href="/" className="text-sm font-semibold tracking-tight">LayoutLoom</a>
            <span className="text-2xs text-muted">
              layout-preserving PDF translation
            </span>
          </div>
          <ThemeToggle />
        </header>
        <main className="mx-auto max-w-[1800px] p-4">{children}</main>
      </body>
    </html>
  );
}
