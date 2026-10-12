// Media that may not exist yet. The page is built statically, so whether an
// image has landed in public/ is known at build time; until it has, its slot
// renders as an honest labelled box and no request is made for it.
import { existsSync } from "node:fs";
import { join } from "node:path";

export function hasAsset(path: string): boolean {
  return existsSync(join(process.cwd(), "public", path));
}
