import { useCallback, useEffect, useState } from "react";

export type FontScheme = "default" | "inter" | "geist" | "manrope";

export interface FontSchemeInfo {
  id: FontScheme;
  label: string;
  sampleFamily: string;
}

export const FONT_SCHEMES: FontSchemeInfo[] = [
  { id: "default", label: "DM Sans / Bricolage", sampleFamily: "DM Sans" },
  { id: "inter", label: "Inter", sampleFamily: "Inter" },
  { id: "geist", label: "Geist", sampleFamily: "Geist" },
  { id: "manrope", label: "Manrope", sampleFamily: "Manrope" },
];

const STORAGE_KEY = "mahoraga:font";

function initial(): FontScheme {
  if (typeof window === "undefined") return "default";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  const valid = FONT_SCHEMES.map((s) => s.id) as string[];
  return (stored && valid.includes(stored) ? stored : "default") as FontScheme;
}

export function useFont(): {
  scheme: FontScheme;
  set: (s: FontScheme) => void;
  cycle: () => void;
  current: FontSchemeInfo;
} {
  const [scheme, setScheme] = useState<FontScheme>(() => initial());

  useEffect(() => {
    const root = document.documentElement;
    FONT_SCHEMES.forEach((s) => {
      root.classList.remove(`font-${s.id}`);
    });
    if (scheme !== "default") {
      root.classList.add(`font-${scheme}`);
    }
    window.localStorage.setItem(STORAGE_KEY, scheme);
  }, [scheme]);

  const set = useCallback((s: FontScheme) => setScheme(s), []);

  const cycle = useCallback(() => {
    setScheme((current) => {
      const ids = FONT_SCHEMES.map((s) => s.id);
      const idx = ids.indexOf(current);
      return ids[(idx + 1) % ids.length];
    });
  }, []);

  const current = FONT_SCHEMES.find((s) => s.id === scheme) ?? FONT_SCHEMES[0];
  return { scheme, set, cycle, current };
}
