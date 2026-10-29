# Mahoraga brand

One look for the app (`frontend/`) and the landing page (kaitosoeno.com/mahoraga). The
tokens live in `tokens.css`; this file says why they are what they are, so a later change
can disagree with a reason instead of a hex.

## The idea

Mahoraga is an engine that keeps measuring whether local coding AI is worth it on real
code, and the landing page is its log. Each entry answers with a measurement and its
uncertainty, and the corrections and voided runs stay in the log next to the findings. The
page opens on the latest answer and the chart behind it, so a reader meets the result before
the method. The look follows from that. It is calm and exact, and its one colour marks the
share of work local can take. Spacing comes from ui-master Design 03 ("Studio"), which is the
spacing Kaito picked on 2026-10-12. The landing page sits on the same black as the rest of
kaitosoeno.com, so it continues the site instead of interrupting it.

## Mark

An eight-spoke wheel. Mahoraga's wheel turns one notch each time it adapts, and the single
lit spoke is that notch. Files: `mark-dark.svg` (on dark), `mark-light.svg` (on light),
`mark-mono.svg` (`currentColor`, for inline use), `favicon.svg` (heavier strokes for 16px,
follows the OS theme).

- Keep the lit spoke at twelve o'clock. Rotating it is reserved for motion that means
  something (the verdict moving).
- Minimum size 16px. Clear space around it is half its width.
- Never add a container, a gradient, a shadow, or a second accent.
- The wordmark is live text: "Mahoraga" in Geist 600 at -0.02em, 8–10px from the mark.

## Colour

| Token | Light | Dark | Job |
|---|---|---|---|
| `--mh-stage` | `#fcfcfa` | `#000000` | Landing page and hero stage |
| `--mh-bg` | `#fcfcfa` | `#0b0a09` | App window |
| `--mh-surface` | `#f6f5f3` | `#141211` | Sidebar, grouped panels |
| `--mh-raised` | `#ffffff` | `#1d1b19` | Inputs, popovers |
| `--mh-line` / `-strong` | `#e4e3e0` / `#cfcdca` | `#282624` / `#3d3a37` | Hairlines |
| `--mh-ink` | `#161311` (18.5:1) | `#f5f3f0` (19.0:1) | Text |
| `--mh-muted` | `#55524e` (7.8:1) | `#b4b0ac` (9.8:1) | Secondary text |
| `--mh-faint` | `#76726e` (4.8:1) | `#8c8884` (6.0:1) | Tertiary text, labels |
| `--mh-accent` | `#bf5504` (4.7:1) | `#f79643` (9.4:1) | "Local" |
| `--mh-danger` | `#c9302d` | `#f2716a` | Errors only |

Contrast is against `--mh-bg` (light) and `#000` (dark). The accent was sampled from the
amber-on-black terminal look recorded as Mahoraga's palette, pulled toward terracotta, at
oklch chroma 0.15. **One accent, and it means "local"**: the resolved share of tasks, the
measured interval, the verdict word, the lit spoke, and keyboard focus. Status that is not
"local could take this" is carried by shape and ink weight, never by a second colour.

## Type

Geist for everything that is read and Geist Mono for anything a machine prints (commands,
SHAs, model tags, tables of figures). Both are SIL OFL and self-hosted, so the app makes no
font request to anyone.

- Display: 600, tracking tightening with size (-0.02em at 32px, -0.035em at 72px+),
  leading 1.05 or below at hero sizes. This departs from Design 03's regular-weight display
  on purpose, since the brief asked for an Apple-like page and SF-style display type is set
  semibold.
- Body 16–18px at 400 with 1.5 leading. Labels 11–12px at 500, uppercase, `0.12em`.
- Figures are tabular wherever they line up in a column.

## Space and shape

Design 03, verbatim: the 4 → 128px ladder, one gutter (16px, 32px from 640px), outer gaps
at least three times inner gaps, one idea per screen on the landing page, a corner-anchored
hero. Controls are 48px pills (32px in the app's dense toolbars). Panels are 18px-radius
soft rectangles, one level deep and never nested. No shadows. Depth on the landing page comes
from lighting (the stage), and in the app from the surface steps.

Corners are concentric, as Apple's are. A control in a panel's corner keeps the corner's
centre, so its radius is the panel's less the gap between them, never below
`--mh-radius-floor` (the code panel's tabs sit 12px into an 18px corner, so they are 6px). A
pill inside a pill is concentric when its inset is the difference of the two radii, which is
why the install commands are 44px capsules around a 32px copy button.

## Motion

One curve, `cubic-bezier(0.2, 0.8, 0.2, 1)`. UI feedback 160–240ms, entrances 700ms with a
12–16px rise, staggered 60–90ms in a series. Scroll-linked motion is CSS
(`animation-timeline`) behind `@supports`, never a scroll listener. Everything has a
reduced-motion state that shows the final frame.

Motion reveals a figure and never changes what it says. A figure opens on its finding, a
number changes only when the reader changes a setting, and it rolls to its new value. A
segmented control's thumb slides to the reader's choice.

## Voice

Plain, specific, and honest about uncertainty. Every number on a page comes from
`docs/site/site.json`, which the engine generates from committed data, and carries the
sentence that says what it means. The engine is allowed to say "you're not wasting money".
