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
repositories, and the tool behind it runs the same measurement on yours.

## The short answer

For most people the answer is to keep paying for cloud and send a small slice
of patient work to local. A local agent on this laptop lands between one in
nine and one in four real edits on the codebases measured so far, and the
repository's own tests caught every failure. That rate pays only when a cloud
task is expensive. The real cost of a failed local attempt is the developer's
time to notice it, which outweighs the electricity several hundred to one.

## 1. The local tier stopped getting new models

Between June and September 2026 the flagship open model lines moved up and
out of the size that fits consumer hardware ([Era 31](../brain/state/findings.md)).
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

> **PENDING (run scheduled 2026-10-02):** the same bank and method with
> granite 4.1 against granite 4.2. IBM published a HumanEval+ figure for 4.1
> (79.88) and none for 4.2, so this comparison will be the only
> protocol-matched one that exists. Our harness reproduces IBM's 4.1 figure
> within its run-to-run band (0.774 to 0.805), which is what makes the 4.2
> number citable.

## 3. Local agents on real repositories

A benchmark built from a repository's own history asks a harder question than
a synthetic one. Each task is a real commit with its source change removed and
its tests kept, and an attempt counts only if the commit's tests pass and
nothing that passed before has broken.

| Agent and model | Repo | Tasks | Resolved | 95% interval (by task) |
| --- | --- | --- | --- | --- |
| aider + qwen3.5 (9.7B) | Mahoraga | 18 | 5 | PENDING recompute |
| aider + granite 4.1 (8B) | Mahoraga | 18 | 0 to 1 | PENDING recompute |
| aider + qwen3.5 (9.7B) | ops | 9 | 1 | 2% to 43% |
| aider + qwen3.5 (9.7B) | more-itertools, click, attrs | 73 | PENDING | PENDING |

Three things held across every run. The test gate caught every failed
attempt, so a test-gated queue never shipped a broken edit. The commonest
failure was an edit that fixed the target and broke neighbouring code, which
only the full-suite check sees. Rankings also flipped between interfaces. Granite was the best of our models
at answering prompts and the worst at editing files, since 27 of its 36
attempts targeted code that was not in the file.

The intervals count tasks, not attempts. Repeating a task tells you how stable
the model is on that task, but it adds no new evidence about tasks in general.
Nine tasks therefore leave an interval about 40 points wide however many times
they are rerun, which is why this report pools five repositories.

## 4. When local pays

The verdict engine (`orch agentbench verdict`) values each queued task as the
chance local solves it times the cloud cost it saves, minus electricity and
minus the developer's time to triage a failure. On the ops repository, at
$0.75 per cloud task, local would need to solve at least 77% of queued tasks
to break even, and it measured 11%. At that rate local pays only once a cloud
task costs about $20, which suggests the realistic customer is a team paying
per token for long agentic tasks rather than a subscriber.

A flat subscription changes the arithmetic, since an extra cloud task costs
nothing. Local can then only help by moving someone to a cheaper tier or by
freeing room under a usage cap they keep hitting. Take a Max 5x user at 60% of
the cap. Dropping to Pro would need local to absorb more work than an
overnight queue can hold, so the engine answers that this user is not wasting
money.

> **PENDING:** the pooled verdict across all five repositories, with the
> sensitivity lines for price, resolve rate, and hardware.

## 5. The hardware question

The step from 16 GB to 24 GB is the only one that changes model class,
according to vendor numbers ([Era 32](../brain/state/findings.md)). The best
SWE-bench Verified score that fits rises from 47.67 to 70.9 for the cost of a
used RTX 3090. One 9B model (ornith-1.5) claims 70.6 at a quarter of the
memory, which would make that upgrade far less valuable if it held. Its figure
was measured with a scaffold trained alongside the model, so it cannot be
compared directly with a plain harness.

> **PENDING:** ornith-1.5 on the same agent harness and repositories as the
> other models. This is the measurement that decides whether a 24 GB machine is
> worth buying.

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

## 7. Run it on your repository

The measurement takes one command to check and one to run overnight.

```bash
orch agentbench mine   ~/code/your-repo
orch agentbench doctor ~/code/your-repo --arm aider:qwen3.5:latest
orch agentbench run    ~/code/your-repo --arm aider:qwen3.5:latest --cond feedback --until 07:30
orch agentbench verdict ~/code/your-repo --arm aider:qwen3.5:latest \
    --tasks-per-month 200 --plan claude-max-5x --usage-of-cap 0.6
```

Nothing leaves the machine. The repository is only read, agents run in a
sandbox that can write only to their own copy, and the only network they can
reach is the local model server.
