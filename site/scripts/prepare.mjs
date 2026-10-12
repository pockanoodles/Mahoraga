// Brings the brand and the engine's export into the site before each build.
// Both live in this repository (brand/, docs/site/site.json), so the page can
// never show a number or a colour the product doesn't have. The copies are
// gitignored.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const site = join(dirname(fileURLToPath(import.meta.url)), "..");
const repo = join(site, "..");

mkdirSync(join(site, "generated"), { recursive: true });
mkdirSync(join(site, "public", "brand"), { recursive: true });
copyFileSync(join(repo, "brand", "tokens.css"), join(site, "generated", "tokens.css"));
// SITE_JSON points at another export while developing (e.g. an unmerged branch).
copyFileSync(process.env.SITE_JSON ?? join(repo, "docs", "site", "site.json"), join(site, "generated", "site.json"));
copyFileSync(join(repo, "brand", "favicon.svg"), join(site, "public", "favicon.svg"));
for (const f of ["mark-dark.svg", "mark-light.svg"]) {
  copyFileSync(join(repo, "brand", f), join(site, "public", "brand", f));
}
