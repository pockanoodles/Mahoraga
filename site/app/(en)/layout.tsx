import { metadataFor, viewport } from "../_lib/meta";
import { mono, sans } from "../_lib/fonts";
import { ViewTransitionScript, ViewTransitionSurface } from "@/components/ViewTransition";
import "../globals.css";

export const metadata = metadataFor("en");
export { viewport };

export default function EnLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" data-theme="dark" className={`${sans.variable} ${mono.variable}`}>
      <head>
        {/* Lets the stylesheet hide reveal-on-scroll blocks only when the script that reveals them will run. */}
        <script dangerouslySetInnerHTML={{ __html: "document.documentElement.classList.add('js')" }} />
        <ViewTransitionScript />
      </head>
      <body>
        <ViewTransitionSurface />
        {children}
      </body>
    </html>
  );
}
