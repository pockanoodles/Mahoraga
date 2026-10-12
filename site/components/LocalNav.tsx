"use client";

// The product's own bar (Design 03): mark and name at the left, the page's
// sections in the true centre, the repository and the language at the right.
// It is clear over the hero and frosts once the page scrolls under it, which an
// IntersectionObserver on the hero decides, not a scroll listener.
import { useEffect, useRef, useState } from "react";
import type { Dict, Locale } from "@/content/copy";
import { REPO_URL } from "@/lib/data";
import { Mark } from "./Mark";

const SECTIONS = ["how", "evidence", "app", "install"] as const;

export function LocalNav({ t, locale, version }: { t: Dict["nav"]; locale: Locale; version: string | null }) {
  const bar = useRef<HTMLElement>(null);
  const [overHero, setOverHero] = useState(true);
  const [current, setCurrent] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const hero = document.querySelector("[data-hero]");
    if (!hero || !bar.current) return;
    const io = new IntersectionObserver(([e]) => setOverHero(e.isIntersecting), {
      rootMargin: `-${bar.current.offsetHeight}px 0px 0px 0px`,
    });
    io.observe(hero);
    return () => io.disconnect();
  }, []);

  // The section whose top has passed the middle of the screen is the current one.
  useEffect(() => {
    const els = SECTIONS.map((id) => document.getElementById(id)).filter(Boolean) as HTMLElement[];
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) if (e.isIntersecting) setCurrent(e.target.id);
      },
      { rootMargin: "-50% 0px -49% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const other = locale === "en" ? { href: "/mahoraga/ja/", label: "日本語", lang: "ja" } : { href: "/mahoraga/", label: "English", lang: "en" };
  const links = SECTIONS.map((id) => ({ id, label: t[id] }));

  return (
    <header ref={bar} className="lnav" data-clear={(overHero && !open) || undefined} data-open={open || undefined}>
      <div className="lnav__row">
        <a href="#top" className="lnav__brand" aria-label={`${t.label}, back to the top`}>
          <Mark size={22} />
          <span>Mahoraga</span>
        </a>
        <nav className="lnav__links" aria-label={t.label}>
          {links.map((l) => (
            <a key={l.id} href={`#${l.id}`} aria-current={current === l.id ? "true" : undefined}>
              {l.label}
            </a>
          ))}
        </nav>
        <div className="lnav__end">
          {version && <span className="lnav__meta">v{version}</span>}
          <a className="lnav__lang" href={other.href} hrefLang={other.lang} lang={other.lang}>
            {other.label}
          </a>
          <a className="lnav__gh" href={REPO_URL} target="_blank" rel="noreferrer">
            {t.github} <span aria-hidden>↗</span>
          </a>
          <button
            type="button"
            className="lnav__toggle"
            aria-expanded={open}
            aria-controls="lnav-panel"
            aria-label={open ? "Close" : "Sections"}
            onClick={() => setOpen((o) => !o)}
          >
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
              {open ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 8h16M4 16h16" />}
            </svg>
          </button>
        </div>
      </div>
      {open && (
        <nav id="lnav-panel" className="lnav__panel" aria-label={t.label}>
          {links.map((l) => (
            <a key={l.id} href={`#${l.id}`} onClick={() => setOpen(false)}>
              {l.label}
            </a>
          ))}
          <a href={REPO_URL} target="_blank" rel="noreferrer" onClick={() => setOpen(false)}>
            {t.github} ↗
          </a>
          <a href={other.href} hrefLang={other.lang} lang={other.lang}>
            {other.label}
          </a>
        </nav>
      )}
    </header>
  );
}
