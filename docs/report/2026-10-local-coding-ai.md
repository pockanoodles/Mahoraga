# Is local coding AI worth it on a 16 GB Mac? (October 2026)

**Draft.** Every figure here links to the artifact it came from. Sections
marked PENDING wait on runs that are scheduled, and no number goes into them
until the run has finished and `orch bench verify` recomputes it.

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
repositories (10% of 92 tasks) and about one in five on this project's own
code. A 64 GB machine runs a larger class of model that resolves about one in
three (34% of the same 92 tasks), and the repository's own tests caught every
failure on both machines. Neither rate pays at today's cloud prices, because
the developer's time to notice a failed attempt outweighs the electricity
several hundred to one. On the 64 GB machine local starts to pay once a cloud
task costs about $5, which describes a team paying per token for long agentic
work rather than a subscriber.

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

> **PENDING (the run scheduled for 2026-10-02 never started):** the same bank
> and method with granite 4.1 against granite 4.2. IBM published a HumanEval+
> figure for 4.1 (79.88) and none for 4.2, so this comparison will be the only
> protocol-matched one that exists. Our harness reproduces IBM's 4.1 figure
> within its run-to-run band (0.774 to 0.805), which is what makes the 4.2
> number citable.

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
| aider + qwen3.6 (35B-A3B, 131k) | 64 GB Studio | Mahoraga | 32 | 13 | 26% to 58% |
| aider + qwen3.6 (35B-A3B, 131k) | 64 GB Studio | more-itertools, click, attrs | 92 | 31 | 25% to 44% |

Every attempt was blind and run once. The qwen3.5 rows on the Studio use the
laptop's exact model file (checksum verified) with the laptop's settings, so
they measure the laptop's model on faster hardware. The laptop's own rate may
sit slightly below them, because its attempts take 8 to 13 minutes and the
slowest run into the 20-minute timeout. The Studio rows were run overnight on
2026-10-06 ([Era 36](../../brain/state/findings.md)). Every count in this
section and the next is recomputed by `orch bench verify` from one line per
attempt in [`experiments/agentbench/`](../../experiments/agentbench/), and CI
runs that check, so a reader can confirm each number without our machines.

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
On more-itertools, the one repository with enough tasks on both sides of the
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
| qwen3.5 (fits 16 GB) | 10% [5%–18%] | −$109 (−$117 to −$96) | $23.06 | STAY |
| qwen3.6 (needs 24 GB or more) | 34% [25%–44%] | −$70 (−$85 to −$54) | $4.92 | STAY |

Local would need to resolve at least 77% of queued tasks to break even at
$0.75 a task, which is about twice what the larger model manages. Running the
queue on the laptop costs about $109 a month more in triage time than it
saves. The bigger machine cuts that loss to about $70, and it moves the
break-even price from $23 a task to under $5. Earlier, on the ops repository
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
measurement of that step on real repositories, and it points the same way.
On the same 92 outside tasks, the 35B model that needs about 21 GB resolves
three times as many commits as the 9.7B model that fits the laptop (31 against
9).

Speed alone does not explain the gap. The laptop's model on the Studio runs at
about 79 tokens a second instead of 14.5, and its median attempt drops from
8.4 minutes to about one. Its resolve rate on Mahoraga stays within the
laptop's interval (19% against 28%), so faster hardware gave the same model
the same answers sooner. More memory pays through the larger model and the
larger context it makes room for.

Those two effects are still tangled. Most of qwen3.5's failures on the outside
repositories were attempts that made no edit at all, and more-itertools keeps
5,641 lines in one file. At a 32k context that file overflows the prompt, and
Ollama truncates it without an error.

> **PENDING (running 2026-10-13):** qwen3.5 at a 131k context on the same
> three repositories. If its rate rises toward the 35B model's, part of the gap
> belongs to the context window, and a 16 GB owner can recover some of it by
> raising the window rather than buying memory.

One 9B model (ornith-1.5) claims 70.6 on SWE-bench Verified at a quarter of
the memory, which would undercut the upgrade if it held. Its figure was
measured with a scaffold trained alongside the model, so it cannot be compared
directly with a plain harness, and it has not yet been run on these
repositories.

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
models on the same tasks reversed it within a day.

## 7. Run it on your repository

The measurement takes one command to check and one to run overnight.

```bash
mahoraga audit mine    ~/code/your-repo
mahoraga audit doctor  ~/code/your-repo --arm aider:qwen3.5:latest
mahoraga audit run     ~/code/your-repo --arm aider:qwen3.5:latest --cond feedback --until 07:30
mahoraga audit verdict ~/code/your-repo --arm aider:qwen3.5:latest \
    --tasks-per-month 200 --plan claude-max-5x --usage-of-cap 0.6
```

Nothing leaves the machine during an audit. The repository is only read,
agents run in a sandbox that can write only to their own copy, and the only
network they can reach is the local model server.
