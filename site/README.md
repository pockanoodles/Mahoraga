# kaitosoeno.com/mahoraga

Mahoraga's site. It is a Next.js static export that ships as its own Cloudflare Worker on
the route `kaitosoeno.com/mahoraga*`, so it releases with Mahoraga rather than with the
personal site, which stays the origin for every other path. It has three pages, each in
English under `/mahoraga/` and in Japanese under `/mahoraga/ja/`:

- **Research** (`/mahoraga/`, the landing page): where local stands, one task in full, the
  settings grid and the log (`components/Research.tsx`).
- **Engine** (`/mahoraga/engine/`): the pipeline, the first probe replayed, the verdict
  calculator and what stays on the machine (`components/Engine.tsx`).
- **Install** (`/mahoraga/install/`): installing it, running it, scripting it and helping
  with it (`components/Install.tsx`).

`components/Shell.tsx` gives every page the same bar, footer and menu, and `lib/routes.ts`
holds every page's path, which the links, the metadata and the sitemap all read.

## Where things come from

- **Numbers:** `../docs/site/site.json`, written by `scripts/export_site.py` from committed
  results. The page formats them and never types one in, so a page and the engine can't
  disagree. Regenerate it on the commit you release from. Server components read it
  through `lib/data.ts`. Client components get their numbers as props and import only
  `lib/format.ts`, so the export never ships to the browser.
- **The log:** `content/log.ts`, one dated entry per finding, correction or voided run,
  each reading its numbers from a claim. The same entries render the Atom feed at
  `/mahoraga/log.xml` (`app/log.xml/route.ts`).
- **The specimen:** one attempt kept whole in `../experiments/agentbench/specimen-2026-10.json`,
  written by `scripts/specimen.py` from the bench that ran it. Rerun that script for a new
  edition's first task, then the export.
- **Brand:** `../brand/` (tokens, the wheel mark, `BRAND.md`).
- **Copy:** `content/copy.ts`, both languages behind one interface, so a missing Japanese
  line fails the build.
- **Install channels:** `content/channels.ts`. Each channel's command is shown as live only
  once that channel is published.

`scripts/prepare.mjs` copies the brand and the export in before every build.

## Run

```bash
npm install
npm run dev        # http://localhost:3000/mahoraga/
npm run build      # static export, nested at dist/mahoraga/
npm run preview    # the Worker itself, at http://localhost:3222/mahoraga/
npm run typecheck
```

## Release checklist

1. On `main` at the release commit, run `python scripts/export_site.py` from the repo
   root and commit `docs/site/site.json` if it changed.
2. Publish the channels (see `packaging/`), then set `live: true` for each one in
   `content/channels.ts`. Leave a channel at `false` until its command really works.
3. If GitHub Sponsors (or Ko-fi or Liberapay) is set up, put its URL in `SPONSOR_URL`.
4. If the hero's copy or the export changed, run `npm run build && npm run og` first, so
   the social card shows what the page now does.
5. `npm run build`, then `npm run preview`, and in another shell
   `BASE=http://localhost:3222 npm run verify`, which loads all six pages on a laptop and a
   phone and checks the hero's layout at the window sizes that once broke it.
6. `npx wrangler deploy` from this directory. The route needs the `kaitosoeno.com` zone on
   the same Cloudflare account as the personal site. After the first deploy, run
   `curl -sI https://kaitosoeno.com/mahoraga/` and confirm that this Worker answers it,
   not the personal site.
