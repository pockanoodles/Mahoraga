// Copies the brand marks into public/ so Vite serves them at stable URLs
// (/brand/*.svg, /favicon.svg). The source of truth is ../brand; the copies are
// gitignored and rewritten on every dev start and build.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const brand = join(here, "..", "..", "brand");
const pub = join(here, "..", "public");

mkdirSync(join(pub, "brand"), { recursive: true });
copyFileSync(join(brand, "favicon.svg"), join(pub, "favicon.svg"));
for (const f of ["mark-dark.svg", "mark-light.svg", "mark-mono.svg"]) {
  copyFileSync(join(brand, f), join(pub, "brand", f));
}
