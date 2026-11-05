import type { MetadataRoute } from "next";
import { ALL_PAGES, pagePath } from "@/lib/routes";
import { SITE } from "./_lib/meta";

// Served at /mahoraga/sitemap.xml. The domain's robots.txt belongs to the
// personal site, which should list this sitemap next to its own.
export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  return ALL_PAGES.flatMap((page) => {
    const languages = { en: `${SITE}${pagePath("en", page)}`, ja: `${SITE}${pagePath("ja", page)}` };
    return [
      { url: languages.en, alternates: { languages } },
      { url: languages.ja, alternates: { languages } },
    ];
  });
}
