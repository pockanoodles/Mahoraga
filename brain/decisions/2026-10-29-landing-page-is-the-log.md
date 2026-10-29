# ADR 2026-10-29 — The landing page is the audit's running log, with exhibits made from its data

## Status
Accepted (2026-10-29).

**Builds on:** `2026-10-12-launch-shape.md`, decision 3 (the page lives in this repo and
renders only `docs/site/site.json`). It replaces that decision's picture of the page as an
open-source product page.

## Context

Kaito, looking at the page deployed for testing: "Mahoraga's page is misleading. It is less
of an app, and more of an engine, or a continuous research log." He also asked what framing
would make the project stand out, and how the page could be pretty yet informative.

The page led with the parked router's HumanEval+ cascade. Its hero and its calculator ran on
the September probe, 18 of Mahoraga's own commits on a machine nobody recorded, and the
calculator said Stay at 30 of its 32 price stops without saying why a positive saving could
still be a stay. The October report (#48) had the stronger material: 92 outside commits
under three settings, the context-window finding, the split by release date, and the
corrections in its section 6.

## Decision

1. **The page opens on where local stands, and the H1 gives the answer.** The hero draws the
   three October claims against the rate local needs at $0.75 a task, and says that for most
   people the answer is still to keep paying for cloud. The laptop's own run is a row marked
   running, with no interim counts, since reading a rate while it runs is how a measurement
   fools its author.
2. **A dated log replaces the evidence and corrections sections.** `content/log.ts` holds every
   finding, correction, voided run and null result, newest first. Each entry reads its
   numbers from a claim and links receipts pinned to the export's commit. The same entries
   render an Atom feed at `/mahoraga/log.xml`. A report edition is one more log entry, so
   editions need no archive of their own.
3. **Exhibits are built from the data, each in its own form.** One task in full: a committed
   specimen (`scripts/specimen.py`), picked by position and not by outcome. The settings
   grid: all 92 commits under 32k, 131k and the 35B model, with the paired test between
   neighbouring settings, so a reader sees which commits moved and not only how many. The
   calculator runs on the headline claim, with triage time as a second control and the cost
   of an attempt's electricity next to the cost of checking a failed one. The September
   replay stays as the exhibit of evidence settling.
4. **The engine says why a saving can still be a stay.** Every reason the materiality bar
   decides (the larger of $5 and 5% of the cloud bill) names the bar, and a loss is written
   as what local costs more, never as a negative saving.
5. **The export stays on the server.** Client components get their numbers as props and
   import only `lib/format.ts`.

## Alternatives considered

- **Keep the product page and fix its numbers.** It would still mislead, because what the
  product produces is the measurement, so the measurement is what the page should show.
- **Dated editions with their own archive strip.** The log covers the same ground at a finer
  grain, and an edition reads as one of its entries.
- **"The leaderboard says 70, your repo says 30."** Rejected. Ornith's 70.6 comes from a
  scaffold trained with the model, on other tasks, and the report says the two numbers
  aren't comparable.

## Consequences

- Every new result needs a log entry, and `HEADLINE` in `scripts/export_site.py` moves when a
  newer measurement leads (the laptop's 131k row, once its run finishes).
- Each edition reruns `scripts/specimen.py` on its first task, then the export.
- The calculator's curves grow with each triage time offered (five today), and the settings
  grid with each setting.
