"use client";

// The site's own bar (Design 03): mark and name at the left, its three pages in
// the true centre, the log's last update, the language and the repository at
// the right.
// Over the research page's hero it is clear, and it frosts once the page
// scrolls under it, which an IntersectionObserver on the hero decides, not a
// scroll listener. The other pages have no hero, so there it starts frosted.
import { useEffect, useRef, useState } from "react";
import type { Dict } from "@/content/copy";
import { Mark } from "./Mark";

export type NavLink = { href: string; label: string; current: boolean };

// A function can't cross into a client component, so the date's label comes
// formatted from the server, next to the date the <time> element carries, and
// every href comes resolved for the page and language being served.
export function LocalNav({
  t,
  updated,
  updatedLabel,
  repo,
  home,
  links,
  other,
  hero,
}: {
  t: Pick<Dict["nav"], "github" | "label">;
  updated: string;
  updatedLabel: string;
  repo: string;
  home: string;
  links: NavLink[];
  other: { href: string; label: string; lang: string };
  hero: boolean;
}) {
  const bar = useRef<HTMLElement>(null);
  const [overHero, setOverHero] = useState(hero);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const el = document.querySelector("[data-hero]");
    if (!el || !bar.current) return;
    const io = new IntersectionObserver(([e]) => setOverHero(e.isIntersecting), {
      rootMargin: `-${bar.current.offsetHeight}px 0px 0px 0px`,
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header ref={bar} className="lnav" data-clear={(overHero && !open) || undefined} data-open={open || undefined}>
      <div className="lnav__row">
        <a href={hero ? "#top" : home} className="lnav__brand" aria-label={hero ? `${t.label}, back to the top` : undefined}>
          <Mark size={22} />
          <span>Mahoraga</span>
        </a>
        <nav className="lnav__links" aria-label={t.label}>
          {links.map((l) => (
            <a key={l.href} href={l.href} aria-current={l.current ? "page" : undefined}>
              {l.label}
            </a>
          ))}
        </nav>
        <div className="lnav__end">
          <time className="lnav__meta" dateTime={updated}>
            {updatedLabel}
          </time>
          <a className="lnav__lang" href={other.href} hrefLang={other.lang} lang={other.lang}>
            {other.label}
          </a>
          <a className="lnav__gh" href={repo} target="_blank" rel="noreferrer">
            {t.github} <span aria-hidden>↗</span>
          </a>
          <button
            type="button"
            className="lnav__toggle"
            aria-expanded={open}
            aria-controls="lnav-panel"
            aria-label={open ? "Close" : "Pages"}
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
            <a key={l.href} href={l.href} aria-current={l.current ? "page" : undefined} onClick={() => setOpen(false)}>
              {l.label}
            </a>
          ))}
          <a href={repo} target="_blank" rel="noreferrer" onClick={() => setOpen(false)}>
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
