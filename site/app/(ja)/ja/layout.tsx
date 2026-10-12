import { metadataFor, viewport } from "../../_lib/meta";
import { mono, sans } from "../../_lib/fonts";
import "../../globals.css";

export const metadata = metadataFor("ja");
export { viewport };

export default function JaLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja" data-theme="dark" className={`${sans.variable} ${mono.variable}`}>
      <head>
        {/* Lets the stylesheet hide reveal-on-scroll blocks only when the script that reveals them will run. */}
        <script dangerouslySetInnerHTML={{ __html: "document.documentElement.classList.add('js')" }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
