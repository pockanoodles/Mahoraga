# Is local coding AI worth it on a 16 GB Mac? (October 2026)

**Draft.** Every figure here links to the artifact it came from. Sections
marked PENDING wait on the laptop's own 131k run for sections 3 to 5, which
runs night by night into early November. No number goes into them until the
run has finished and `orch bench verify` recomputes it.

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
once the context is raised to 131k. Both rates come from the laptop's exact
model file run on a faster machine, and the laptop's own 131k run is still
under way (PENDING, section 5). A 64 GB machine runs a larger model that
resolves about the same share (34% of the same 92 tasks), so most of what
first looked like a hardware gap was a settings gap. The repository's own
tests caught every failure on both machines. Neither rate pays at today's
cloud prices, because the developer's time to notice a failed attempt
outweighs the electricity several hundred to one. At the wider context, local
starts to pay once a cloud task costs about $6 (about $5 with the larger
model), which describes a team paying per token for long agentic work rather
than a subscriber.

## 1. The local tier stopped getting new models

Between June and September 2026 the flagship open model lines moved up and
out of the size that fits consumer hardware ([Era 31](../../brain/state/findings.md)).
Qwen 3.6 and 3.8 ship no model under 17 GB, the smallest `qwen3-coder` is
19 GB, and Meta's line now starts at 17.31 GB. Smaller releases did continue
(IBM's granite 4.2 8B and a 9B from ornith-ai), but none of them has a
verified advantage in writing code. This matters for the thesis, since a
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
| aider + qwen3.6 (35B-A3B, 131k) | 64 GB Studio | Mahoraga | 32 | 13 | 26% to 58% |
| aider + qwen3.6 (35B-A3B, 131k) | 64 GB Studio | more-itertools, click, attrs | 92 | 31 | 25% to 44% |

Every attempt was blind and run once. The qwen3.5 rows on the Studio use the
laptop's exact model file (checksum verified) with the laptop's settings, so
they measure the laptop's model on faster hardware. The laptop's own run at
131k is the row this table still lacks (PENDING, section 5), since the laptop
reads a 100,000-token prompt for over ten minutes and each attempt there needs
a limit of 90 minutes rather than 20. The Studio rows were run overnight on
2026-10-06 ([Era 36](../../brain/state/findings.md)), except qwen3.5 at 131k,
which ran on the night of 2026-10-13 with the same model file. Every count in this
section and the next is recomputed by `orch bench verify` from one line per
attempt in [`experiments/agentbench/`](../../experiments/agentbench/), and CI
runs that check, so a reader can confirm each number without our machines.

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

| Model (92 outside tasks) | Resolved | Net per month | Local pays once a cloud task costs | Verdict |
| --- | --- | --- | --- | --- |
| qwen3.5 at 32k (fits 16 GB, run on the Studio) | 10% [5%–18%] | −$109 (−$117 to −$96) | $23.06 | STAY |
| qwen3.5 at 131k (fits 16 GB, run on the Studio) | 30% [22%–40%] | −$76 (−$89 to −$59) | $5.72 | STAY |
| qwen3.6 at 131k (needs 24 GB or more) | 34% [25%–44%] | −$70 (−$85 to −$54) | $4.92 | STAY |

> **PENDING (section 5):** the same verdict on the laptop's own 131k run, on
> the 47 outside tasks it attempts.

Local would need to resolve at least 77% of queued tasks to break even at
$0.75 a task, which is about twice what either model manages at 131k. At a
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

> **PENDING (running from 2026-10-28 into early November):** qwen3.5 at 131k on the
> laptop itself, blind, on 47 tasks from more-itertools and attrs, compared
> task by task with the Studio's run of the same model file.

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

## 7. Run it on your repository

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

Nothing leaves the machine during an audit. The repository is only read,
agents run in a sandbox that can write only to their own copy, and the only
network they can reach is the local model server.
