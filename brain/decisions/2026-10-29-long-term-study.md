# ADR 2026-10-29 — Mahoraga is a long-term study of local against cloud coding AI, and the engine is how a reader repeats it

## Status
Accepted (2026-10-29).

**Builds on:** `2026-09-10-longitudinal-thesis.md` (the headline question is longitudinal),
`2026-10-02-local-audit-product.md` (the report and the tool answer one question, and routing
comes last), `2026-10-29-landing-page-is-the-log.md` and `2026-10-29-three-pages.md`. It keeps
their decisions and says what each of the three pages is for.

## Context

On 2026-10-29 Kaito read the deployed site and objected to the settings section's title, "The
upgrade that mattered was a setting.", since it read as though local coding AI only needed a
switch flipped. He then set out what the project is. Its value is a long-term study of local
against cloud AI. The research page is where he writes up what he finds. The engine asks
whether the judgment between local and cloud can be made seamless, mostly as a way for readers
to replicate the research on their own machines. Later, readers might share some of their
results so the findings can come out in paper form. The install page is the engine's logistics.

Three facts shaped the decision.

**1. Every measured setting still says stay.** On the 92 outside commits, the laptop's 9.7B
model solved 9 at a 32k window and 28 at 131k, and a 35B model solved 31, all on the 64 GB
Studio. At $0.75 a cloud task local pays for itself only above a 77% resolve rate, because
each failure costs a developer's time to check, so all three verdicts are stay. The laptop's
own run at 131k is still under way.

**2. Routing between local and cloud is already a product category.** RouteLLM, Not Diamond and
OpenRouter's auto router pick a model per request, and Cursor's Auto mode does it inside an
editor. None of them measures whether local is good enough on the reader's own repository and
hardware, which is the part Mahoraga already does.

**3. The sharing was anticipated but never specified.** The 10-02 ADR designed the ledger so
results could be pooled across users and left the sharing unbuilt. Nothing yet says what a
shared record may contain, and the site's promise is that nothing leaves the machine.

## Decision

1. **Mahoraga is a long-term study.** It measures local and cloud coding AI on real commits,
   on hardware people own, and measures again as models and machines change. The hero's
   question stays "Should you keep paying for cloud coding AI, or run a model on your own
   machine?", and its answer is dated because it is expected to move.
2. **The research page publishes the study, one finding at a time.** A finding is a measured
   number with what it costs and where it stops holding (the machine it ran on, the tasks it
   covers), and it sits in the log beside the corrections and voided runs. Writing up a result
   means writing it in that unit. A result that raises local's number also says how far it
   remains from the break-even line and what it costs on a 16 GB laptop, as the settings
   section has since c2580ea. Once the same repositories have been measured at two dates, the
   change between them leads the page.
3. **The engine is the judgment, shipped as a replication kit.** A reader runs the same
   pipeline on their own repository and machine and gets their own rate and verdict from the
   code that produced the page's. Live routing between local and cloud stays the last stage
   (10-02, decision 10), and it is built only after a verdict says split for some slice of
   real work. No measured setting has said so yet.
4. **The install page is logistics.** It installs, runs and scripts the engine, and it makes no
   claims of its own.
5. **Pooled results wait for five conditions.** Each one is met before any code for sharing is
   written.
   - Contributing is opt-in for each run, and the reader sees the exact payload before it
     leaves.
   - A shared record carries the hardware, the model digest, the agent and its version, the
     settings, the time taken, the outcome and a hashed task id. It never carries code,
     prompts, diffs, file paths or repository names.
   - Every contributor also runs a shared public set of commits (today's 92, or whatever
     replaces them), since rates measured on different private repositories can't be compared.
   - `DATAFLOW.md` says what leaves, where it goes and why before anything does, and the
     upload goes through one audited client.
   - A pooled number says who contributed it, since volunteers skew toward larger machines, and
     it is never presented as representative.

## Alternatives considered

- **Make seamless routing the product.** Rejected for now, since routers already exist and
  every verdict Mahoraga has measured says stay, which leaves a router nothing to send local.
- **Run the research page as essays.** An essay can link to findings, but the finding with its
  cost and its limit is the part no one else publishes, so it stays the page's unit.
- **Start collecting results now.** Rejected, since results from private repositories without
  a shared set of commits can't be compared, and a payload that might carry code would break
  the promise the site is built on.

## Consequences

- Copy that reports a gain for local names the bar it is measured against and the machine it
  ran on.
- The study's series needs repeated measurements of the same repositories. The engine already
  mines new commits each window, so each new model meets commits made after its release, and a
  model's rate can be split by whether a commit predates it, as the report's section 3 does.
- Pooled results need their own ADR and a `DATAFLOW.md` section before they are built.
- `CLAUDE.md` describes the project this way and points here.
