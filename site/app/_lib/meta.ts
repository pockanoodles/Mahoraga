import type { Metadata, Viewport } from "next";
import { copy, type Locale } from "@/content/copy";
import { pagePath, type Page } from "@/lib/routes";

export const SITE = "https://kaitosoeno.com";

export function metadataFor(locale: Locale, page: Page): Metadata {
  const t = copy[locale].meta[page];
  const path = pagePath(locale, page);
  return {
    metadataBase: new URL(SITE),
    title: t.title,
    description: t.description,
    alternates: {
      canonical: path,
      languages: { en: pagePath("en", page), ja: pagePath("ja", page), "x-default": pagePath("en", page) },
      types: { "application/atom+xml": [{ url: "/mahoraga/log.xml", title: copy.en.log.feed }] },
    },
    icons: { icon: "/mahoraga/favicon.svg" },
    openGraph: {
      type: "website",
      url: path,
      title: t.title,
      description: t.description,
      siteName: "Mahoraga",
      locale: locale === "ja" ? "ja_JP" : "en_US",
      images: [{ url: "/mahoraga/og.png", width: 1200, height: 630, alt: copy[locale].meta.research.title }],
    },
    twitter: { card: "summary_large_image", title: t.title, description: t.description, images: ["/mahoraga/og.png"] },
  };
}

export const viewport: Viewport = {
  themeColor: "#000000",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};
