import { viewport } from "../_lib/meta";
import { mono, sans } from "../_lib/fonts";
import "../globals.css";

export { viewport };

export default function EnLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    // The script below adds a class to <html> before React hydrates, so React is
    // told to expect that one attribute to differ from the server's HTML.
    <html lang="en" data-theme="dark" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        {/* Lets the stylesheet hide reveal-on-scroll blocks only when the script that reveals them will run. */}
        <script dangerouslySetInnerHTML={{ __html: "document.documentElement.classList.add('js')" }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
