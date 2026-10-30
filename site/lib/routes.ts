// The site's pages and where each lives in each language. Every link between
// pages, the metadata and the sitemap read their paths from here. PAGES are the
// three the bar names. The privacy policy is a page too, reached from the
// footer, so it is in ALL_PAGES but not in the bar.
import type { Locale } from "@/content/copy";

export const PAGES = ["research", "engine", "install"] as const;
export type Page = (typeof PAGES)[number] | "privacy";
export const ALL_PAGES: readonly Page[] = [...PAGES, "privacy"];

const ROOT: Record<Locale, string> = { en: "/mahoraga/", ja: "/mahoraga/ja/" };

export function pagePath(locale: Locale, page: Page): string {
  return page === "research" ? ROOT[locale] : `${ROOT[locale]}${page}/`;
}
