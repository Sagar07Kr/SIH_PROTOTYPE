"use client";
import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/primitives";

// Both themes are explicit choices, stored locally. No system-preference
// guessing: a reviewer comparing renders needs the surface to stay put.
export function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    const saved = (window.localStorage.getItem("ll-theme") as "light" | "dark" | null);
    const initial = saved ?? "light";
    setTheme(initial);
    document.documentElement.dataset.theme = initial;
  }, []);

  function toggle() {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    window.localStorage.setItem("ll-theme", next);
  }

  return (
    <Button variant="ghost" size="xs" onClick={toggle} aria-label="Toggle theme">
      {theme === "light" ? <Moon size={13} /> : <Sun size={13} />}
      <span className="text-2xs">{theme === "light" ? "Dark" : "Light"}</span>
    </Button>
  );
}
