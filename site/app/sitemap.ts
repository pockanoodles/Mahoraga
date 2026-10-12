import type { MetadataRoute } from "next";
import { SITE } from "./_lib/meta";

// Served at /mahoraga/sitemap.xml. The domain's robots.txt belongs to the
// personal site, which should list this sitemap next to its own.
export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${SITE}/mahoraga/`, alternates: { languages: { en: `${SITE}/mahoraga/`, ja: `${SITE}/mahoraga/ja/` } } },
    { url: `${SITE}/mahoraga/ja/`, alternates: { languages: { en: `${SITE}/mahoraga/`, ja: `${SITE}/mahoraga/ja/` } } },
  ];
}
