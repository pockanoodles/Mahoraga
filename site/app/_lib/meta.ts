import type { Metadata, Viewport } from "next";
import { copy, type Locale } from "@/content/copy";

export const SITE = "https://kaitosoeno.com";

const PATHS: Record<Locale, string> = { en: "/mahoraga/", ja: "/mahoraga/ja/" };

export function metadataFor(locale: Locale): Metadata {
  const t = copy[locale].meta;
  return {
    metadataBase: new URL(SITE),
    title: t.title,
    description: t.description,
    alternates: { canonical: PATHS[locale], languages: { en: PATHS.en, ja: PATHS.ja, "x-default": PATHS.en } },
    icons: { icon: "/mahoraga/favicon.svg" },
    openGraph: {
      type: "website",
      url: PATHS[locale],
      title: t.title,
      description: t.description,
      siteName: "Mahoraga",
      locale: locale === "ja" ? "ja_JP" : "en_US",
      images: [{ url: "/mahoraga/og.png", width: 1200, height: 630, alt: t.title }],
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
