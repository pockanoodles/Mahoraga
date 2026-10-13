import { Geist, Geist_Mono } from "next/font/google";

// The brand's two voices (brand/BRAND.md), self-hosted by next/font at build
// time, so the page makes no font request to anyone. Japanese falls through to
// the system's gothic (Hiragino on Apple devices), set in globals.css.
export const sans = Geist({ subsets: ["latin"], variable: "--font-geist" });
export const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });
