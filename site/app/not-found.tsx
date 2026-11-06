import type { Metadata } from "next";
import { mono, sans } from "./_lib/fonts";
import { Mark } from "@/components/Mark";
import "./globals.css";

// Without its own title the tab showed the bare URL.
export const metadata: Metadata = { title: "Nothing here · Mahoraga" };

// The Worker serves this for any missing path under /mahoraga. It has its own
// <html> because the two languages are separate root layouts.
export default function NotFound() {
  return (
    <html lang="en" data-theme="dark" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <main className="lost">
          <Mark size={44} />
          <h1 className="h2">Nothing here.</h1>
          <p className="lede">That page isn&apos;t part of Mahoraga&apos;s site, which is in English and Japanese.</p>
          <p className="lost__links">
            <a className="pill" href="/mahoraga/">
              Mahoraga
            </a>
            <a className="textlink" href="/mahoraga/ja/" lang="ja">
              日本語
            </a>
          </p>
        </main>
      </body>
    </html>
  );
}
