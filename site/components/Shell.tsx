// What every page shares: the bar with its three pages, the footer and the
// site's own menu. Each page passes its sections as children.
import type { Dict, Locale } from "@/content/copy";
import { UPDATED } from "@/content/log";
import { REPO_URL } from "@/lib/data";
import { PAGES, pagePath, type Page } from "@/lib/routes";
import { Footer } from "./Footer";
import { LocalNav } from "./LocalNav";
import { SiteMenu } from "./SiteMenu";

export function Shell({ t, locale, page, children }: { t: Dict; locale: Locale; page: Page; children: React.ReactNode }) {
  const { updated, github, label } = t.nav;
  const links = PAGES.map((p) => ({ href: pagePath(locale, p), label: t.nav[p], current: p === page }));
  const other =
    locale === "en"
      ? { href: pagePath("ja", page), label: "日本語", lang: "ja" }
      : { href: pagePath("en", page), label: "English", lang: "en" };

  return (
    <>
      <LocalNav
        t={{ github, label }}
        updated={UPDATED}
        updatedLabel={updated(UPDATED)}
        repo={REPO_URL}
        home={pagePath(locale, "research")}
        links={links}
        other={other}
        hero={page === "research"}
      />
      <main id="top">{children}</main>
      <Footer t={t} locale={locale} page={page} />
      <SiteMenu t={t.menu} />
    </>
  );
}
