"use client";

// kaitosoeno.com's own menu: the pill at the bottom centre that every page of
// the site carries, so this page reads as part of the site. Ported from
// personal-website/components/menu.tsx as of 348652e ("the menu pill grows
// into its drawer"): one surface whose width and height animate from the pill
// into the drawer, with the button as its bottom row. Its sizes are in the
// site's fluid rem (--site-rem in globals.css), except the button row, which
// is a true 44px: the site's own comment and BAR constant say 44, but its
// `h-11` is 2.75rem on a fluid root and renders 33-35px below 1920px wide.
// If the site's menu changes shape, change this copy and Sigwald's together
// (sigwald/site/components/SiteMenu.tsx is the twin).
//
// The links are plain anchors on purpose: they leave this app for the site, so
// they must not get the /mahoraga base path a Next <Link> would add.
import { useEffect, useRef, useState } from "react";
import type { Dict } from "@/content/copy";

const BAR = 44; // the button row, in px

export function SiteMenu({ t }: { t: Dict["menu"] }) {
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const drawer = useRef<HTMLDivElement>(null);
  const label = useRef<HTMLSpanElement>(null);
  const [size, setSize] = useState<{ labelW: number; drawerW: number; drawerH: number } | null>(null);

  // A size transition needs pixel endpoints at both ends, and `auto` is not
  // one. The observer fires on attach and again when the font swaps in.
  useEffect(() => {
    const d = drawer.current;
    const l = label.current;
    if (!d || !l) return;
    const ro = new ResizeObserver(() => setSize({ labelW: l.offsetWidth, drawerW: d.offsetWidth, drawerH: d.offsetHeight }));
    ro.observe(d);
    ro.observe(l);
    return () => ro.disconnect();
  }, []);

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
      <div
        className="site-menu__surface"
        style={
          size
            ? {
                width: (open ? Math.max(size.drawerW, size.labelW) : size.labelW) + 2,
                height: (open ? size.drawerH + BAR : BAR) + 2,
              }
            : undefined
        }
      >
        <div id="site-menu" ref={drawer} className="site-menu__drawer" data-open={open || undefined} inert={!open}>
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
          <span ref={label} className="site-menu__label">
            {t.menu}
            <span aria-hidden className="site-menu__glyph" data-open={open || undefined}>
              ↑
            </span>
          </span>
        </button>
      </div>
    </div>
  );
}
