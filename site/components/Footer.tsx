// The footer every page ends on: the project's pages, its files in the
// repository and its privacy policy, and the person's own site.
import type { Dict, Locale } from "@/content/copy";
import { GIT } from "@/content/channels";
import { REPO_URL, blob } from "@/lib/data";
import { PAGES, pagePath, type Page } from "@/lib/routes";
import { Mark } from "./Mark";

const REPORT = "docs/report/2026-10-local-coding-ai.md";

export function Footer({ t, locale, page }: { t: Dict; locale: Locale; page: Page }) {
  const ja = locale === "ja";
  return (
    <footer className="foot">
      <div className="foot__brand">
        <Mark size={40} />
        <p className="foot__made">{t.footer.made}</p>
        <p className="foot__legal">© 2026 Kaito Soeno · {t.footer.license}</p>
        <p className="foot__legal">{t.footer.affiliation}</p>
      </div>
      <nav className="foot__cols" aria-label="Footer">
        <div>
          <h2>{t.footer.page}</h2>
          <ul>
            {PAGES.map((p) => (
              <li key={p}>
                <a href={pagePath(locale, p)} aria-current={p === page ? "page" : undefined}>
                  {t.nav[p]}
                </a>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h2>{t.footer.project}</h2>
          <ul>
            <li>
              <a href={REPO_URL} target="_blank" rel="noreferrer">
                GitHub
              </a>
            </li>
            <li>
              <a href={blob(REPORT)} target="_blank" rel="noreferrer">
                {t.footer.report}
              </a>
            </li>
            <li>
              <a href={`${GIT}/blob/main/docs/RESULTS.md`} target="_blank" rel="noreferrer">
                {t.footer.results}
              </a>
            </li>
            <li>
              <a href={`${GIT}/blob/main/DATAFLOW.md`} target="_blank" rel="noreferrer">
                {t.footer.dataflow}
              </a>
            </li>
            <li>
              <a href={`${GIT}/blob/main/CONTRIBUTING.md`} target="_blank" rel="noreferrer">
                {t.footer.contributing}
              </a>
            </li>
            <li>
              <a href={`${GIT}/security`} target="_blank" rel="noreferrer">
                {t.footer.security}
              </a>
            </li>
            <li>
              <a href={pagePath(locale, "privacy")} aria-current={page === "privacy" ? "page" : undefined}>
                {t.footer.privacy}
              </a>
            </li>
          </ul>
        </div>
        <div>
          <h2>{t.footer.person}</h2>
          <ul>
            <li>
              <a href={ja ? "/ja/portfolio" : "/portfolio"}>{t.footer.portfolio}</a>
            </li>
            <li>
              <a href={ja ? "/ja" : "/"}>{t.footer.contact}</a>
            </li>
            <li>
              <a href={pagePath(ja ? "en" : "ja", page)} hrefLang={ja ? "en" : "ja"} lang={ja ? "en" : "ja"}>
                {t.footer.language}
              </a>
            </li>
          </ul>
        </div>
      </nav>
    </footer>
  );
}
