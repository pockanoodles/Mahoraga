"use client";

// kaitosoeno.com's own menu: the pill at the bottom centre that every page of
// the site carries, so this page reads as part of the site. Copied from
// personal-website/components/menu.tsx and kept identical in size, type and
// behaviour; its sizes are in the site's fluid rem (--site-rem in globals.css),
// not this page's. If the site's menu changes shape, change this copy and
// Erwin's (erwin/site) together.
//
// The links are plain anchors on purpose: they leave this app for the site, so
// they must not get the /mahoraga base path a Next <Link> would add.
import { useEffect, useRef, useState } from "react";
import type { Dict } from "@/content/copy";

export function SiteMenu({ t }: { t: Dict["menu"] }) {
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onClick = (e: MouseEvent) => {
      if (!panel.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  return (
    <div ref={panel} className="site-menu">
      <div id="site-menu" hidden={!open} className="site-menu__drawer">
        <ul>
          {t.items.map((item) => (
            <li key={item.href}>
              <a href={item.href} className="site-menu__link">
                {item.label}
              </a>
            </li>
          ))}
        </ul>
      </div>
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-controls="site-menu" className="site-menu__button">
        {open ? t.close : t.menu}
        <span aria-hidden className="site-menu__glyph">
          {open ? "×" : "↑"}
        </span>
      </button>
    </div>
  );
}
