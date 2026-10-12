// A Playwright sweep of the built page: both languages, a laptop and a phone.
// It loads the page the way a visitor does and drives the parts that move, so
// a broken interaction fails here rather than on kaitosoeno.com.
//
//   npm run build && npx wrangler dev --port 3222   (or any static server on dist/)
//   BASE=http://localhost:3222 npm run verify
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3222";
const PAGES = [
  { path: "/mahoraga/", lang: "en", stay: "Stay", split: "Split", open: "Not yet decided", h1: "Is local coding AI worth it for your code?" },
  { path: "/mahoraga/ja/", lang: "ja", stay: "継続", split: "分担", open: "未確定", h1: "ローカルのコーディング AI は、あなたのコードで割に合うのか。" },
];
const VIEWPORTS = [
  { name: "laptop", width: 1440, height: 900 },
  { name: "phone", width: 390, height: 844 },
];

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
    check((await page.textContent("h1"))?.trim() === pg.h1, `${tag}: the statement is the h1`);

    // The replay lands every task and then shows where it ended.
    await page.waitForTimeout(3600);
    const landed = await page.locator(".replay__task[data-on]").count();
    check(landed === 18, `${tag}: replay landed all 18 tasks (${landed})`);
    check(await page.locator(".replay__end[data-on]").count() === 1, `${tag}: replay shows its final rate`);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(overflow <= 0, `${tag}: no sideways scroll (${overflow}px)`);

    // The verdict follows the price: stay at the cheap end, a tentative split at the dear end.
    const slider = page.locator("#vdemo-price");
    await slider.scrollIntoViewIfNeeded();
    await slider.evaluate((el) => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      set.call(el, el.max);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    check((await page.textContent(".vdemo__word"))?.trim() === pg.split, `${tag}: $30 a task reads ${pg.split}`);
    check((await page.textContent(".vdemo__status"))?.trim() === pg.open, `${tag}: and it isn't decided yet`);
    await slider.evaluate((el) => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      set.call(el, el.min);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    check((await page.textContent(".vdemo__word"))?.trim() === pg.stay, `${tag}: $0.10 a task reads ${pg.stay}`);

    // Code tabs switch, and each shows a real command.
    await page.locator('[role="tab"]', { hasText: "HTTP" }).click();
    check((await page.textContent(".codetabs__panel"))?.includes("/api/audit/verdict"), `${tag}: HTTP tab shows the verdict endpoint`);
    await page.locator('[role="tab"]', { hasText: "MCP" }).click();
    check((await page.textContent(".codetabs__panel"))?.includes("mahoraga mcp"), `${tag}: MCP tab shows mahoraga mcp`);

    // Only a published channel offers to copy its command.
    const rows = await page.locator(".install__row").count();
    const live = await page.locator(".install__row[data-live] .copy").count();
    const pending = await page.locator(".install__row:not([data-live]) .install__pending").count();
    check(rows === live + pending && live >= 1, `${tag}: install rows are live or labelled pending (${live} live, ${pending} pending)`);

    // Footnote markers point at notes that exist.
    const refs = await page.locator(".fnref").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
    const missing = [];
    for (const r of refs) if (!(await page.locator(r).count())) missing.push(r);
    check(refs.length > 0 && missing.length === 0, `${tag}: every footnote marker has its note`);

    // The site's own menu opens, and its links leave for the site (no /mahoraga prefix).
    await page.locator(".site-menu__button").click();
    const hrefs = await page.locator(".site-menu__link").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
    check(hrefs.length === 4 && hrefs.every((h) => !h.startsWith("/mahoraga")), `${tag}: site menu links go to the site (${hrefs.join(" ")})`);
    await page.keyboard.press("Escape");

    // The bar frosts once the page is under it.
    await page.evaluate(() => window.scrollTo(0, window.innerHeight * 1.5));
    await page.waitForTimeout(300);
    check((await page.getAttribute(".lnav", "data-clear")) === null, `${tag}: the bar frosts past the hero`);

    const shot = await page.locator(".shot__img").count();
    if (shot) {
      await page.locator(".shot__img").scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      const w = await page.locator(".shot__img").evaluate((img) => img.naturalWidth);
      check(w > 0, `${tag}: the app screenshot loads`);
    }

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
