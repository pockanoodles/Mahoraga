// A Playwright sweep of the built page: both languages, a laptop and a phone.
// It loads the page the way a visitor does and drives the parts that move, so
// a broken interaction fails here rather than on kaitosoeno.com.
//
//   npm run build && npx wrangler dev --port 3222   (or any static server on dist/)
//   BASE=http://localhost:3222 npm run verify
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3222";
const PAGES = [
  { path: "/mahoraga/", lang: "en", stay: "Stay", open: "Not yet decided", h1: "For most people, the answer is still to keep paying for cloud." },
  { path: "/mahoraga/ja/", lang: "ja", stay: "継続", open: "未確定", h1: "多くの人にとって、答えは今もクラウドに払い続けることです。" },
];
const VIEWPORTS = [
  { name: "laptop", width: 1440, height: 900 },
  { name: "phone", width: 390, height: 844 },
];
const REPO = "https://github.com/pockanoodles/Mahoraga/";

const failures = [];
const check = (ok, what) => {
  if (!ok) failures.push(what);
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
};

const browser = await chromium.launch();
for (const pg of PAGES) {
  for (const vp of VIEWPORTS) {
    const tag = `${pg.lang}/${vp.name}`;
    const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
    const errors = [];
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(String(e)));
    const res = await page.goto(BASE + pg.path, { waitUntil: "networkidle" });
    check(res?.status() === 200, `${tag}: page answers 200`);
    check((await page.getAttribute("html", "lang")) === pg.lang, `${tag}: <html lang="${pg.lang}">`);
    check((await page.textContent("h1"))?.trim() === pg.h1, `${tag}: the answer is the h1`);

    // The hero chart: measured rows draw an interval and a rate, and the run still
    // under way shows no rate at all, since a number read mid-run would be peeking.
    const measured = page.locator(".standing__row:not(.standing__row--running)");
    const rows = await measured.count();
    const bands = await measured.locator(".standing__band").evaluateAll((els) => els.filter((e) => e.getBoundingClientRect().width > 0).length);
    check(rows >= 1 && bands === rows, `${tag}: every measured row draws its interval (${bands}/${rows})`);
    check((await page.locator(".standing__row--running .standing__pct").count()) === 0, `${tag}: the running row shows no rate`);
    const running = await page.locator(".standing__running").getAttribute("href");
    check(Boolean(running) && (await page.locator(running).count()) === 1, `${tag}: the running row links to its log entry (${running})`);

    // The log: newest first, every entry dated and sourced, and the bar's
    // "updated" date is the newest entry's.
    const entries = await page.locator(".entry").evaluateAll((els) =>
      els.map((e) => ({
        date: e.querySelector("time")?.getAttribute("datetime") ?? "",
        sources: [...e.querySelectorAll(".entry__sources a")].map((a) => a.getAttribute("href") ?? ""),
      })),
    );
    const dates = entries.map((e) => e.date);
    check(entries.length >= 10 && dates.every((d) => /^\d{4}-\d{2}-\d{2}$/.test(d)), `${tag}: ${entries.length} log entries, each dated`);
    check(dates.every((d, i) => i === 0 || d <= dates[i - 1]), `${tag}: the log runs newest first`);
    check(entries.every((e) => e.sources.length > 0 && e.sources.every((h) => h.startsWith(REPO))), `${tag}: every entry links its sources in the repository`);
    if (vp.width > 900) {
      const updated = await page.getAttribute(".lnav__meta", "datetime");
      check(updated === dates[0], `${tag}: the bar's update date is the newest entry's (${updated})`);
    }

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(overflow <= 0, `${tag}: no sideways scroll (${overflow}px)`);

    // The replay waits until it is seen, then lands every task and shows where it ended.
    check((await page.locator(".replay__task[data-on]").count()) === 0, `${tag}: replay hasn't run below the fold`);
    await page.locator(".exhibit").scrollIntoViewIfNeeded();
    await page.waitForTimeout(3600);
    const landed = await page.locator(".replay__task[data-on]").count();
    const tasks = await page.locator(".replay__task").count();
    check(tasks > 0 && landed === tasks, `${tag}: replay landed all ${tasks} tasks (${landed})`);
    check((await page.locator(".replay__end[data-on]").count()) === 1, `${tag}: replay shows its final rate`);

    // The verdict follows the price: stay at the cheap end, something else by the
    // dear end, and somewhere between them a call the evidence hasn't settled.
    const slider = page.locator("#vdemo-price");
    await slider.scrollIntoViewIfNeeded();
    const seen = [];
    for (const f of [0, 0.25, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]) {
      await slider.evaluate((el, frac) => {
        const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
        set.call(el, String(Number(el.min) + frac * (Number(el.max) - Number(el.min))));
        el.dispatchEvent(new Event("input", { bubbles: true }));
      }, f);
      seen.push({ word: (await page.textContent(".vdemo__word"))?.trim(), status: (await page.textContent(".vdemo__status"))?.trim() });
    }
    check(seen[0].word === pg.stay, `${tag}: the cheapest cloud task reads ${pg.stay}`);
    check(seen[seen.length - 1].word !== pg.stay, `${tag}: the dearest reads something else (${seen[seen.length - 1].word})`);
    check(seen.some((s) => s.status === pg.open), `${tag}: some price is not yet decided`);

    // Code tabs switch, and each shows a real command.
    await page.locator('[role="tab"]', { hasText: "HTTP" }).click();
    check((await page.textContent(".codetabs__panel"))?.includes("/api/audit/verdict"), `${tag}: HTTP tab shows the verdict endpoint`);
    await page.locator('[role="tab"]', { hasText: "MCP" }).click();
    check((await page.textContent(".codetabs__panel"))?.includes("mahoraga mcp"), `${tag}: MCP tab shows mahoraga mcp`);

    // Every command the run section shows can be copied, and tonight's run is one of them.
    const cmds = await page.locator(".run__cmd").count();
    const copies = await page.locator(".run__cmd .copy").count();
    check(cmds > 0 && copies === cmds, `${tag}: every run command has a copy button (${copies}/${cmds})`);
    check((await page.locator(".run__cmd code", { hasText: "mahoraga audit tonight" }).count()) === 1, `${tag}: the run section queues tonight's run`);

    // Footnote markers point at notes that exist.
    const refs = await page.locator(".fnref").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
    const missing = [];
    for (const r of refs) if (!(await page.locator(r).count())) missing.push(r);
    check(refs.length > 0 && missing.length === 0, `${tag}: every footnote marker has its note`);

    // The site's own menu opens, and its links leave for the site (no /mahoraga prefix).
    await page.locator(".site-menu__button").click();
    const hrefs = await page.locator(".site-menu__link").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
    check(hrefs.length === 2 && hrefs.every((h) => !h.startsWith("/mahoraga")), `${tag}: site menu links go to the site (${hrefs.join(" ")})`);
    await page.keyboard.press("Escape");

    // The bar frosts once the page is under it.
    await page.evaluate(() => window.scrollTo(0, window.innerHeight * 1.5));
    await page.waitForTimeout(300);
    check((await page.getAttribute(".lnav", "data-clear")) === null, `${tag}: the bar frosts past the hero`);

    check(errors.length === 0, `${tag}: no console errors${errors.length ? ` (${errors.join(" | ")})` : ""}`);
    await page.close();
  }
}
const og = await (await browser.newPage()).goto(`${BASE}/mahoraga/og.png`);
check(og?.status() === 200, "og.png is served");
await browser.close();

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed`);
  process.exit(1);
}
console.log("\nall checks passed");
