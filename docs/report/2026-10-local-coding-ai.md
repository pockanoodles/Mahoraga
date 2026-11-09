# Is local coding AI worth it on a 16 GB Mac? (November 2026)

**Draft.** Every figure here links to the artifact it came from, and
`orch bench verify` recomputes each one from that artifact. The laptop's own
131k run, which sections 3 to 5 report, ran night by night from 2026-10-28 to
2026-11-01, and the cloud agent that section 4 measures on the same tasks ran
on 2026-11-05 (Opus 5.5) and 2026-11-08 (Sonnet 5.5 and Haiku 5.5), so this
edition is dated November.

## The question

Most developers who use a coding agent pay for a cloud model, either through
a flat subscription or by the token. Free local models keep improving, so the
fair question is whether any of that cloud spend could move to a model on your
own laptop. Leaderboards cannot answer it, because they measure someone
else's tasks on someone else's hardware. Vendors will not answer it either,
since the honest answer is sometimes "don't buy this." This report answers it
for one machine (a MacBook Pro with 16 GB of unified memory) and for real
repositories, and the tool behind it runs the same measurement on yours. A
second machine (a Mac Studio with 64 GB) shows what more memory changes.

## The short answer

For most people the answer is to keep paying for cloud. The model that fits
this laptop resolves about one real commit in ten on three outside
repositories (10% of 92 tasks) at a 32k context, and about three in ten (30%)
once the context is raised to 131k. Both of those rates come from the
laptop's exact model file run on a faster machine. On the laptop itself at
131k, the same model resolved 17 of the 47 outside tasks it attempted (36%),
one fewer than the faster machine resolved on the same 47. The laptop
therefore reaches about the same answers and only takes longer, with a median
attempt of 28 minutes. A 64 GB machine runs a larger model that resolves about the same
share (34% of the 92 tasks), so most of what first looked like a hardware gap
was a settings gap. The repository's own tests caught every failure on both
machines. None of these rates pays at today's cloud prices, because the
developer's time to notice a failed attempt outweighs the electricity several
hundred to one. At the wider context, local starts to pay once a cloud task
costs between about $4.40 and $5.70, depending on the model and the tasks,
which describes a team paying per token for long agentic work rather than a
subscriber.

The cloud side is now measured as well. Claude Code running Claude Opus 5.5
resolved all 47 of the laptop's tasks, with a median attempt under 40 seconds,
and at API list prices each task cost $0.21 on average. That price is about a
twentieth of the $4.43 at which the laptop's own run starts to pay, so on
these repositories the cloud solves as many tasks as the report's verdicts
assume, at under a third of the price they assume. The two cheaper tiers ran
the same tasks on 2026-11-08. Claude Haiku 5.5 also resolved all 47, at about
a cent a task, and Claude Sonnet 5.5 resolved 43 at $0.07, so local trails
every tier. Measured as a gap on the same tasks, every tier's resolve rate
sits 55 to 64 points above local's.

## 1. The local tier stopped getting new models

Between June and September 2026 the flagship open model lines moved up and
out of the size that fits consumer hardware ([Era 31](../../brain/state/findings.md)).
Qwen 3.6 and 3.8 ship no model under 17 GB, the smallest `qwen3-coder` is
19 GB, and Meta's line now starts at 17.31 GB. Smaller releases did continue
(IBM's granite 4.2 8B and a 9B from ornith-ai, then in September Xiaomi's 9B
fine-tune of the same Qwen3.5 base and JetBrains' Mellum 2.1, a 12B model with
2.5B parameters active), but none of them has a verified advantage in writing
code, and the scores published for the last two are their makers' own. This matters for the thesis, since a
widening gap between a free local tier and a paid frontier tier makes routing
between them more useful rather than less.

## 2. The longitudinal point

In August, a cascade that tries the local model first and escalates to cloud
when a judge rejects the answer reached 0.921 pass@1 on HumanEval+. That is
94.4% of cloud quality at $8.47 per thousand tasks, against $35.97 for always
using cloud, so the cascade cut cost by 76.5%. The result was identical
across two independent runs.

In October we ran the same bank on granite 4.1 and on granite 4.2, which is
IBM's newer model at the same 8B size. IBM published a HumanEval+ figure for
4.1 (79.88) and none for 4.2, so this is the only protocol-matched comparison
of the two that we know of. Granite 4.1 passed 132 of the 164 tasks (0.805,
with a 95% interval of 0.738 to 0.858), which is within a point of IBM's own
figure. Granite 4.2 passed 130 (0.793, with an interval of 0.724 to 0.848),
and two of its answers ran past the ten-minute limit and count as failures.
The two models disagreed on 38 tasks, 20 that only 4.1 solved and 18 that only
4.2 solved, and a split that even is what chance alone would produce (exact
McNemar p = 0.87). The newer model therefore gives a 16 GB user no measurable
gain on this bank with reasoning off
([claims](../../experiments/claims.json), `report_2026_10_humaneval_*`).

Every answer in the run came from the model it is credited to, since the
cascade was off and none of the 328 answers went to the cloud. Neither model
reasoned before answering. Granite 4.1 has also held steady since August, as
its August answers graded again by the same grader pass 127 of 164 (0.774),
and a gap of 14 tasks against 9 between the two months is well within chance
(p = 0.40). Granite 4.2 can also reason before it answers, which takes about
four hours over the bank on this laptop and has not been run yet.

## 3. Local agents on real repositories

A benchmark built from a repository's own history asks a harder question than
a synthetic one. Each task is a real commit with its source change removed and
its tests kept, and an attempt counts only if the commit's tests pass and
nothing that passed before has broken.

| Agent and model | Machine | Repo | Tasks | Resolved | 95% interval (by task) |
| --- | --- | --- | --- | --- | --- |
| aider + qwen3.5 (9.7B, 32k context) | 16 GB laptop | Mahoraga | 18 | 5 | 12% to 51% |
| aider + granite 4.1 (8B, 32k) | 16 GB laptop | Mahoraga | 18 | 0 | 0% to 18% |
| aider + qwen3.5 (9.7B, 32k) | 16 GB laptop | ops | 9 | 1 | 2% to 43% |
| aider + qwen3.5 (9.7B, 32k) | 64 GB Studio | Mahoraga | 32 | 6 | 9% to 35% |
| aider + qwen3.5 (9.7B, 32k) | 64 GB Studio | more-itertools, click, attrs | 92 | 9 | 5% to 18% |
| aider + qwen3.5 (9.7B, 131k) | 64 GB Studio | more-itertools, click, attrs | 92 | 28 | 22% to 40% |
| aider + qwen3.5 (9.7B, 131k) | 16 GB laptop | more-itertools, attrs | 47 | 17 | 24% to 50% |
| aider + qwen3.6 (35B-A3B, 131k) | 64 GB Studio | Mahoraga | 32 | 13 | 26% to 58% |
| aider + qwen3.6 (35B-A3B, 131k) | 64 GB Studio | more-itertools, click, attrs | 92 | 31 | 25% to 44% |

Every attempt was blind and run once. The qwen3.5 rows on the Studio use the
laptop's exact model file (checksum verified) with the laptop's settings, so
they measure the laptop's model on faster hardware. The laptop's own run at
131k gave each attempt 90 minutes rather than 20, since the laptop reads a
100,000-token prompt for over ten minutes, and it covers the 47 tasks from
more-itertools and attrs (section 5). The Studio rows were run overnight on
2026-10-06 ([Era 36](../../brain/state/findings.md)), except qwen3.5 at 131k,
which ran on the night of 2026-10-13 with the same model file. The laptop's
run took the nights from 2026-10-28 to 2026-11-01. Every count in this
section and the next is recomputed by `orch bench verify` from one line per
attempt in [`experiments/agentbench/`](../../experiments/agentbench/), and CI
runs that check, so a reader can confirm each number without our machines.

On 2026-11-07 the 47 tasks behind the laptop and cloud rows were mined again
under two stricter checks. The first applies the commit's real fix to the
finished task and grades it like an attempt, so a test the fix itself breaks
can't count against an agent (SWE-bench's definition of the baseline). The
second runs each check five times and leaves out any test whose result
changes between runs. All 47 came out with the same target tests and the same
baseline, so no figure in this report changes.

Each agent got the commit's message as its task, trailers included. Nineteen
of the 92 outside prompts carried a line naming a co-author, and two of those
named a Claude model. Every arm read the same prompts, so the rows stay
comparable, and the next edition leaves out the lines that say who wrote a
commit, for every model.

Three things held across every run. The test gate caught every failed
attempt, so a test-gated queue never shipped a broken edit. The commonest
failure was an edit that fixed the target and broke neighbouring code, which
only the full-suite check sees. Rankings also flipped between interfaces.
Granite was the best of our models at answering prompts and the worst at
editing files, since 27 of its 36 attempts targeted code that was not in the
file.

The intervals count tasks, not attempts. Repeating a task tells you how stable
the model is on that task, but it adds no new evidence about tasks in general.
Nine tasks therefore leave an interval about 40 points wide however many times
they are rerun, which is why this report pools five repositories.

A benchmark built from public repositories invites the worry that the model
saw the answers in training. Qwen3.6 was released around 2026-04-16, so
commits made after that date are ones it cannot have seen. The three outside
repositories hold 56 tasks from such commits. On those it resolves 30% (17 of
56), against qwen3.5's 4% (2 of 56), and the two intervals do not overlap.
Given a 131k context, qwen3.5 resolves 23% of the same commits (13 of 56).
That interval overlaps qwen3.6's, and qwen3.5 is the older model, so it cannot
have seen them either. On more-itertools, the one repository with enough tasks on both sides of the
date, qwen3.6 resolves 6 of 15 earlier commits and 8 of 21 later ones. That flat rate is what an unmemorised model looks like.

## 4. When local pays

The verdict engine (`mahoraga audit verdict`) values each queued task as the
chance local solves it times the cloud cost it saves, minus electricity and
minus the developer's time to triage a failure. The pooled run below assumes
200 cloud tasks a month at $0.75 each ($150 of API spend), a quarter of them
patient enough for an overnight queue, and three minutes at $50 an hour to
triage each failure.

| Model (92 outside tasks unless noted) | Resolved | Net per month | Local pays once a cloud task costs | Verdict |
| --- | --- | --- | --- | --- |
| qwen3.5 at 32k (fits 16 GB, run on the Studio) | 10% [5%–18%] | −$109 (−$117 to −$96) | $23.06 | STAY |
| qwen3.5 at 131k (fits 16 GB, run on the Studio) | 30% [22%–40%] | −$76 (−$89 to −$59) | $5.72 | STAY |
| qwen3.6 at 131k (needs 24 GB or more) | 34% [25%–44%] | −$70 (−$85 to −$54) | $4.92 | STAY |
| qwen3.5 at 131k on the laptop itself (47 of those tasks) | 36% [24%–50%] | −$67 (−$86 to −$43) | $4.43 | STAY |

The laptop's own run gives the same verdict. On the 47 tasks it attempted,
running the queue locally costs about $67 a month more than it saves (between
$43 and $86), and local pays only once a cloud task costs $4.43. Only 2 of its
30 failures ran out of time, so the engine calls this stay decided, which it
refused to do for the laptop's 20-minute night (section 6). The laptop's tasks
leave out click, so its row is not a like-for-like comparison with the three
above it.

Local would need to resolve at least 77% of queued tasks to break even at
$0.75 a task, which is about twice what either model manages at 131k on
either machine. At a
32k context, running the queue on the laptop costs about $109 a month more in
triage time than it saves. Raising the context to 131k cuts that loss to about
$76 and moves the break-even price from $23 a task to about $6. The bigger
machine only adds a little to that, with a loss of about $70 and a break-even
price under $5. Earlier, on the ops repository
alone, qwen3.5 measured 11% and needed about $20 a task, so the pooled result
agrees with the first reading.

A flat subscription changes the arithmetic, since an extra cloud task costs
nothing. Local can then only help by moving someone to a cheaper tier or by
freeing room under a usage cap they keep hitting. Take a Max 5x user at 60% of
the cap. Dropping to Pro would need local to take all of their queueable work
with either model, so the engine answers that this user is not wasting money.

### The cloud side, measured

The verdicts in the table above assume two things about the cloud that the
study had not measured, namely that a cloud agent solves every task it is
given and that each task costs $0.75. To check both, Claude Code 2.1.318
running Claude Opus 5.5 attempted the laptop's 47 tasks on 2026-11-05, with
the same prompt, the same grader and the same 90-minute limit as the laptop's
131k run. It ran on a Dell OptiPlex 7070, which runs the agent and the tests
but no model. Each attempt started from an empty home directory, and its one
way out was a proxy that let traffic through to api.anthropic.com alone and
logged every connection. Over the 47 attempts that log holds 141 connections,
and the proxy refused none of them ([DATAFLOW.md](../../DATAFLOW.md) says what
each one carries).

| Agent and model | Resolved | Median attempt | Cost per task |
| --- | --- | --- | --- |
| Claude Code, Claude Opus 5.5 (the cloud) | 47 of 47, 100% [92%–100%] | under 40 seconds | $0.21 at list prices |
| aider, qwen3.5 at 131k (the laptop) | 17 of 47, 36% [24%–50%] | 28 minutes | the electricity |

Task by task, the cloud solved all 17 tasks the laptop solved as well as 30
that the laptop did not, and the laptop solved none that the cloud missed. If
the two agents were equally good, a split of 30 to 0 would happen about twice
in a billion runs (exact McNemar test). Memorisation does not explain the gap,
since 19 of the tasks were committed after June 2026, which is Opus 5.5's
stated knowledge cutoff. The cloud solved all 19 of those, and the laptop
solved 3.

The pair compares two agents as much as two models. Under the blind condition
neither agent is told how the task is graded, but Claude Code can still find
the repository's tests and run them through its shell, which aider cannot do.
Each agent ships that way, so the pair shows what a developer gets from each
one, and a comparison of the models alone would need both of them under one
agent.

The measured price moves the verdict more than the measured rate does. The
assumption that the cloud solves every task held on these 47, since the low
end of its interval is 92%. The price assumption did not hold, because a task
cost $0.21 at list prices, and the tasks the laptop solved cost the cloud only
$0.15 compared to $0.24 for the ones it failed. Local takes the easy tasks,
and those are the cheap ones, so each task local resolves saves less than the
average task costs. Priced at that $0.15, running the queue locally costs
about $77 a month more than it saves (between $58 and $94). Local would then
need to resolve 95% of queued tasks to break even, compared to 77% at the
assumed $0.75.

| Model | Resolved | Net per month | Break-even resolve rate | Verdict |
| --- | --- | --- | --- | --- |
| qwen3.5 at 131k on the laptop, priced at $0.75 a task | 36% [24%–50%] | −$67 (−$86 to −$43) | 77% | STAY |
| the same run, priced at what the cloud spent on the tasks the laptop solved | 36% [24%–50%] | −$77 (−$94 to −$58) | 95% | STAY |

These prices are what the CLI computes for each attempt at API list prices,
while the run itself went through a Claude seat, which has no per-task price.
A subscriber pays the flat fee instead, as the paragraph above describes. The
tasks are single commits, so a long agentic session costs more per task than
these did. Even so, the $4.43 at which local pays is about twenty times what
these tasks cost. `mahoraga audit verdict --cloud-arm` prices a reader's
verdict the same way once the cloud arm has run on their tasks
([claims](../../experiments/claims.json), `report_2026_11_*`).

### Every cloud tier

Opus 5.5 is the most expensive model Claude Code runs, so measuring the cloud
with it alone could set the bar higher than most cloud work does. Claude
Sonnet 5.5 and Claude Haiku 5.5 attempted the same 47 tasks on 2026-11-08,
with the same agent, flags, machine, limit and proxy as the Opus run.

| Agent and model | Resolved | Median attempt | Cost per task |
| --- | --- | --- | --- |
| Claude Code, Claude Opus 5.5 | 47 of 47, 100% [92%–100%] | under 40 seconds | $0.21 at list prices |
| Claude Code, Claude Sonnet 5.5 | 43 of 47, 91% [80%–97%] | about 19 seconds | $0.07 at list prices |
| Claude Code, Claude Haiku 5.5 | 47 of 47, 100% [92%–100%] | about 36 seconds | $0.01 at list prices |
| aider, qwen3.5 at 131k (the laptop) | 17 of 47, 36% [24%–50%] | 28 minutes | the electricity |

The cheapest tier did as well as the most expensive one, since Haiku 5.5
resolved all 47 tasks. Sonnet 5.5 missed 4, and each of those attempts ended
within 30 seconds with some of the target tests passing, so Sonnet stopped
early rather than running out of time. Sonnet against Opus is a split of 4 to
0. Two equally good agents would split that way about one time in eight
(exact McNemar test, p = 0.125), so these 47 tasks can't tell them apart.
One of Sonnet's misses (attrs `cbaef3f`) is a task the laptop solved, and it
is the only one of the 47 that local solved and a cloud tier missed.
Against the laptop, Sonnet's split is 27 to 1 (p = 2 × 10⁻⁷) and Haiku's is 30
to 0, the same as Opus's. On the 19 tasks committed after June 2026, Sonnet
solved 18 and Haiku all 19, compared to the laptop's 3.

A cheaper tier makes local's case harder. Haiku 5.5 solves as many of these
tasks as Opus does. The tasks the laptop solved cost Haiku less than a cent
each on average ($0.007), so local saves almost nothing on the work it can do.
The net per month barely moves across tiers (−$77 against Opus, −$79 against
Sonnet and −$80 against Haiku). This is because the developer's time spent on
local's failures outweighs the cloud price at every tier. What moves is the
break-even rate. Local needs to resolve 95% of the queue at what Opus spent, 98% at what
Sonnet spent and 100% at what Haiku spent.

Haiku 5.5 was the only tier that tried to reach anything besides the model.
It tried to install packages so it could run the tests, which made 217
connections to pypi.org and one to api.github.com, and the proxy refused all
of them. It then solved the tasks without those packages. Opus and Sonnet
connected to api.anthropic.com alone.

### Caught up, by capability

The break-even rate answers whether a reader should move work to local, and
it is priced by what the cloud spends. Haiku 5.5 spent less than a cent on
each task the laptop solved, so against Haiku the break-even rate asks local
to resolve 100% of the queue. A bar that high says more about the cloud's
price than about what local can do. The map therefore reads a second number
for each tier, which is the gap between the cloud's resolve rate and local's
on the same tasks ([the decision](../../brain/decisions/2026-11-08-caught-up-by-capability.md)).

The gap comes with a 95% interval (Tango's score interval for paired results),
and it is read against a margin of 10 points that was fixed before any new
run. Local has caught up with a tier when the whole interval sits below 10
points, and it is behind when the whole interval sits above. An interval that
straddles the margin is open, which means not settled. Ten points is about how close the
cloud's own tiers are on these tasks, since Sonnet trails Opus by 8.5.

| Tier | Gap on all 47 tasks (cloud minus local) | Gap on the 19 committed after June 2026 | Standing |
| --- | --- | --- | --- |
| Claude Opus 5.5 | 64 points (50 to 76) | 84 points (53 to 94) | behind |
| Claude Sonnet 5.5 | 55 points (38 to 69) | 79 points (49 to 91) | behind |
| Claude Haiku 5.5 | 64 points (50 to 76) | 84 points (53 to 94) | behind |

Local is behind every tier, and the cheapest tier leads by as much as the
most expensive one. The laptop ran under aider while the cloud ran under
Claude Code, so part of each gap belongs to the agent. A run of Claude Code
driving qwen3.5 on the laptop will replace this row's local side, and the gap
that remains will then belong to the model.

The test also shows how much evidence a row needs. On these 47 tasks Sonnet
trails Opus by 8.5 points (0.3 to 19.9), which straddles the margin, so the
test can't call even two cloud tiers level. In a simulation, a local model
that is truly level and disagrees with the cloud on one task in ten reads
"caught up" 43% of the time on 47 tasks and 94% of the time on 150. The
coding row therefore grows toward 150 tasks. Two more conditions guard a
crossing once one appears. The tasks committed after the cloud model's
training cutoff can't read "behind" on their own, and the crossing has to
hold after the next batch of newly mined tasks is added. The map tests every
tier again each month, and enough repeated tests would eventually cross by
chance.

### The same agent on both sides

The cloud ran under Claude Code and the laptop under aider, so the gaps above
measure the agent as well as the model. Claude Code can find a repository's
tests and run them through its shell even in the blind condition, which aider
can't. To hold the agent fixed, Claude Code 2.1.318 drove qwen3.5 through
Ollama on 2026-11-07 and 2026-11-08, with the cloud run's flags, prompt, empty
home directory and sandbox, on the Mac Studio. The model was the laptop's own
file at the same 131k context. The Studio stands in for the laptop's answers
rather than its speed, since under aider the two machines solved 17 and 18 of
these 47 (section 5).

| Agent and model, same 47 tasks | Resolved | Median attempt |
| --- | --- | --- |
| Claude Code, qwen3.5 at 131k (the Studio) | 34 of 47, 72% [58%–83%] | about 5 minutes |
| aider, qwen3.5 at 131k (the Studio) | 18 of 47 | |
| aider, qwen3.5 at 131k (the laptop) | 17 of 47, 36% [24%–50%] | 28 minutes |

The agent moved the result more than any setting this study has measured,
since the wider context added about 20 points (section 3). On the same
machine with the same model file, Claude Code solved 17 tasks that aider
missed and aider solved 1 that Claude Code missed (exact McNemar test, p =
0.0001). By repository, local solved 26 of 28 more-itertools tasks and 8 of 19
attrs tasks, so the small, self-contained functions came close while the class
machinery did not.

| Tier | Cloud's lead on all 47 tasks | Standing | Lead on the 19 committed after June 2026 | Standing |
| --- | --- | --- | --- | --- |
| Claude Opus 5.5 | 28 points (17 to 42) | behind | 11 points (−8 to 31) | open |
| Claude Sonnet 5.5 | 19 points (5 to 34) | open | 5 points (−12 to 25) | open |
| Claude Haiku 5.5 | 28 points (17 to 42) | behind | 11 points (−8 to 31) | open |

Holding the agent fixed cuts the cloud's lead by more than half, from 64
points to 28 against Opus and Haiku. Against Sonnet the lead of 19 points is
not settled, which is the first standing on the map that isn't "behind". An
open standing is not a crossing. Its interval still reaches 34 points, and
only more tasks can narrow it. On the tasks committed after June 2026, local
solved 17 of 19, so the after-cutoff slice does not read "behind" against any
tier. That slice is mostly more-itertools, the repository local does best on,
so it isn't evidence that local does better on newer work.

Two parts of the run differ from the cloud's. Thirty-one cells ran at commit
`4ec7beb` with Claude Code's output capped at 8192 tokens a reply, and that
cap crashed 9 attempts on the first night. The other 16 cells ran at
`0fa2d94` (#100), which gives a local model the CLI's own cap of 32,000. They
are the cells left over from the first night and every cell the 8192 cap had
touched, so no kept attempt ran into the old cap. The laptop's own run under
Claude Code is still to come. Its first night finished 3 cells and solved 1,
with the machine short of memory at a 131k context, and the run stopped
before a second night. It waits on #100.

## 5. The hardware question

Vendor numbers say the step from 16 GB to 24 GB is the only one that changes
model class ([Era 32](../../brain/state/findings.md)), since the best SWE-bench
Verified score that fits rises from 47.67 to 70.9. The Studio run is the first
measurement of that step on real repositories. At first it seemed to confirm
the step, since the 35B model that needs about 21 GB resolved three times as
many of the 92 outside tasks as the 9.7B model that fits the laptop (31
against 9). That comparison also changed the context window from 32k to 131k.
Given the same 131k window, the 9.7B model resolves 28 of the 92, which means
the context accounts for 19 of the 22 tasks between the two models.

Speed does not explain the gap either. The laptop's model on the Studio runs
at about 79 tokens a second instead of 14.5, and its median attempt drops from
8.3 minutes to 1.4. Its resolve rate on Mahoraga stays within the
laptop's interval (19% against 28%), so faster hardware gave the same model
the same answers sooner.

The context window explains it. Most of qwen3.5's failures at 32k were
attempts that made no edit at all, and more-itertools keeps 5,641 lines in one
file. At a 32k context that file overflows the prompt, and Ollama truncates it
without an error, so the model never sees the code it was asked to change. At
131k most of those empty attempts became edits. Task by task, the two models
at 131k disagree on 21 of the 92 tasks, and each wins about half of them (9 for
qwen3.5 and 12 for qwen3.6). A split that close to even is what chance alone would
produce (an exact McNemar test gives p = 0.66), so these tasks show no
difference between the models.

The setting fits the laptop. Under Ollama on the 16 GB MacBook, qwen3.5 takes
10.4 GB at a 131k context against 6.1 GB at 32k, and its generation speed on a
short prompt barely moves (17.6 against 18.7 tokens a second, measured
2026-10-14). The model is a hybrid that keeps a full attention cache in only 8
of its 32 layers, which is why the wider window costs about 4 GB rather than
the 16 a plain transformer of its size would need.

Fitting in memory is not the same as running well, which is why the laptop
needs its own measurement. About 1.8 GB of the cache sits off the GPU at 131k,
and the laptop reads a long prompt at about 140 tokens a second (measured
2026-10-15). A single more-itertools request of 100,000 tokens therefore keeps
it busy for about twelve minutes before the model writes a word. The laptop's
first two tries at this measurement were set by clocks rather than by the
model (section 6), so the run below gives each attempt 90 minutes and puts no
shorter clock inside it.

On the laptop itself, qwen3.5 at 131k resolved 17 of the 47 tasks from
more-itertools and attrs. The Studio, running the same model file at the same
131k context, resolved 18 of the same 47. The two machines disagree on 9
tasks, 4 that only the laptop solved and 5 that only the Studio solved, and a
split that even is what chance alone would produce (exact McNemar p = 1.00).
Before the request-timeout fix the same comparison read 8 against 14 (section
6), and with the clocks removed that gap is gone. The laptop's median attempt
took 28 minutes, only 2 of its 47 attempts used up the 90 minutes, and none of
its model requests failed.

The run also needs the machine to itself. On its second night it loaded the
model while the laptop was in heavy use, memory ran out, and the laptop
crashed and restarted. The run was restarted with a gate that waits until the
laptop has been idle for 15 minutes and has 11 GB free before it loads the
model, and no night after that crashed.

For a 16 GB owner, the first change is the context window, and a memory
upgrade is worth measuring only after it. The larger model still resolves a
few more tasks (3 more of the 92 and 4 more of the 56 commits made after its
release), but both differences sit well inside the intervals.

One 9B model (ornith-1.5) claims 70.6 on SWE-bench Verified at a quarter of
the memory, which would undercut the upgrade if it held. Its figure was
measured with a scaffold trained alongside the model, so it cannot be compared
directly with a plain harness. Under aider its replies came back as tool-call
markup that aider cannot apply (section 6), so it ran under opencode instead,
and opencode lets a model run the tests itself even in the blind condition.
That run needs the same control model under opencode before its numbers mean
anything, and the control had not finished when the Studio was last available,
so ornith has no result in this report.

## 6. What we got wrong along the way

Several numbers in this project were wrong when first written, and each one
was corrected in the repository before this report. Granite 4.1's HumanEval+
figure was first recorded as 80.49, which appears in no primary source, and
the correct value is 79.88. An early summary said the small-model tier had
been "vacated", which overstated a finding that only showed no verified
upgrade. The local model was described as generating 30 tokens a second, but
measured idle on this laptop it reaches about 14.5. All 17 attempts from the
first run of a second agent were voided, because the agent ran in the wrong
directory, and every agent now runs inside an operating-system sandbox. A
src-layout bug silently tested the wrong copy of two repositories, and the
benchmark's own gates rejected every affected task rather than scoring them.

The hardware finding was also wrong at first. After the first Studio runs we
wrote that more memory "bought speed, not solve rate", which compared the 35B
model on one set of tasks with the laptop's model on another. Running both
models on the same tasks reversed it within a day. The reversal itself then
overstated the case, because the 35B model ran at a 131k context and the
laptop's model at 32k. We credited the larger model with a gain that mostly
came from the wider window, until a night with qwen3.5 at 131k closed 19 of the
22 tasks between them.

The laptop's own 131k runs went wrong twice before they measured anything.
The first night gave each attempt 20 minutes, and 26 of its 38 attempts ran
out of time, because we had measured the laptop's speed on short prompts and
it reads a long one at about 140 tokens a second. The verdict engine now
refuses to call a stay decided when most of the failures are timeouts, since
a timeout can only lower the rate. A longer limit then exposed a second clock
inside the agent. Aider gives each model request ten minutes unless told
otherwise, and the laptop needs longer than that to read a prompt of more than
about 80,000 tokens, so 27 of the first night's 38 attempts lost a request and
had to send it again. With both clocks in place the laptop seemed to solve far
fewer tasks than the Studio running the same model file (8 against 14 of the
same 28), which read like a hardware result until the logs showed the cut
requests. Mahoraga now gives each request the whole attempt's limit, and the
laptop's numbers in this report come from a rerun with that fix.

The first run of the granite comparison in section 2, on 2026-10-28,
measured our harness instead of the models, in three ways at once. Granite 4.2
reasoned at length before every answer, about 1,400 tokens against roughly 150
that it showed, because its template turns reasoning on unless a request turns
it off and our request never reached the template. A two-minute limit inside
our client then cut 59 of those long replies, each cut also broke the request
after it, and the run's summary still reported every task as a success when
only 46 of granite 4.2's 164 answers had come back. The same run also left the
cascade on, which sent 25 of granite 4.1's tasks to Claude and recorded
Claude's answers under granite's name. We caught all three by grading the
outputs rather than trusting the summary. The rerun in section 2 records for
every answer whether it reasoned, whether it was escalated and whether it
errored, so a claim checks that none of them came from Claude.

Two more errors came from the agents' interfaces rather than from the models.
Ornith-1.5 was first recorded under aider as 3 resolved and 99 attempts with
no edit, but 47 of its replies were tool-call markup that aider cannot apply,
so those attempts measured the interface and were set aside. The first
opencode attempt then ran `git show` on the commit that held the answer,
since each task's clone was built on top of that commit and `git log` showed
it one step back. Every
clone is now rebuilt with nothing of the answer in it, and the one attempt
that saw it was discarded. Aider cannot run shell commands, so no aider result
in this report was affected.

## 7. What the numbers don't settle

The rates above say whether a local agent can do this work today, on these
repositories and these machines. A developer choosing between local and cloud
also weighs what each side will cost next year, who controls the model, where
the code goes and what "resolved" means, and a resolve rate measures none of
those. This section collects what was public on 2026-11-07 and names the part
of the verdict each item would move. The fuller notes, with a confidence tag on
every source, are in [the landscape note](../../brain/research/2026-11-07-landscape.md).

**Cloud prices are falling per token and rising per developer.** Opus 5.5
lists at $4 per million input tokens and $20 per million output tokens,
compared to $5 and $25 for the Opus models before it
([Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)).
Agents spend far more tokens on a task than a chat does, though, and Gartner
described per-developer AI coding bills rising from $20 to $100 a month to
$2,000 to $5,000 under consumption pricing
([The Register, 2026-06-24](https://www.theregister.com/ai-and-ml/2026/06/24/ai-coding-agents-could-soon-cost-more-than-the-developers-using-them/5260864)).
Flat plans moved the same way. GitHub moved every Copilot plan to token-priced
credits on 2026-06-01
([GitHub](https://github.blog/news-insights/company-news/github-copilot-is-moving-to-usage-based-billing/)),
and on 2026-03-26 Anthropic made its five-hour session limits run out faster
during peak hours, from 05:00 to 11:00 Pacific
([The Register](https://www.theregister.com/software/2026/03/26/anthropic-tweaks-claude-usage-limits-to-manage-capacity/5225406)).
The cloud price in section 4 is therefore a measurement with a date on it,
which is why the cloud arm now runs again once a month, by hand.

**A cloud model can change or retire while the user changes nothing.**
Anthropic traced weeks of weaker Claude Code results, between 4 March and 20
April 2026, to three changes in the product around the model (a lower default
reasoning effort, a bug that kept clearing its older reasoning, and an
instruction to cut verbosity), while its API and inference layer were
unaffected ([postmortem, 2026-04-23](https://www.anthropic.com/engineering/april-23-postmortem)).
Its models also retire on a schedule, with at least 60 days' notice, and Opus
5.5 is listed as available until at least 2027-09-22
([model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations)),
so the cloud row in section 4 can only be repeated until then. The laptop's
model file can be run again in ten years, and for a study that measures the
same tasks over time that difference is part of the result.

**Local agents send nothing out, and consumer cloud plans keep more than most
users expect.** Since Anthropic's 2025-08-28 terms, a Free, Pro or Max user who
allows training has their chats and Claude Code sessions kept for five years
instead of 30 days, while API and commercial accounts are excluded
([Anthropic](https://www.anthropic.com/news/updates-to-our-consumer-terms)).
No input to the verdict captures this. A reader whose code can't leave the
machine should read the verdict as the price of keeping it local.

**The hardware got more expensive this year.** Apple raised Mac prices on
2026-06-25 as memory costs climbed, the MacBook Pro from $1,699 to $1,999 and
the M4 Max Mac Studio from $1,999 to $2,499
([MacRumors](https://www.macrumors.com/2026/06/25/apple-just-increased-prices/)),
and on 2026-11-02 it said memory costs would be worse in the December quarter
([MacRumors](https://www.macrumors.com/2026/11/02/apple-memory-costs-increase-december/)).
The verdict charges local its electricity and the developer's time on each
failed attempt, but not the machine, because it assumes the machine is already
owned. A reader who would buy a machine to go local should add its price,
which moves the break-even further toward cloud.

**A resolved task is not the same as a merged change.** METR had four
maintainers of scikit-learn, Sphinx and pytest review 296 AI patches that
passed the tests, and they would have merged about 24 percentage points fewer
than the grader passed
([METR, 2026-03-10](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/)).
The same gap would lower both rows in section 4, so the cloud's 47 and the
laptop's 17 are both upper bounds on work a maintainer would accept. The
verdict's three minutes to triage a failed attempt has no study behind it in
either direction, which is why every verdict prints it and `--triage-min`
changes it.

**The models that fit a laptop trail the frontier further than the headlines
say.** The best open models are about four months behind the closed frontier
on Epoch AI's capabilities index
([Epoch AI](https://epoch.ai/data-insights/open-closed-eci-gap)), but they need
datacenter hardware. Epoch's 2025 estimate put open models that fit one
consumer GPU 6 to 12 months behind
([Epoch AI](https://epoch.ai/data-insights/consumer-gpu-model-gap)), and a
9.7B model on 16 GB is smaller than that class, which fits a gap of 17 tasks
against 47 here. Models from Chinese labs were the largest share of Hugging
Face downloads over the year to February 2026, at 41%
([Hugging Face](https://huggingface.co/blog/huggingface/state-of-os-hf-spring-2026)),
and the qwen models in this report are among them, so the local option also
depends on where open weights keep coming from.

**Other tools mine repositories too, and the agent is part of the result.**
SelfBench, RepoTrials and Hugging Face's Tasksmith turn a repository's merged
changes into private tasks with checks like the ones in section 3, and
SWE-rebench refreshes public tasks the same way
([SWE-rebench](https://swe-rebench.com/)). What this report adds is a verdict
priced for the reader's bill and time, with a local machine measured beside
the cloud on the same tasks. The agent matters as well, since under a short
context, trimming old tool results raised one open model's complete solutions
from 43 to 72 of 169 SWE-bench Verified tasks
([arXiv 2608.26218](https://arxiv.org/abs/2608.26218)). Ollama has accepted
Anthropic's API since January 2026, so Claude Code could drive the laptop's
own model and hold the agent fixed, which Mahoraga's harness doesn't support
yet.

## 8. Run it on your repository

The measurement takes one command, and the answer is ready the next morning.

```bash
mahoraga audit tonight ~/code/your-repo
```

It mines tasks from the repository's own history and checks that the machine
and the agent are ready. It also measures once how fast the machine reads a
long prompt, so each attempt gets a limit this machine can finish inside, and
then it queues the run for the night. The morning file gives the rate, its
interval and the verdict, after asking once how you pay for cloud. Each step
is also its own command for anyone who wants to set it by hand, and on a 16 GB
machine at 131k the hand-set run needs the longer limit.

```bash
mahoraga audit mine    ~/code/your-repo
mahoraga audit doctor  ~/code/your-repo --arm aider:qwen3.5:latest@ctx=131072
mahoraga audit run     ~/code/your-repo --arm aider:qwen3.5:latest@ctx=131072 \
    --cond feedback --timeout 5400 --until 07:30
mahoraga audit verdict ~/code/your-repo --arm aider:qwen3.5:latest@ctx=131072 \
    --tasks-per-month 200 --plan claude-max-5x --usage-of-cap 0.6
```

Nothing leaves the machine during an audit with local agents. The repository
is only read, and agents run in a sandbox that can write only to their own
copy and can't reach past this machine (on Linux, past the local model
server). The cloud arm
of section 4 is the one exception, and it runs only when a run is given
`--allow-cloud`, since it sends what Claude Code reads of the task to
Anthropic ([DATAFLOW.md](../../DATAFLOW.md)).
