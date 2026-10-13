# kaitosoeno.com/mahoraga

Mahoraga's landing page. It is a Next.js static export that ships as its own Cloudflare
Worker on the route `kaitosoeno.com/mahoraga*`, so it releases with Mahoraga rather than
with the personal site, which stays the origin for every other path. English is at
`/mahoraga/` and Japanese at `/mahoraga/ja/`.

## Where things come from

- **Numbers:** `../docs/site/site.json`, written by `scripts/export_site.py` from committed
  results. The page formats them and never types one in, so a page and the engine can't
  disagree. Regenerate it on the commit you release from.
- **Brand:** `../brand/` (tokens, the wheel mark, `BRAND.md`).
- **Copy:** `content/copy.ts`, both languages behind one interface, so a missing Japanese
  line fails the build.
- **Install channels:** `content/channels.ts`. Each channel's command is shown as live only
  once that channel is published.
- **App screenshots:** `public/app/` (`verdict-dark.png`, `verdict-dark-narrow.png`), taken
  from `mahoraga demo`. Until they exist the slot renders as a labelled box.

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
4. `npm run build`, check `/mahoraga/` and `/mahoraga/ja/` with `npm run preview`.
5. `npx wrangler deploy` from this directory. The route needs the `kaitosoeno.com` zone on
   the same Cloudflare account as the personal site. After the first deploy, run
   `curl -sI https://kaitosoeno.com/mahoraga/` and confirm that this Worker answers it,
   not the personal site.
