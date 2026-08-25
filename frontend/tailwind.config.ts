import type { Config } from "tailwindcss";

// A document workbench: neutral surfaces, one accent, tabular numerals.
// No gradients, no glow -- the page being translated is the only thing that
// should attract the eye.
const config: Config = {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface)",
        panel: "var(--panel)",
        ink: "var(--ink)",
        muted: "var(--muted)",
        line: "var(--line)",
        accent: "var(--accent)",
        ok: "var(--ok)",
        warn: "var(--warn)",
        bad: "var(--bad)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      borderRadius: { sm: "3px", DEFAULT: "4px", md: "6px" },
    },
  },
  plugins: [],
};
export default config;
