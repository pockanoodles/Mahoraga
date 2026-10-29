// The site's three pages and where each lives in each language. Every link
// between pages, the metadata and the sitemap read their paths from here.
import type { Locale } from "@/content/copy";

export const PAGES = ["research", "engine", "install"] as const;
export type Page = (typeof PAGES)[number];

const ROOT: Record<Locale, string> = { en: "/mahoraga/", ja: "/mahoraga/ja/" };

export function pagePath(locale: Locale, page: Page): string {
  return page === "research" ? ROOT[locale] : `${ROOT[locale]}${page}/`;
}
