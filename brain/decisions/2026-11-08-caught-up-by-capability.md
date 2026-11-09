# ADR 2026-11-08 — Caught up means capability: a paired gap inside a fixed margin

## Status
Accepted (2026-11-08, late evening). Kaito asked for a planning session on a capability test,
I proposed the rule below with six recommendations, and he answered "go on ur recs".

**Supersedes:** decision 2 of `2026-11-08-the-map.md` (caught up as the low end of local's
interval clearing the break-even rate). The rest of that ADR stands. The break-even verdict
stays as the reader's tool and moves to its own column.

This record is the pre-registration. It fixes the test, the margin and the conditions on a
crossing before any new run. Every row measured so far reads "behind" at any margin a reader
would accept (the smallest gap is 55 points), so choosing the margin now can't be shaped by a
result.

## Context

The cloud tiers finished on the evening of 2026-11-08 (findings Era 41). Claude Haiku 5.5
solved all 47 tasks at about a cent each, which put the price-based break-even line at 100%.
Against a tier that cheap, "caught up" asked local to never fail, so the line measured the
cloud's price more than local's ability. Kaito had already said the study is about
capability, meaning what a model on owned hardware can do.

Three facts shaped the rule.

**1. "Not significantly below" rewards small samples.** My first suggestion was to call local
caught up when its rate isn't significantly below the tier's. On 10 tasks, local at 5 against
the cloud's 8 isn't significant, so a small row would cross easily. Parity needs evidence that
the gap is small, and a test that fails to find a gap isn't that evidence. That suggestion is
withdrawn.

**2. Forty-seven tasks can't show parity even between cloud tiers.** Sonnet 5.5 missed 4 of
the tasks Opus 5.5 solved and solved none Opus missed. With a 10-point margin, that gap of 8.5
points has a 95% interval of 0.3 to 19.9 points, which straddles the margin. At 47 tasks local
reads "caught up" only if it loses no task the cloud solves.

**3. The tasks set the cost.** A local model that really is level, disagreeing with the cloud
on 10% of tasks, reads "caught up" 43% of the time at 47 tasks, 83% at 100, 94% at 150 and 98%
at 200. At 20% disagreement the figures are 32%, 59%, 78% and 89% (simulated, 600 draws each).
The cloud side of 150 tasks costs about $1.50 on Haiku and $32 on Opus. The local side is the
cost: the laptop took about three nights for 47 tasks.

## Decision

1. **The test is the paired gap.** For each cloud tier on each row, the gap is the cloud's
   resolve rate minus local's, on the tasks both ran, one attempt a side. Its 95% interval is
   Tango's score interval for paired proportions, one of the intervals Fagerland, Lydersen and
   Laake (2014) recommend, computed in `agentbench/stats.py:paired_difference`. The row reads
   one of four standings against the margin:

   | Standing | When |
   |---|---|
   | behind | the whole interval is above the margin |
   | open | the interval straddles the margin |
   | caught up | the whole interval is below the margin |
   | ahead | the whole interval is below zero |

2. **The margin is 10 percentage points for rows graded pass or fail, fixed for the life of
   the map.** It lives in `audit/capability.py` as `MARGIN`, and changing it takes a new ADR.
   Ten points is about how close the cloud's own tiers are to each other on these tasks
   (Sonnet trails Opus by 8.5), and it is the usual non-inferiority margin for a binary
   outcome. Five points would need 300 or more tasks a row.
3. **Both sides run under the same agent, so a crossing belongs to the model.** The cloud runs
   Claude Code, so local runs Claude Code driving the local model through Ollama, on a named
   machine. Rows whose local side ran under another agent (today's aider rows) carry their
   standing, labelled with the agent, until the same-agent run replaces them. On the Studio,
   Claude Code with qwen3.5 already beat aider with qwen3.5 on the same tasks (24 to 12 of
   36), so the same agent is also the strongest local setup measured.
4. **A crossing needs the commit-date split to agree.** The standing is read on all of a row's
   tasks. A row can't be printed as caught up or ahead while the tasks committed after the
   cloud model's stated training cutoff read "behind" on their own. The factory mines new
   commits, so this check gets stronger as the row grows.
5. **A crossing is confirmed before it is printed.** The map re-tests every tier on every row
   each month, and with enough repeated tests a 95% interval will eventually cross by chance.
   So a crossing is printed only once the next batch of freshly mined tasks has been added
   and the standing still holds on the pooled tasks, and the fresh batch alone doesn't read
   "behind". The site's build refuses a crossing that has no confirmation claim.
6. **The price line becomes its own column.** The break-even verdict, priced at what each
   tier spent on the same tasks, answers the reader's question (stay, split or switch) and no
   longer decides whether local has caught up.
7. **Other kinds of work use the same four standings.** Each row's answer key fixes its margin
   in its own units before its first run (word error rate points for speech to text, for
   example), and a metric that isn't pass or fail takes a paired bootstrap interval on the
   per-item differences. The speech-to-text positive control tests the test: if it can't read
   "caught up" where local is expected to be level, the test is at fault, not local.

## Where every row stands at the decision

| Row | Tier | Gap (cloud minus local) | 95% interval | Standing |
|---|---|---|---|---|
| 47 tasks, laptop (aider) | Opus 5.5 | 64 points | 50 to 76 | behind |
| 47 tasks, laptop (aider) | Sonnet 5.5 | 55 points | 38 to 69 | behind |
| 47 tasks, laptop (aider) | Haiku 5.5 | 64 points | 50 to 76 | behind |
| Sonnet against Opus (check) | Opus 5.5 | 8.5 points | 0.3 to 19.9 | open |

The 92-task row has no cloud run, so it has no standing.

## Alternatives considered

- **Keep the price-based line.** Rejected: against a tier that costs a cent it asks for 100%,
  so it measures price, and Kaito asked about capability.
- **"Not significantly below" the tier.** Rejected for fact 1.
- **A five-point margin.** Rejected: 300 or more tasks a row before a level model has a fair
  chance, when the laptop took about three nights for 47.
- **A margin tied to the spread between cloud tiers.** Rejected: it would move with every
  release, and a dated map needs one bar across years.
- **Newcombe's hybrid score interval instead of Tango's.** Either would do. Tango's is the
  score test inverted directly, so the interval and the test can't disagree.

## Consequences

- Every tier reads "behind" today. The map now says by how much, with an interval, which a
  later run can be compared against.
- The coding row needs about 150 tasks for the test to be fair. The work is #100 (the local
  Claude Code arm's output cap), then the laptop's same-agent run on the 47, then more tasks
  from the factory, with every tier run on each new batch.
- The speech-to-text row is the next kind of work, as the positive control.
- `CLAUDE.md`, the README, the report and the site state the rule this way.
