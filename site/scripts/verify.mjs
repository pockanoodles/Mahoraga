// A Playwright sweep of the built site: its pages in both languages, on
// a laptop and a phone, then the hero's layout at the window sizes that broke
// it. It loads each page the way a visitor does and drives the parts that
// move, so a broken interaction fails here rather than on kaitosoeno.com.
//
//   npm run build && npx wrangler dev --port 3222   (or any static server on dist/)
//   BASE=http://localhost:3222 npm run verify
import { readFileSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:3222";
// The data the page was built from (prepare-assets copies it), for the parts the
// export can leave out: a run's one-task card waits until its rate can sit beside it.
const DATA = JSON.parse(readFileSync(new URL("../generated/site.json", import.meta.url), "utf8"));
const SITE = "https://kaitosoeno.com";
const PAGES = ["research", "engine", "install"]; // the bar's three
const ALL_PAGES = [...PAGES, "privacy"];
const LOCALES = [
  {
    lang: "en",
    root: "/mahoraga/",
    nav: ["Research", "Engine", "Install"],
    unaffiliated: "Mahoraga is not affiliated with Anthropic, OpenAI, Alibaba, IBM or Ollama.",
    stay: "Stay",
    open: "Not yet decided",
    answer: "For most people, the answer is still to keep paying for cloud.",
    h1: {
      research: "Should you keep paying for cloud coding AI, or run a model on your own machine?",
      engine: "One pipeline, from a repository's history to a verdict.",
      install: "Run it on your own repository.",
      privacy: "Privacy",
    },
  },
  {
    lang: "ja",
    root: "/mahoraga/ja/",
    nav: ["研究", "エンジン", "インストール"],
    unaffiliated: "Mahoraga は Anthropic、OpenAI、Alibaba、IBM、Ollama のいずれとも提携していません。",
    stay: "継続",
    open: "未確定",
    answer: "多くの人にとって、答えは今もクラウドに払い続けることです。",
    h1: {
      research: "クラウドのコーディング AI に払い続けるべきか、それとも自分のマシンでモデルを動かすべきか。",
      engine: "リポジトリの履歴から判定まで、ひとつのパイプラインで。",
      install: "あなたのリポジトリで動かす。",
      privacy: "プライバシーポリシー",
    },
  },
];
const pathOf = (loc, page) => (page === "research" ? loc.root : `${loc.root}${page}/`);
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

// What every page shares: its address and language, its title, the bar's three
// pages with this one marked, the language switch to the same page, the
// metadata pairs, no sideways scroll, and the site's own menu.
async function shared(page, loc, name, tag) {
  check((await page.getAttribute("html", "lang")) === loc.lang, `${tag}: <html lang="${loc.lang}">`);
  check((await page.textContent("h1"))?.trim() === loc.h1[name], `${tag}: the page's title is its h1`);
  check((await page.locator("h1").count()) === 1, `${tag}: one h1`);
  check((await page.locator('head link[rel="alternate"][type="application/atom+xml"]').count()) === 1, `${tag}: the head names the log's feed`);
  const canonical = await page.getAttribute('head link[rel="canonical"]', "href");
  check(canonical === SITE + pathOf(loc, name), `${tag}: canonical is the page itself (${canonical})`);
  const alt = async (hl) => page.getAttribute(`head link[rel="alternate"][hreflang="${hl}"]`, "href");
  const [en, ja] = [await alt("en"), await alt("ja")];
  check(en === SITE + pathOf(LOCALES[0], name) && ja === SITE + pathOf(LOCALES[1], name), `${tag}: hreflang pairs this page in both languages`);

  const links = await page.locator(".lnav__links a").evaluateAll((as) =>
    as.map((a) => ({ text: a.textContent.trim(), href: a.getAttribute("href"), current: a.getAttribute("aria-current") })),
  );
  check(links.map((l) => l.text).join("|") === loc.nav.join("|"), `${tag}: the bar names the three pages (${links.map((l) => l.text).join(", ")})`);
  check(links.every((l, i) => l.href === pathOf(loc, PAGES[i])), `${tag}: each of the bar's pages links to its page`);
  const marked = PAGES.includes(name) ? pathOf(loc, name) : "";
  check(links.filter((l) => l.current === "page").map((l) => l.href).join() === marked, `${tag}: the bar marks this page and only this one`);
  const other = LOCALES.find((l) => l !== loc);
  check((await page.getAttribute(".lnav__lang", "href")) === pathOf(other, name), `${tag}: the language switch keeps the page`);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(overflow <= 0, `${tag}: no sideways scroll (${overflow}px)`);
  check((await page.textContent(".foot"))?.includes(loc.unaffiliated), `${tag}: the footer says whom Mahoraga is not affiliated with`);

  // The site's own menu opens, and its links leave for the site (no /mahoraga prefix).
  await page.locator(".site-menu__button").click();
  const hrefs = await page.locator(".site-menu__link").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  check(hrefs.length === 2 && hrefs.every((h) => !h.startsWith("/mahoraga")), `${tag}: site menu links go to the site (${hrefs.join(" ")})`);
  await page.keyboard.press("Escape");
}

async function research(page, loc, vp, tag) {
  // The question is the h1, in its own panel, and the dated answer follows it.
  check((await page.locator(".hero__question h1").count()) === 1, `${tag}: the question is the h1, in its own panel`);
  check((await page.textContent(".hero__answer .hero__statement"))?.trim() === loc.answer, `${tag}: the answer follows the question`);

  // The hero chart: measured rows draw an interval and a rate, and the run still
  // under way shows no rate at all, since a number read mid-run would be peeking.
  const measured = page.locator(".standing__row:not(.standing__row--running)");
  const rows = await measured.count();
  const bands = await measured.locator(".standing__band").evaluateAll((els) => els.filter((e) => e.getBoundingClientRect().width > 0).length);
  check(rows >= 1 && bands === rows, `${tag}: every measured row draws its interval (${bands}/${rows})`);
  check((await page.locator(".standing__row--running .standing__pct").count()) === 0, `${tag}: the running row shows no rate`);
  const running = await page.locator(".standing__running").getAttribute("href");
  check(Boolean(running) && (await page.locator(running).count()) === 1, `${tag}: the running row links to its log entry (${running})`);
  const heroPct = (await measured.locator(".standing__pct").allTextContents()).map((s) => s.trim());
  check((await page.getAttribute(".hero__more", "href")) === pathOf(loc, "engine"), `${tag}: the hero's second action goes to the engine page`);

  // One task in full when the export carries one: the commit upstream, the fix
  // and the agent's patch, and what the tests said. Without one, no section.
  if (DATA.specimen) {
    check((await page.locator("#task .specimen__diff").count()) === 2, `${tag}: the specimen shows the fix and the agent's patch`);
    const upstream = (await page.getAttribute("#task .specimen__where a", "href")) ?? "";
    check(/^https:\/\/github\.com\/[^/]+\/[^/]+\/commit\/[0-9a-f]{7,40}$/.test(upstream), `${tag}: the specimen links its upstream commit (${upstream})`);
  } else {
    check((await page.locator("#task").count()) === 0, `${tag}: the export carries no specimen, so there is no #task section`);
  }

  // The settings grid opens on its finding, the second setting, already in
  // the server's HTML, and never changes the setting itself. Each setting's
  // count is the squares it lights and the share the hero prints for the same
  // setting, only a setting with one before it rings changes, and the squares
  // stay where they are whichever setting the reader picks.
  const served = await (await page.request.get(page.url())).text();
  const picked = [...served.matchAll(/role="radio" aria-checked="(true|false)"/g)].map((m) => m[1]);
  check(picked.join(",") === "false,true,false", `${tag}: the server's HTML opens the grid on the second setting (${picked.join(",")})`);
  const grid = page.locator("#settings .sgrid");
  await grid.evaluate((el) => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - window.innerHeight * 0.2));
  await page.waitForTimeout(1200);
  const settings = grid.locator('[role="radio"]');
  check((await settings.nth(1).getAttribute("aria-checked")) === "true", `${tag}: the grid is still on the second setting once seen`);
  const counts = [];
  const tops = [];
  for (let i = 0; i < (await settings.count()); i++) {
    await settings.nth(i).click();
    const k = Number((await grid.locator(".sgrid__k .sr-only").textContent())?.trim());
    const lit = await grid.locator(".sgrid__sq[data-solved]").count();
    const n = await grid.locator(".sgrid__sq").count();
    const rings = await grid.locator(".sgrid__sq[data-change]").count();
    counts.push(k);
    tops.push(Math.round((await grid.locator(".sgrid__plot").boundingBox()).y));
    check(k === lit, `${tag}: setting ${i + 1} counts the squares it lights (${k}/${lit})`);
    check(`${Math.round((100 * k) / n)}%` === heroPct[i], `${tag}: setting ${i + 1} is the hero's ${heroPct[i]} (${k} of ${n})`);
    check(i === 0 ? rings === 0 : rings > 0, `${tag}: setting ${i + 1} rings ${rings} changed commits`);
  }
  check(counts.length === 3 && counts[2] !== counts[1], `${tag}: choosing 35B changes the count (${counts.join(" → ")})`);
  check(new Set(tops).size === 1, `${tag}: the squares stay put across settings (${tops.join(", ")})`);
  await settings.nth(2).focus();
  await page.keyboard.press("ArrowLeft");
  check((await settings.nth(1).getAttribute("aria-checked")) === "true", `${tag}: the arrow keys move the setting`);
  await grid.locator(".sgrid__plot").focus();
  await page.keyboard.press("ArrowRight");
  check((await grid.locator(".sgrid__tip-head").count()) === 1, `${tag}: the arrow keys step through the commits`);
  await page.keyboard.press("Escape");
  if (vp.width > 900) {
    await grid.locator(".sgrid__sq").nth(3).hover();
    check((await grid.locator(".sgrid__tip-head").count()) === 1, `${tag}: pointing at a square shows its commit`);
  }

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
  check((await page.locator('#log a[href="/mahoraga/log.xml"]').count()) === 1, `${tag}: the log links its feed`);
  if (vp.width > 900) {
    const updated = await page.getAttribute(".lnav__meta", "datetime");
    check(updated === dates[0], `${tag}: the bar's update date is the newest entry's (${updated})`);
  }

  // Footnote markers point at notes that exist.
  const refs = await page.locator(".fnref").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  const missing = [];
  for (const r of refs) if (!(await page.locator(r).count())) missing.push(r);
  check(refs.length > 0 && missing.length === 0, `${tag}: every footnote marker has its note`);

  // The bar is clear over the hero and frosts once the page is under it.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(300);
  check((await page.getAttribute(".lnav", "data-clear")) !== null, `${tag}: the bar is clear over the hero`);
  await page.evaluate(() => window.scrollTo(0, document.querySelector("[data-hero]").getBoundingClientRect().bottom + window.scrollY));
  await page.waitForTimeout(300);
  check((await page.getAttribute(".lnav", "data-clear")) === null, `${tag}: the bar frosts past the hero`);
  return entries.length;
}

async function engine(page, loc, tag) {
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
  check(seen[0].word === loc.stay, `${tag}: the cheapest cloud task reads ${loc.stay}`);
  check(seen[seen.length - 1].word !== loc.stay, `${tag}: the dearest reads something else (${seen[seen.length - 1].word})`);
  check(seen.some((s) => s.status === loc.open), `${tag}: some price is not yet decided`);

  // The second control sets how long a failed attempt takes to check, and the costs line follows it.
  const triage = page.locator('[role="radiogroup"][aria-labelledby="vdemo-triage"] [role="radio"]');
  const minutes = await triage.count();
  check(minutes >= 2 && (await triage.and(page.locator('[aria-checked="true"]')).count()) === 1, `${tag}: one of ${minutes} triage times is chosen`);
  await triage.first().click();
  const cheapest = (await page.textContent(".vdemo__costs"))?.trim();
  await triage.last().click();
  check(cheapest !== (await page.textContent(".vdemo__costs"))?.trim(), `${tag}: the triage time changes what a failed attempt costs`);
}

async function install(page, tag) {
  // Every command the run section shows can be copied, and tonight's run is one of them.
  const cmds = await page.locator(".run__cmd").count();
  const copies = await page.locator(".run__cmd .copy").count();
  check(cmds > 0 && copies === cmds, `${tag}: every run command has a copy button (${copies}/${cmds})`);
  check((await page.locator(".run__cmd code", { hasText: "mahoraga audit tonight" }).count()) === 1, `${tag}: the run section queues tonight's run`);

  // Code tabs switch, and each shows a real command.
  await page.locator('[role="tab"]', { hasText: "HTTP" }).click();
  check((await page.textContent(".codetabs__panel"))?.includes("/api/audit/verdict"), `${tag}: HTTP tab shows the verdict endpoint`);
  await page.locator('[role="tab"]', { hasText: "MCP" }).click();
  check((await page.textContent(".codetabs__panel"))?.includes("mahoraga mcp"), `${tag}: MCP tab shows mahoraga mcp`);
}

// The privacy policy: the footer reaches it from every page, and its
// where / what / why rows read as a table on a laptop and stack on a phone.
async function privacy(page, loc, tag) {
  check((await page.getAttribute(`.foot a[href="${pathOf(loc, "privacy")}"]`, "aria-current")) === "page", `${tag}: the footer marks the privacy link`);
  const rows = await page.locator(".ledger__row:not(.ledger__head)").count();
  check(rows >= 4, `${tag}: the policy lists what can leave the machine (${rows} rows)`);
  check((await page.locator(".policy__section").count()) === 5, `${tag}: five sections`);
}

const browser = await chromium.launch();
let logCount = 0;
for (const loc of LOCALES) {
  for (const vp of VIEWPORTS) {
    for (const name of ALL_PAGES) {
      const tag = `${loc.lang}/${name}/${vp.name}`;
      const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
      const errors = [];
      page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
      page.on("pageerror", (e) => errors.push(String(e)));
      const res = await page.goto(BASE + pathOf(loc, name), { waitUntil: "networkidle" });
      check(res?.status() === 200, `${tag}: page answers 200`);
      // A page without the hero opens under a bar that is already frosted.
      if (name !== "research") check((await page.getAttribute(".lnav", "data-clear")) === null, `${tag}: the bar starts frosted`);
      await shared(page, loc, name, tag);
      if (name === "research") logCount = await research(page, loc, vp, tag);
      if (name === "engine") await engine(page, loc, tag);
      if (name === "install") await install(page, tag);
      if (name === "privacy") await privacy(page, loc, tag);
      check(errors.length === 0, `${tag}: no console errors${errors.length ? ` (${errors.join(" | ")})` : ""}`);
      await page.close();
    }
  }
}

// The hero at the window sizes that once broke it: the chart below the bar,
// nothing in it overlapping anything else, and the answer on the first screen.
const box = (page, sel) => page.locator(sel).first().evaluate((e) => {
  const b = e.getBoundingClientRect();
  return { top: b.top, bottom: b.bottom, left: b.left, right: b.right };
});
const meets = (a, b) => a.top < b.bottom - 1 && b.top < a.bottom - 1 && a.left < b.right - 1 && b.left < a.right - 1;
for (const [loc, width, height] of [[LOCALES[0], 1000, 558], [LOCALES[0], 1280, 800], [LOCALES[0], 1440, 780], [LOCALES[0], 390, 844], [LOCALES[0], 375, 600], [LOCALES[1], 1000, 558], [LOCALES[1], 375, 600]]) {
  const tag = `layout ${loc.lang} ${width}×${height}`;
  const page = await browser.newPage({ viewport: { width, height } });
  await page.goto(BASE + loc.root, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200); // the hero's rise animation
  const [nav, chart, text, cta, ask, answer] = await Promise.all([".lnav", ".standing", ".hero__text", ".hero__cta", ".hero__question", ".hero__statement"].map((s) => box(page, s)));
  check(chart.top >= nav.bottom - 1, `${tag}: the chart starts below the bar`);
  check(text.top >= nav.bottom - 1 && cta.top >= nav.bottom - 1, `${tag}: the answer and its actions start below the bar`);
  check(!meets(chart, text) && !meets(chart, cta) && !meets(text, cta), `${tag}: the chart, the answer and its actions don't overlap`);
  check(ask.bottom <= answer.top, `${tag}: the answer sits under the question`);
  check(answer.bottom <= height, `${tag}: the question and its answer are on the first screen`);
  const clipped = await page.locator(".hero .standing span, .hero .standing p").evaluateAll((els) =>
    els.filter((e) => { const b = e.getBoundingClientRect(); return b.width > 0 && (b.left < -1 || b.right > window.innerWidth + 1); }).length,
  );
  check(clipped === 0, `${tag}: no label of the chart runs off the screen (${clipped})`);
  await page.close();
}

// On a touch screen the first tap on a square shows its commit, a second tap
// on the same square opens it, and so does a tap on the tip's own link.
{
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/mahoraga/`, { waitUntil: "networkidle" });
  const sq = page.locator("#settings .sgrid__sq").first();
  // A reader looks, then taps: the scroll lands at once, and the taps wait for
  // hydration (the picker's thumb is placed by its script), since a tap before
  // it is a plain link.
  await sq.evaluate((el) => el.scrollIntoView({ block: "center", behavior: "instant" }));
  await page.locator("#settings .seg[data-thumb]").waitFor();
  let opened = 0;
  ctx.on("page", () => opened++);
  // A browser's first new tab took 2 s to appear on 10-29, and later ones about
  // 0.2 s, so each check waits for the tab and not for a fixed time.
  const tab = () => ctx.waitForEvent("page", { timeout: 10_000 }).catch(() => null);
  await sq.tap();
  await page.waitForTimeout(500);
  check(opened === 0 && (await page.locator(".sgrid__tip-head").count()) === 1, "touch: the first tap shows the commit and opens nothing");
  const second = tab();
  await sq.tap();
  check(Boolean(await second), "touch: a second tap opens the commit");
  await page.waitForTimeout(500);
  check(opened === 1, `touch: the two taps open one tab between them (${opened})`);
  await page.locator("#settings .sgrid__sq").nth(1).tap();
  const link = page.locator(".sgrid__tip-open");
  await link.waitFor();
  const viaTip = tab();
  await link.tap();
  check(Boolean(await viaTip), "touch: the tip's own link opens the commit");
  await ctx.close();
}

const feed = await (await browser.newPage()).goto(`${BASE}/mahoraga/log.xml`);
const xml = (await feed?.text()) ?? "";
check(feed?.status() === 200 && xml.includes('<feed xmlns="http://www.w3.org/2005/Atom"'), "log.xml is served as an Atom feed");
check((xml.match(/<entry>/g) ?? []).length === logCount, `log.xml carries every log entry (${(xml.match(/<entry>/g) ?? []).length}/${logCount})`);
const map = await (await browser.newPage()).goto(`${BASE}/mahoraga/sitemap.xml`);
const urls = ((await map?.text()) ?? "").match(/<loc>[^<]+<\/loc>/g) ?? [];
const expected = LOCALES.flatMap((loc) => ALL_PAGES.map((p) => `<loc>${SITE}${pathOf(loc, p)}</loc>`));
check(expected.every((u) => urls.includes(u)) && urls.length === expected.length, `sitemap lists the ${expected.length} pages (${urls.length})`);
const og = await (await browser.newPage()).goto(`${BASE}/mahoraga/og.png`);
check(og?.status() === 200, "og.png is served");
await browser.close();

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed`);
  process.exit(1);
}
console.log("\nall checks passed");
