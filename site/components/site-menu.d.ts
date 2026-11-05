import type { DetailedHTMLProps, HTMLAttributes } from "react";

// <site-menu>, kaitosoeno.com's menu pill: defined by /site/menu.js, which
// personal-website serves (components/site-menu/element.ts there).
declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "site-menu": DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
        concealed?: boolean;
      };
    }
  }
}
