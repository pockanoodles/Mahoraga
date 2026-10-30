// The move between kaitosoeno.com/portfolio and this page. They are different
// apps on one origin, so it is a cross-document view transition: the row
// clicked on the portfolio grows into this page (vt-surface), its name flies
// into the wordmark here (vt-name), and the site's menu pill holds still
// (vt-menu). The names are the contract with personal-website
// (components/view-transitions.tsx there, and sigwald/site's twin of this).
// The new document styles the animation, so the way in is in this app's
// globals.css and the way back is in personal-website's.
//
// Only moves to and from the portfolio animate; the language switch and every
// other load are skipped. `pagereveal` has to be listening before the first
// frame, so this is a plain inline script in <head>.
const script = `(() => {
  const html = document.documentElement;
  const portfolio = (url) => !!url && /^\\/(ja\\/)?portfolio\\/?$/.test(new URL(url, location.href).pathname);

  // Where a click is about to go, for browsers whose pageswap carries no
  // activation (no Navigation API).
  let clicked = null;
  addEventListener("click", (e) => {
    const a = e.target.closest?.("a[href]");
    clicked = a ? a.href : null;
  }, true);

  addEventListener("pageswap", (e) => {
    if (!e.viewTransition) return;
    if (!portfolio(e.activation?.entry?.url || clicked)) return e.viewTransition.skipTransition();
    html.dataset.vt = "out";
  });

  addEventListener("pagereveal", (e) => {
    delete html.dataset.vt;
    if (!e.viewTransition) return;
    if (!portfolio(window.navigation?.activation?.from?.url || document.referrer)) return e.viewTransition.skipTransition();
    html.dataset.vt = "in";
    e.viewTransition.finished.finally(() => delete html.dataset.vt);
  });
})();`;

export function ViewTransitionScript() {
  return <script dangerouslySetInnerHTML={{ __html: script }} />;
}

// What the portfolio's row grows into: the page's own ground, behind
// everything, and indistinguishable from it once the transition is over.
export function ViewTransitionSurface() {
  return <div className="vt-surface" aria-hidden />;
}
