// Renders the social card (public/og.png) from the built page: its stylesheet,
// its self-hosted fonts, the hero's eyebrow and answer, and the hero chart's
// own markup. Nothing on the card is typed here, so it can't show a number or
// a sentence the page doesn't, and it changes only when the page does.
//
//   npm run build && npm run og     (then build again to ship the new card)
//
// Playwright serves dist/ to itself through page.route, so no server runs.
import { chromium } from "playwright";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const site = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(site, "dist");
const W = 1200;
const H = 630;

const page = readFileSync(join(dist, "mahoraga", "index.html"), "utf8");
const pick = (re, what) => {
  const m = page.match(re);
  if (!m) throw new Error(`the built page has no ${what}; build it first`);
  return m[1];
};
const htmlClass = pick(/<html[^>]*\sclass="([^"]+)"/, "<html> font classes");
const sheets = [...page.matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map((m) => m[1]);
const question = pick(/<h1[^>]*>([\s\S]*?)<\/h1>/, "h1 (the question)");
const eyebrow = pick(/<p class="hero__eyebrow">([\s\S]*?)<\/p>/, "hero eyebrow");
const answer = pick(/<p class="hero__statement">([\s\S]*?)<\/p>/, "hero answer");
const chart = pick(/(<figure class="standing"[\s\S]*?<\/figure>)/, "hero chart");
const mark = readFileSync(join(site, "public", "brand", "mark-dark.svg"), "utf8");

const card = `<!doctype html>
<html lang="en" data-theme="dark" class="${htmlClass}">
<head>
<meta charset="utf-8">
${sheets.map((href) => `<link rel="stylesheet" href="${href}">`).join("\n")}
<style>
  body { width: ${W}px; height: ${H}px; overflow: hidden; }
  .card { position: relative; isolation: isolate; display: grid; grid-template-columns: 31rem minmax(0, 1fr);
    grid-template-rows: auto minmax(0, 1fr); column-gap: 56px; width: ${W}px; height: ${H}px; padding: 52px 64px 60px; }
  .card__brand { display: flex; align-items: center; gap: 10px; font-size: 28px; font-weight: 600; letter-spacing: -0.02em; }
  .card__brand svg { width: 34px; height: 34px; }
  .card__url { justify-self: end; align-self: center; font-size: 19px; color: var(--mh-muted); }
  .card__text { grid-row: 2; align-self: end; }
  .card__ask { margin-bottom: 30px; font-size: 23px; font-weight: 500; line-height: 1.3; color: var(--mh-muted); text-wrap: balance; }
  .card__text .hero__statement { font-size: 50px; }
  .card__chart { grid-row: 2; align-self: center; }
  .card__chart .standing { --label: 8.5rem; --value: 4.25rem; --gap: 14px; --row: 18px; }
  .card__chart .standing__label { font-size: 13px; }
  .card__chart .standing__fit { font-size: 11px; }
  .card__chart .standing__pct { font-size: 21px; }
  .card__chart .standing__caption { display: none; }
</style>
</head>
<body>
<div class="card">
  <div class="hero__stage" aria-hidden="true"></div>
  <p class="card__brand">${mark}<span>Mahoraga</span></p>
  <p class="card__url">kaitosoeno.com/mahoraga</p>
  <div class="card__text">
    <p class="card__ask">${question}</p>
    <p class="hero__eyebrow">${eyebrow}</p>
    <p class="hero__statement">${answer}</p>
  </div>
  <div class="card__chart">${chart}</div>
</div>
</body>
</html>`;

const TYPES = { ".css": "text/css", ".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png" };
const browser = await chromium.launch();
const tab = await browser.newPage({ viewport: { width: W, height: H }, reducedMotion: "reduce" });
await tab.route("**/*", (route) => {
  const url = new URL(route.request().url());
  if (url.pathname === "/card") return route.fulfill({ contentType: "text/html", body: card });
  try {
    const body = readFileSync(join(dist, url.pathname));
    return route.fulfill({ contentType: TYPES[extname(url.pathname)] ?? "application/octet-stream", body });
  } catch {
    return route.fulfill({ status: 404, body: "" });
  }
});
await tab.goto("http://og.local/card", { waitUntil: "networkidle" });
await tab.evaluate(() => document.fonts.ready);
const png = await tab.screenshot({ clip: { x: 0, y: 0, width: W, height: H } });
await browser.close();

writeFileSync(join(site, "public", "og.png"), png);
mkdirSync(join(dist, "mahoraga"), { recursive: true });
writeFileSync(join(dist, "mahoraga", "og.png"), png); // so a preview shows it before the next build
console.log(`public/og.png: ${(png.length / 1024).toFixed(0)} KB`);
