# ADR 2026-10-29 — The site is three pages: Research, Engine and Install

## Status
Accepted (2026-10-29).

**Builds on:** `2026-10-29-landing-page-is-the-log.md`. Its page is now the Research page.

## Context

Kaito, looking at the built page on 2026-10-29: the hero overlapped itself at his window,
and "one single page is too long". He asked for the bar to read Research, Engine and
Install, for the landing page to be the research page, for the engine page to explain the
product and the install page to carry the install steps, and for the site to stay concise.

The hero was a grid fixed at the screen's height (`height: 100svh`) whose chart row could
shrink to nothing (`minmax(0, 1fr)`). At every laptop size measured (1000×558 to 1512×860)
and on phones, the chart spilled under the bar and over the answer. The browser sweep had
no layout check, so it passed.

## Decision

1. **Three pages in each language**, at `/mahoraga/`, `/mahoraga/engine/` and
   `/mahoraga/install/`, and the same under `/mahoraga/ja/`. Research holds the hero, one
   task in full, the settings grid, the log and the footnotes. Engine holds the pipeline,
   the September replay (it shows how a verdict settles, which is the engine's behaviour),
   the calculator and privacy. Install holds the run steps, the CLI, HTTP and MCP tabs, and
   the support links.
2. **`lib/routes.ts` is the only place a page's path is written.** The bar, the footer, the
   hero's second action, each page's metadata (title, description, canonical, hreflang
   pair, og) and the sitemap all read it.
3. **The hero puts the answer and its actions beside the chart on wide screens**, as the
   social card does, and stacks them on tall, narrow ones. It is at least one screen high
   and grows when its content needs more, so nothing in it can overlap. The chart picks its
   compact layout from its own width (a container query), not the screen's.
4. **`scripts/verify.mjs` loads all six pages** on a laptop and a phone, and checks the
   hero's layout at the window sizes that broke it.

## Alternatives considered

- **Keep one page and shorten it.** The log, the exhibits, the calculator and the install
  steps serve different readers, and splitting them keeps each page to one subject.
- **Keep the chart above the answer and only let the hero grow.** That removes the
  overlap, but on most laptop screens it pushes the answer below the fold.

## Consequences

- A new section goes on the page whose subject it shares, and a link to it from another
  page goes through `pagePath`.
- The Atom feed and its link stay on Research, where the log is.
