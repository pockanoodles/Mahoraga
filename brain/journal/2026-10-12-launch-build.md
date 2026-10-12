# 2026-10-12 — Mahoraga gets a face: the app, the landing page, and every way to install it

A build session, run overnight while Kaito slept, with "you have the reins" as the brief.
He wanted Mahoraga presentable for applications: a landing page on kaitosoeno.com like an
open-source project's, an app on a headless engine, many install channels, and an
Apple-like look with PaleSquid's spacing (now ui-master Design 03). The shape is recorded
in `brain/decisions/2026-10-12-launch-shape.md`.

## What got built

- **Brand** (`brand/`): an eight-spoke wheel with one lit spoke, an oklch-derived palette
  whose single amber accent means "local", Geist and Geist Mono.
- **Engine API** for the audit, history fallback, `mahoraga`/`audit`/`mcp` commands and
  headless serving, by a delegated agent (1,975 tests at hand-back).
- **Demo and site export**: the committed probe as a packaged bench, and `site.json`
  computed by the engine itself (the hero replays its Wilson interval step by step).
- **Packaging**: version 2.1.0 from one file, a wheel with the app inside, Docker,
  release workflow, Homebrew formula, CONTRIBUTING, SECURITY.
- **The app**, rebuilt: verdict first, recomputed live from the API.
- **The landing page** (`site/`), English and Japanese, with a Playwright sweep in CI.
- The portfolio case study on personal-website was rewritten (branch
  `content/mahoraga-audit`). It had still said the bandit "sharpens with traffic", which
  RESULTS.md refutes.

## What the work found

- `mahoraga` is taken on PyPI by an active, unrelated project, so the distribution is
  `mahoraga-audit`.
- A fresh machine couldn't start the server (no `~/.mahoraga-v2`), and a wheel had no
  roster. Both are fixed.
- The router escalates to the Claude CLI by default. The privacy copy said otherwise, and
  `DATAFLOW.md` now says it plainly.
- `mahoraga demo` was showing the user's own router log under a banner saying none of it
  was theirs. It now runs in a scratch HOME.
- Kaito, mid-session, on the old site copy: "way too wordy, but there's good info." Every
  web string was cut to one short sentence, and the rule went into the voice file.

## Not done

Nothing is pushed, published or deployed. The order is: PRs, then the tag and channels,
then flip `live` in `site/content/channels.ts`, then deploy the site.
