# ADR 2026-10-12 — Mahoraga ships as a product: a headless engine, an app on top, a landing page of its own

## Status
Accepted (2026-10-12).

**Builds on:** `2026-10-02-local-audit-product.md`. That decision fixed what the product
measures. This one fixes how it reaches people.

## Context

Kaito is ramping up applications, so his public profile, Mahoraga included, has to look
finished. He asked for three things: a landing page like an open-source project's, on his
personal site rather than its own domain; an app UI, with the engine itself headless
because this is a technical project; and the many install channels FOSS projects like
sleek offer, which show it can launch and leave room for small sponsorship later. The look
was to be Apple-like, with the spacing he pointed at in PaleSquid's pages (recorded as
ui-master Design 03).

## Decision

1. **The engine is headless, and everything is a client of its HTTP API.** `/api/engine`
   and `/api/audit/*` (benches, runs, arms, pricing, verdict) are published as response
   models in `/openapi.json`. The CLI is `mahoraga` (with `audit` as the product name for
   `agentbench`, and `orch` kept as an alias). `mahoraga mcp` runs the MCP bridge.
   `mahoraga serve --headless` mounts no UI.
2. **The app is optional and ships inside the wheel.** `frontend/` was rebuilt around the
   audit (verdict, repositories, runs, arms, then the router), reads only the API, and
   bundles its fonts so it makes no request off the machine. A history-API fallback (by
   `Accept: text/html`) lets app routes like `/runs` coexist with the old JSON endpoints.
3. **The landing page lives in this repo, not the personal site.** `site/` is a Next static
   export with basePath `/mahoraga`, deployed as its own Worker on the route
   `kaitosoeno.com/mahoraga*`. Then it releases with the product, its numbers come from
   `docs/site/site.json` (written by `scripts/export_site.py` from committed data) with no
   cross-repo copying, and the personal site only links to it. The Erwin session adopted
   the same shape for `kaitosoeno.com/erwin`.
4. **One brand for both.** `brand/` holds the tokens, the wheel mark and `BRAND.md`. It uses
   Design 03 spacing on the site's black, with one amber accent that means "local".
5. **Distribution:** PyPI as **`mahoraga-audit`** (the name `mahoraga` belongs to an unrelated
   project), a formula in `pockanoodles/homebrew-tap`, an image on GHCR, and install from
   git. A tag publishes all of them through one release workflow after a smoke test of the
   built wheel. Each channel shows as live on the page only once it is published.
6. **`mahoraga demo` is fully isolated.** It serves the committed 18-commit probe from a
   scratch HOME with the cascade off, so a demo shows none of the user's own state and
   reaches no cloud.

## Consequences

- Every number on the landing page is reproducible from the repo, and CI builds the page
  and drives it in a browser in both languages.
- `DATAFLOW.md` now exists. It records that the router escalates judge-rejected tasks to
  the Claude CLI by default (up to 25 a day). The old privacy line ("nothing leaves unless
  you turn on a cloud arm") was wrong about that.
- Fresh-machine bugs surfaced and were fixed: the store couldn't create `~/.mahoraga-v2`,
  and an installed package had no roster (it now ships `default_agents.yaml`).
- **Known debt, not done here:** the top-level package is still called `backend` in
  site-packages (renaming it to `mahoraga` is a large refactor); about 30 modules hard-code
  `~/.mahoraga-v2` (a `MAHORAGA_HOME` override would replace the demo's HOME trick); a few
  legacy paths ignore `OLLAMA_BASE_URL`; the web-search and URL tools are unwired dead
  code; `evidence` and `summarise` compute the median attempt time differently (8.3 vs 8.4
  minutes on the probe).
- Publishing (PyPI trusted publisher, the tap repo, GHCR visibility, the first tag) and
  deploying the site are Kaito's steps, listed in `packaging/README.md` and `site/README.md`.
