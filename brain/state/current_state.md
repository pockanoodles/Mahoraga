# Current State — 2026-08-05

## Read this first — 2026-08-05 (latest): reward fidelity SHIPPED — the reward is fixed, and that exonerates it

**Era 20's prescription, built and validated (PRs #34 + #35, both merged).**
The judge verdict is now the correctness coefficient on the reward's success
term (`TaskOutcome.correctness`, `w_s * c`); exec gate stays the hard floor;
`None` ≡ legacy bit-for-bit; flags `MAHORAGA_REWARD_JUDGE=off|on|code` (default
on) + `MAHORAGA_REWARD_JUDGE_MODEL`. Decision log gains
correctness/judge_cost/judge_detail; the OLS w_s regressor is finally
identifiable. Offline validation: `orch bench report reward-judge` (zero
inference — recorded P1 cross + real RewardCalculator + fresh LinUCB × 20
orderings).

**Result:** the reward now measures correctness (reward↔pass r 0.119 → 0.98+;
the 50-bank arm reward leader flips from the faster arm to the *better* arm)
— but it does NOT convert into a pass@1 win: oracle-reward LinUCB 0.767 vs
round-robin 0.771. Arm-level gaps (0.004–0.024 reward) are below cold-start
LinUCB's resolution at 50–164 pulls; judge noise transmits only ~0.57–0.64 of
them. **The Era-20 null now has a clean interpretation: not a broken ruler —
the arms aren't separable as arms. The +11.6-pt win is per-prompt → semantic
routing (A1) is the remaining lever, reward ruled out as confounder.** Still
no honest "bandit beats X" resume number; the honest sentence is "reward
measures execution-verified correctness." Detail: findings Era 23 +
`brain/journal/2026-08-05-reward-fidelity.md`.

Also fixed: latent NameError in `code_judge_cmd` fresh checks (masked by warm
cache in Era 22 — would have crashed any new-cache sweep); OverflowError on
factorial-class ints in `code_judge.values_equal` + crash-net + per-case
flush in live-route (PR #33; killed live confirmation attempt #1 at 83/164).
Ops lesson: `nohup` does not survive lid-close sleep — long benches need
`caffeinate -i -w <PID>` + AC power + lid open.

**Next:** (1) live confirmation run attempt #2 (running overnight 2026-08-05)
→ headline decision; (2) K=5 case-coverage sweep (`--gen-samples 5`, needs its
own `--cache` file — the cache key ignores K); (3) A1 semantic routing.

## Read this first — 2026-08-04 (earlier): the code-mode tool-judge — recall 0.688 → 0.781

**Era 19's queued lever is built and measured.** `routing/code_judge.py`: the
judge writes K=3 reference implementations + test inputs from the prompt alone
(never sees candidate or hidden tests), everything executes in the sandbox,
expected output = executed reference consensus, deterministic compare,
recall-only (structurally can only reject/abstain). Replayed offline over the
recorded P0 run — exact counterfactual, free, because the P0 run recorded every
cloud baseline.

**Result (164 HumanEval+, k=3, min_disagree=2):** fail-recall **22/32 → 25/32**,
wrong answers served **10 → 7**, over-escalations 15 → 19 (all added ones on
cloud-pass rows: money, not quality), **routed 0.921 → 0.939 @ $10.04/1k** vs
cloud 0.976 @ $35.97 — **96.2% of cloud quality at a 72.1% cost cut** (was
94.4% / 76.5%). Detail: findings Era 22 + `brain/journal/2026-08-04-code-judge.md`.

**Two structural findings:** (1) tool-judge recall is bounded by the judge
model's own solve rate (qwen3.5 solves 5/10 of the misses; catches only came
from the solvable side) — stronger reference-writer = the ≥32 GB lever;
(2) one disagreeing generated input is noise, two are signal
(`MIN_DISAGREEMENTS=2` dropped 6/9 false alarms incl. the only quality-losing
one, kept all 3 catches) — threshold chosen post-hoc, needs live confirmation.

**DO NOT cite 0.939 as the headline yet** — it's a counterfactual replay on the
run the threshold was calibrated on. The honest sequence: run
`orch bench repro --code-judge` (fresh ~5 h live run) and cite THAT number.
Until then the citable headline stays Era 19's (0.921 / 76.5% / recall 0.688).

**Shipped (branch `feat/code-judge`, PR pending):** `routing/code_judge.py`,
`orch bench report code-judge` (offline replay, `::code` cache slot, free
`--min-disagree` sweeps), `--code-judge` on `bench live-route` + `bench repro`,
`RoutedCase.judge_detail`. 33 tests.

**Next:** (1) live confirmation run → new README/resume headline; (2) reward-
fidelity fix (judge verdict as bandit success term, Era 20) — now fed by a
better judge; (3) case-generation coverage (K=5, boundary sweeps) as the cheap
recall lever at 16 GB.

## Read this first — 2026-08-03 (earlier): P1 — the routing A/B (bandit vs baselines)

**The "learns" claim got its experiment and the answer is a diagnosed null.**
Per bank: force-explore cross (round-robin/statics/per-prompt oracle derived
exactly) + cold-start LinUCB through the real `/api/task` path, isolated scratch
`HOME` per policy, execution-verified pass@1 only. **Bandit never beat
round-robin** (HumanEval+: 0.744 vs 0.771, statics 0.768/0.774, oracle 0.890;
50-bank: 0.920 vs 0.940, statics 0.960/0.920, oracle 0.980). Coin-flip (42%) on
arm-discriminating prompts; no learning curve.

**Why (from the decisions DB, the actual finding):** the reward's success term
= execution-gate "ran without crashing" → saturated at 1.000/0.987 while true
pass@1 was 0.774/0.768 → only latency had gradient → the bandit correctly
chased the faster arm (granite) on both banks, even where it was the worse arm.
**Reward saturation, not learner failure — Era 10 one level up.** Also: Phase-4
arm ranking flipped in 8 days (granite 0.900>qwen 0.818 → qwen 0.960>granite
0.920, same bank) — static assignment rots; online routing needs a
correctness-faithful reward (the judge is exactly that signal). And the oracle
gap (+11.6 pts over best static, arms complementary 19/20) is the quantified
motivation for semantic routing. Detail: findings Era 20 +
`brain/journal/2026-08-03-p1-bandit-ab.md`.

**Resume consequence:** learning line stays architectural (no honest
bandit-beats-X number); the cascade (Era 19) + eval harness carry the numbers.
**Next:** P3 one-command repro + CI badge; then reward-fidelity fix (judge
verdict as success term) and re-run this A/B with the now-standing protocol.

## Read this first — 2026-08-03 (earlier): P0 — the cascade on HumanEval+ (164 external tasks)

**The resume-push bench: the identical live cascade re-run on an external
benchmark, killing the "self-authored 50 tasks" vulnerability.** HumanEval+
(EvalPlus v0.1.10, all 164 problems) converted offline into the verifiable-bank
schema (`experiments/build_humaneval_bank.py` — contract-filtered inputs, oracle
outputs from the canonical solution, atol-aware compare, references verified
through the real `run_case` path; bank + refs + tests in 93d05f7 on
`feat/humaneval-bench`). Full detail:
`brain/journal/2026-08-03-humaneval-plus-cascade.md` + findings Era 19.

**Result (LIVE, n=164, ~3.4 h wall):** always-cloud (claude-cli/sonnet) **0.976**
@ $35.97/1k · always-local (granite) **0.805** @ $0 · **routed cascade 0.921 @
$8.47/1k — 76.5% cost cut, 94.4% of cloud quality, +11.6 pts over local-only.**
Judge (qwen3.5, prompt+output only): accuracy 0.848, **fail-recall 0.688**
(22/32; 5c's 6/6 did NOT generalize), 10 wrong answers served, 15
over-escalations, 37 escalations (22.6%). Tier-monotone; cloud itself lost 4
baseline tasks + 3 of 37 escalations — not a 1.000 oracle here.

**The Era-18 lesson reappeared in code form:** judgment-by-reading saturates
exactly where failures get subtle (plausible, compiling, subtly-wrong granite
code). Next lever on recall: a **code-mode tool-judge** — the judge generates
its *own* tests (never sees the bank's hidden ones) and executes the candidate
in the `execution_gate` sandbox; each converted miss is fp→escalation, money not
quality. Queued behind P1.

**Headline (resume/README, cite this not 5c):** cut LLM inference cost 76%
retaining 94% of cloud pass@1 on HumanEval+ (164), execution-verified, $8.47 vs
$35.97 per 1k tasks.

**Now running / next:** P1 bandit-vs-baselines A/B on the 164 bank (isolated
`HOME` per policy, pass@1 the only cross-policy metric, winnability precheck
from the Phase-4 cross first), then P3 one-command repro + CI badge.

## Read this first — 2026-07-27 (later): Phase 5d — the TOOL-augmented judge

**Era 17 said the quantity/omission blind spot is structural (no ensemble closes
it) → build a tool. Done.** `routing/tool_judge.py` — a compute-check: the judge
model emits a self-contained Python solver, it runs in the `execution_gate`
sandbox, and the executed number is checked against the candidate. Override is
**recall-only** (accept→reject only; never softens a reject; abstains otherwise)
to protect ref-accept. Full detail: `brain/journal/2026-07-27-phase5d-tool-judge.md`
+ findings Era 18.

**Result (v3, full 30-row bank, `orch bench report judge-bank --tool`, local/free):**
accuracy 0.867 → **0.900**, mutant-catch 0.733 → **0.800**, wrong-quantity 1/5 →
**2/5**, **ref-accept 1.000** this run. Caught the *computable* errors the plain
judge AND the granite ensemble missed (two-red-marbles: 4/8 not 4/7; pipes-tank:
dropped the drain); the 3 *factual-lookup* quantities correctly still escalate.

**The build took 3 live iterations — the lesson:** the bottleneck walked down the
chain. v1 (LLM "do they agree?" call) broke ref-accept (pedantic: rejected exact
0.357 for "lacks precision"); v2 (LLM "extract the number") broke on the same row
(misread 0.357→0.3); **v3 removed the LLM from BOTH sides** — solver
**self-consistency** (≥2 of 5 runs agree, else abstain) + deterministic **last-K**
number compare (an intermediate "3/12" spuriously contains a final "3", so
all-number membership under-catches). Every LLM in the compare gap reintroduced
the judgment noise the tool exists to remove.

**Caveat + reframe:** the focused run showed the solver can be *consistently
wrong* (pipes-tank → −12 passed consensus, false-rejecting a correct ref) —
self-consistency stops *random* flakiness, not a *systematically* buggy 8B solver.
**Reframe (5c economics):** in the live gate a false-reject is an **over-escalation
(money, zero quality loss)**, not a wrong answer served — so the tool nets +2 real
catches for a rare needless escalation, a win. **Solver correctness is the limiter,
not the compare design.**

**Shipped:** `routing/tool_judge.py`, `judge_gate.run_text()` (factored,
behavior-preserving), `orch bench report judge-bank --tool` (opt-in,
local-egress-only, own cache slot). 16 tests; suite **1481 green**. (Since merged
to `main` via PR #27, 2026-08-01.)

**Next:** (1) corroborate the solver before overriding (2nd framing / stronger
model) OR make disagreement an escalate-signal not a hard reject; (2) a live
non-verifiable cascade (5c-style) with the tool-judge as gate; (3) the omission
blind spot (subtle-omission still 0.5) → a coverage/checklist tool; (4) productize
`route_one` into `executor.py`.

## Read this first — 2026-07-27 (earlier): Phase 5d deconfound — a SECOND local judge

**Broke the single-judge confound in Era 15.** That profile ("catches stated
falsehoods, blind to quantity/omission, never false-rejects") came from one judge
(qwen3.5) — couldn't tell if it described the *task* or the *model*. Re-judged the
identical 30-row bank with `granite4.1:8b` (IBM lineage, distinct from Qwen, on
disk, free) — pure `--judge-model` swap, no code change, ~5 min, $0. Full detail:
`brain/journal/2026-07-27-phase5d-second-judge.md` + findings Era 16.

granite: **accuracy 0.750, ref-accept 1.000, mutant-catch 0.500** vs qwen3.5's
0.867 / 1.000 / 0.733. **The deconfound split Era 15 in two:**
- **Structural (reproduced across families):** `ref-accept = 1.000` on both →
  both **under-escalate** on prose; **quantity blindness = 1/5 on both, exact.**
  → the **tool-augmented judge** (calc/coverage) for quantity/completeness is now
  *confirmed necessary*, not optional.
- **Model-specific (diverged):** Era-15's "stated falsehoods 17/17" is a
  **qwen3.5 property** — granite catches only ~9/16 there and is the weaker judge
  overall (0.50 vs 0.73). → "trust the local judge for falsehoods" must name its
  judge; **qwen3.5 stays the best single local judge, granite is not a swap-in.**
- **Open — ensemble:** granite is weaker overall, but if its catches cover any of
  qwen3.5's *misses*, a "both-accept-else-escalate" gate could raise recall. Needs
  a case-level overlap join (not done). Caveat: n=30, per-defect cells 1–5, so
  divergence is directional; the two exact matches are the strong evidence.

**Ensemble join DONE (Era 17, 2026-07-27):** ran the overlap. **Answer: no
ensemble.** Union "both-accept-else-escalate" = 0.767 catch vs qwen3.5 alone 0.733
(ref-accept stays 1.0 → free but pointless). granite covers exactly **1** qwen3.5
miss; qwen3.5 covers **8** granite misses — granite is ≈ a strict subset (14/15).
**qwen3.5 is *the* sole local judge.** The 7 mutants neither family catches = 4
quantity + 2 omission + 1 reasoning → the quantity/omission blind spot is
**structural across families**, so the **tool-augmented judge is the only remaining
lever** (calc for quantity, coverage-check for omission), now proven not asserted.
Detail: findings Era 17 + `brain/journal/2026-07-27-phase5d-ensemble-overlap.md`.

**Next:** (1) **tool-augmented judge** — the confirmed next build (calculator +
coverage/checklist check; re-measure the 4 quantity + 2 omission residuals);
(2) bigger non-verifiable bank to firm exact rates (n=30); (3) live non-verifiable
cascade (5c-style); (4) productize `route_one` into `executor.py`.

## Read this first — 2026-07-26 (latest): Phase 5d — the judge with NO oracle

**The judge's real proving ground: non-verifiable tasks.** 5a–5c all lived on code, where hidden tests are the oracle. 5d asks whether a free local judge holds where there's none (explain/factual/reason/summarize/instruct). Built a 30-row bank (6/bucket, tier-skew 5/10/15) with ground truth **by construction** — each row a hand-authored correct `reference` + a subtly-flawed `mutant` (one labeled defect, 14 types), mutant length-matched to the reference so the judge can't win on length. Labels hardened by subagent-draft → my curation → an independent **adversarial blind audit** (agreed 29/30; the 1 borderline rewritten). Full detail: `brain/journal/2026-07-26-phase5d-nonverifiable-judge.md` + findings Era 15.

- Free local qwen3.5 judge: **accuracy 0.867, ref-accept 1.000, mutant-catch 0.733.**
- **The finding — catch rate splits by error kind:** errors of **commission** (states a falsehood / contradicts source) = **17/17 caught (1.00)**; **quantity** (wrong number) = 1/5; **omission/partial** (drops a required part) = 0/3; flawed-reasoning 3/4.
- **Never falsely rejects a correct answer** (ref-accept 1.0) → as a gate it **under**-escalates (opposite of 5c's over-escalating code judge; qwen3.5 is permissive on prose).
- **Routing implication:** trust a local judge to gate stated-falsehood failures; for quantity- or completeness-critical tasks, escalate by default or add a tool (calculator / retrieval / coverage check).

**Shipped (branch `feat/nonverifiable-bank`, PR pending):** `experiments/prompts_nonverifiable.jsonl` (+`_refs`, force-added past the `experiments/` gitignore), `routing/nonverifiable_bank.py` (loader + pure `score()`), `judge_gate.GENERAL_RUBRIC` + `rubric=` param (code rubric stays default), `orch bench report judge-bank` (`--judge-egress local`, free). 10 tests (CI guard + scorer); suite 1465 green.

**Next:** bigger bank + a 2nd local model to tighten the exact rates; a live non-verifiable cascade (5c-style) on the accepted discrimination profile; a tool-augmented judge to attack the quantity blind spot.

## Read this first — 2026-07-26 (earlier): Phase 5c — the cascade run LIVE

**Thesis A proven end-to-end on fresh inference.** 5a/5b were replays of the
run-19 matrix; 5c runs the whole cascade live via `orch bench live-route`: each
gold prompt goes granite → free local qwen3.5 judge → `claude-cli` on a fail
verdict, every served answer graded against hidden tests, nothing read from disk.
Full detail: `brain/journal/2026-07-26-phase5c-live-route.md` + findings Era 14.

- **routed granite→judge→cloud = 1.000 pass@1 (50/50) at $10.54/1k vs
  always-cloud $47.66/1k = 77.9% cost cut.** Live cross-check matched the
  simulator's routed line exactly.
- **Free local judge: accuracy 0.920, fail-recall 6/6 = 1.000** — caught every
  real granite failure (fp=0, served no wrong answer), over-escalated 4 correct
  answers (fn=4). 10 escalations.
- **5c vs 5b:** 5b replay = 0.960 @ $6.10/1k (recall 3/5, under-escalated); 5c
  live = 1.000 @ $10.54/1k (recall 6/6, over-escalated 4). Live judge is more
  **conservative** — verification tax shows up as **money (~$0.19), not quality**.
  100% pass@1 at 22% of always-cloud's cost. always-cloud $0.0477/task ≈ Phase-4's
  $0.0491 (cost capture stable).

**Shipped (branch `feat/live-route-5c`, PR pending):** `routing/live_route.py`
(`route_one` live cascade + grading, `to_matrix` folds into `route_sim.simulate`
so 5b's aggregation runs unchanged on live data, `load_arms` from agents.yaml);
`orch bench live-route` (vanished-models preflight, honest full baseline default /
`--escalate-only`, per-case JSONL, live cross-check). 9 tests; suite 1455 green.
Experiment spend $2.38 cloud (50 baseline calls); routed policy alone = $0.53.
`route_one` is the exact primitive an `executor.py` serving-path productization
would call.

**Also recovered:** PR #23 (5b judge-gate) was a phantom merge — showed "merged"
but landed on the orphaned #22 branch, never on `main`. Restored via PR #24
(merged). `main` now has `judge_gate.py`. Lesson: retarget a stacked child PR's
base to `main` before merging the parent, or merge child-first.

**Next:** harder/larger bank (6 failures/50 still small) · a **non-verifiable**
bank (the judge's real, untested job) · productize `route_one` into `executor.py`
so the gate is a live `/api/task` feature, not only a bench command.

## Read this first — 2026-07-26 (late night): Phase 4 RAN — first head-to-head

**Q5 answered.** `bench_run_id=19`, force-explore, 3 local arms + `claude-cli`
(Sonnet 4.6, Max sub) × 50-row verifiable bank × repeats=1 = 200 tasks, memory
off, ~35 min. Full detail: `brain/journal/2026-07-26-phase4-head-to-head.md`
and findings.md Era 11.

- **pass@1:** claude-cli **1.000** (50/50) · granite4.1-8b **0.900** (45/50) ·
  qwen3-14b **0.880** (44/50) · qwen3.5 **0.818** (36/44).
- **Cost:** claude-cli measured **$0.0491/task** ($2.4526/50, cache-dominated);
  local $0. Best local arm (granite) retains **90% of Claude's verified pass@1
  at $0**.
- **DO NOT quote the cost report's 9.9% headline** — it's the documented floor
  (bare-token pricing of counterfactual local rows, ~27× under the measured
  cloud rate). Honest denominator = measured $0.0491/task.
- **Heuristic inversion replicated** (rho=0.2; perfect arm ranks 3/4 by
  heuristic) — Era 9 holds against a 100%-correct arm.

**DONE (same session):** Kaito's call — **qwen3-14b dropped**, roster is now
the lean 2 local arms (granite + qwen3.5). Disabled in agents.yaml
(`enabled: false`, bandit history preserved). On Phase 4 the 9.3 GB arm was
mid-pack (0.880), beaten by the 5.3 GB granite — no correctness edge for ~2× RAM.

**DONE (same session): the qwen3.5 infra flake is FIXED.** Root cause was
Ollama cold-load transients (`HTTP 5xx` while loading / `ReadError` on a model
swap) surfacing with empty output — 6/50 qwen3.5 tasks at the first task after a
swap. Fix in `workers/ollama.py`: the buffered request now retries transient
failures (HTTP 5xx, ReadError/ReadTimeout/RemoteProtocolError) up to 2× with
2s→4s backoff; 4xx and ConnectError still fail fast. Safe because nothing is
yielded until the stream completes. 5 regression tests; 1430 pass. Benefits live
traffic too (idle-eviction cold loads), not just benches.

**Caveats on the Phase 4 numbers:** force-explore ≠ routing (the "74.9% local"
is just 3/4 arms local, not a learned fraction; escalation economics are
projections). n=50/repeats=1.

**State on exit:** `claude-cli` reverted to `enabled: false`; qwen3-14b disabled;
ollama retry shipped + tested; manual `orch serve` stopped; daemon left stopped
(`orch service start` to restore live routing). **Uncommitted on `main`** — needs
a feat branch + PR. Next: LLM-judge validation (now cheap vs this run's 50-row
ground truth) · Q6 re-run on the fixed ruler · optional repeats=2 to tighten
local-arm error bars.

## Read this first — 2026-07-26 (evening): bank 18→50, roster restored

1. **PR #19 (cost accounting) merged to main.**
2. **Verifiable bank expanded 18 → 50 rows** (`feat/verifiable-bank-50`) —
   closes open thread (b). Medium/hard-skewed, precise-spec prompts (not
   memorized classics); every row has a committed reference + failing mutant,
   CI-enforced by `tests/orchestrator_v2/test_verifiable_bank.py` (106 tests)
   against `experiments/prompts_verifiable_refs.jsonl`. 1426 tests green.
3. **Roster models had vanished from Ollama** — only qwen3:14b remained;
   `qwen3.5:latest` + `granite4.1:8b` re-pulled 2026-07-26 (~12 GB, disk has
   headroom). Check `ollama list` before any bench run; the daemon is
   currently stopped (fine — Phase 4 uses manual `orch serve`).
4. **Phase 4 is now unblocked end-to-end**: merged cost accounting + 50-row
   bank + whole roster. Runbook below (2026-07-26 morning section) — use the
   50-row bank; expect ~400 tasks at --repeats 2 across 4 arms.

## Read this first — 2026-07-26 (morning)

**Cost accounting shipped** (`feat/cost-accounting`, PR #19, 1320 tests green). The dormant cost plumbing is now live end-to-end, unblocking Phase 4 ("Mahoraga vs raw Claude Code" with cost accounting):

1. **New cloud arm `claude-cli`** (`workers/claude_cli.py`) — runs the `claude` CLI in print mode under the Max subscription (no `ANTHROPIC_API_KEY`; the var is stripped so a stale key can't shadow subscription auth). Captures per-task token usage + `total_cost_usd` from `--output-format json`. Sandboxed: prompt over stdin, `--disallowedTools Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch`, isolated cwd (`~/.mahoraga-v2/claude-cli-cwd`) so user-derived prompts can't drive project-authorized tools. Disabled in agents.yaml; enable only for bench runs.
2. **Real cost flows** — `resolve_cost()` threads worker-reported cost into `task_metrics.cost_usd`, `cost_ledger` (best-effort/guarded), `TaskOutcome` → decision log, so φ_cost finally sees real dollars. OllamaWorker now emits `prompt_tokens` (all 961 prior rows have 0 — input side of the counterfactual was missing); SDK claude arm emits usage+cost too so no arm is cost-invisible to the bandit.
3. **`orch bench report cost`** — offline counterfactual: local rows priced at frozen cloud rates (`PRICING_AS_OF=2026-07-26`; the old table had Opus 4.6 at 3× its real price), cloud rows counted at recorded actual cost (token re-pricing underpriced them ~100× — cache-creation dominates a real CLI call: ~35K tokens ≈ $0.21 for a trivial task). Discloses rows lacking prompt-token data. Current live-DB reading: 961 tasks, 100% local, **$1.55 avoided at bare token rates — a floor** (953 rows predate prompt-token emission; and the honest alternative, a full Claude Code call, costs ~$0.21/task ≈ $210/1k tasks).
4. **Known limitations (deferred, in PR body):** retried tasks record only the final attempt's spend (side-channel is last-write-wins; escalation can misattribute cost to the fallback arm); eval-path spend reaches the bandit but not the ledger; gateway-path spend reaches the ledger but not task_metrics; empty `agent_name` classifies as cloud in the report.

**Next session — Phase 4 head-to-head:** `orch serve` in a tmux pane (not the daemon), then
`orch bench run -p experiments/prompts_verifiable.jsonl --mode force-explore --agents ollama:qwen3.5,ollama:granite4.1-8b,ollama:qwen3-14b,claude-cli --repeats 2 --notes "Phase 4: local vs claude-cli, cost + pass@1"`,
then `orch bench report cost --bench-run-id N` + `orch bench report verify --bench-run-id N`. One run produces both the measured cloud cost and the quality-retention pass@1 — the two halves of the portfolio claim ("N% local, X% cost cut, Z% of cloud pass@1 retained"). All cloud calls are Max-subscription (no marginal spend). Consider a bigger verifiable bank first (open thread (b) below — only 18 gold prompts).

## Read this first — 2026-07-15

Shifted from "run more A/Bs" to "fix the ruler." The core diagnosis (this session): the composite reward has **no variance axis** on a free, mostly-succeeding local roster — success + cost are ~constant (~0.65 of the weight, pinned), speed is weakly/wrongly correlated with "better," and quality is a heuristic that Era 7 showed rewards elaboration, not correctness. That's why every downstream A/B (memory on/off, +qwen3-14b) came back null: **you can't detect an intervention with a metric that can't tell good answers from mediocre ones.** Direction chosen (Kaito): **verifiable (execution-based) rewards** for code/debug, where correctness is checkable rather than judged.

Done this session:
1. **Committed the whole 07-10 working tree** (classifier fix, qwen3-14b arm, offline replay tools, service-stop fix, brain docs) — five clean commits, 1231 tests green.
2. **Built a verifiable-reward eval harness** (Piece B): `experiments/prompts_verifiable.jsonl` (18 gold code/debug prompts with hidden Python tests, ground-truth self-validated) + `routing/verify_replay.py` + `orch bench report verify` (offline, zero-inference: extract code → run against hidden tests → pass@1 per arm, side-by-side with heuristic quality + Spearman rho). 22 new tests.
3. **The finding (Era 9 in findings.md):** execution pass@1 spans **0.882–1.000** across the arms (a real signal) where the composite reward tied them at 0.78–0.83. And the heuristic quality score **does not track correctness** — Spearman rho=0.40, the *only* 100%-correct arm (granite) is ranked 3rd of 4 by the heuristic; in the code bucket it gets the *lowest* q despite a perfect pass rate. This resolves Era 5's open fork toward **(b): the heuristic is structurally blind to correctness** — the models aren't similar, the scorer just can't see the difference. Bonus: the "gemma4 is the weakest arm" belief (from the May heuristic bench) was **falsified** on execution (gemma4 is mid-pack) — likely itself a heuristic artifact.
4. **Fixed a live-path bug:** `extract_code` returned raw output (incl. a literal ` ```python `) on truncated/unclosed fences, poisoning "code" for the coder role. Now tolerates unclosed fences.
5. **Scorer bake-off (Era 10):** using pass@1 as ground truth, ranked candidate scorers at the item level (n=70) — **execution (r_pb=+0.434) >> embedding-sim-to-reference (+0.183) > heuristic (+0.095)**. The heuristic is ~uncorrelated with correctness; reference-embedding-similarity is weak (ruled out). LLM-judge untested (deferred, but now cheaply validatable against the benchmark).
6. **Shipped the live execution gate (Piece A):** `routing/execution_gate.py` wired into the serving reward path — for code/test/refactor/debug, output that doesn't execute flips the outcome to failed (reward → 0). Conservative (catches "doesn't run", not "wrong"). **On by default; `MAHORAGA_EXEC_GATE=off` disables.** Verified end-to-end live. Runs model code in an 8s subprocess (security note in the module).

**Open threads (next session):** (a) whether to restart the persistent daemon — see below; (b) a *harder* verifiable bank (only 5/70 outputs failed here, so the fail class is small); (c) the one untested scorer candidate, a better-prompted **LLM-judge**, now cheaply validatable against pass@1; (d) extend verifiable coverage to the `test` bucket (mutation-based) — current bank is code+debug only.

State when leaving off: `gemma4:e4b` reverted to `enabled: false`. **The persistent launchd daemon is now ON** (`orch service start`, verified healthy: 3 agents, 1095 decisions) — restarted this session so Mahoraga is reachable from both Claude Code and Cursor. **The execution gate is ON** (default) and now active on live code/test/debug traffic. All tooling + brain docs committed; `verifiable_results_20260715.jsonl` stays local (gitignored).

**Cursor integration added (2026-07-15):** `~/.cursor/mcp.json` registers the `mahoraga` stdio MCP server (`.venv/bin/python -m backend.mcp.server`, `MAHORAGA_BASE=http://localhost:8000`) — same 20 tools Claude Code has. `~/.cursor/rules/mahoraga-routing.mdc` is a ported+updated version of the `/mahoraga` skill's routing guidance (roster corrected to the 3 real local arms). **Both editors share one backend** (bandit, decision DB, exec gate), so Cursor traffic now mixes into the same real-traffic dataset — there's no editor/source tag today, so keep that in mind before running clean experiments. The `/mahoraga` Claude Code skill's roster table is stale (still lists Aider/Gemini/Goose/OpenCode/LFM2) — worth updating to the 3-arm reality if revisited.

## Read this first — 2026-07-10

Overnight run (~1:30am–3:00am) covered the fully-automatable half of the open backlog; morning session fixed the bug it found and started the blind-ranking exercise. Full detail in `brain/state/findings.md` (Era 6) and the journal. Top line:

1. **Found and fixed a real bug**: the bucket classifier (`store/metrics.py:_classify_bucket`) was misrouting almost all real security-intent traffic to the `code` bucket instead of `security` — confirmed 0/80 real prompts classified as `security` in a sample that included 14 explicit security-audit prompts. Two compounding causes: the security keyword list checked singular `"vulnerability"` (missing the much more common plural `"vulnerabilities"`), and `code`'s generic keywords (`"implement"`, `"def "`, `"return"`) won first-match-in-dict-order against almost any prompt that quotes real code, which most security/review/refactor/debug prompts do. **Fixed 2026-07-10 morning**: reordered so intent-signaling buckets are checked before `code`, fixed the keyword gap, removed a colliding keyword the reorder exposed (`"check"` in `review`). Re-verified: 14/14 real security prompts now correct; `code`'s share of real traffic dropped from 62.5% to 8.75%. 9 new regression tests, full suite 1231 passed. This only ever affected unhinted (organic MCP) traffic — none of this project's own bench experiments were affected, since they always pass an explicit bucket hint.
2. Re-ran the difficulty-tier diagnostic at much better power (9-bucket coverage, n=18/tier vs the original n=2) — confirms the earlier null result: hard tasks don't show a wider inter-agent quality gap than easy ones.
3. Re-checked compat-matrix + leader-stability with qwen3-14b now at full sample size (14-29/bucket) — the tie/instability persists; adding a 3rd arm didn't resolve it.
4. Crossed Q6's 500-task real-traffic threshold (517 unforced decisions now) — the data-volume blocker is gone, the actual retrieval-vs-vanilla A/B test still needs to be designed and run.
5. Confirmed the 2026-04-24 "37% missing reward" bug does not reproduce on the current 3-arm roster (0 NULL rewards/success across 660 rows).
6. Built `experiments/blind_ranking_sheet.md` — 7 real prompts × 3 agents, blind-labeled — the "stronger judge" calibration step recommended last session. In progress as of this morning.
7. **Roster decision**: qwen3-14b judged "too large for this machine" but kept in the roster anyway — still generating useful comparative data for the reward-tie investigation.
8. **Blind ranking done, scorer fix attempted and rejected on evidence.** Kaito's manual blind ranking (7 real prompts) agreed with the heuristic scorer's top pick only 3/7 (43%, barely above chance) — every disagreement traced to the scorer's flat +0.10 structure bonus rewarding elaboration Kaito explicitly called out as not earned. A diminishing-returns length-curve fix was built and tested against the same ground truth: didn't help (one variant made it worse, 2/6) — the curve doesn't touch the actual tie-breaker (the flat structure bonus). A local LLM judge (gemma4:e4b, not a roster arm) was tried as a scalable alternative to human ranking: 1/6 agreement, and its own stated reasoning showed the identical elaboration bias. **Decision: stop here, don't ship either fix** — documented in `brain/state/findings.md` Era 7, revisit only if a cheap way to get reliable volume ground truth turns up.
9. **Q6's first real A/B data point: null result at n≈42/condition.** Ran the same 42-prompt bank through `MAHORAGA_MEMORY_MODE=off` vs default `semantic`, sequentially, via manually-started `orch serve` instances (persistent daemon restored after). Memory changed the routed agent on 6/41 (14.6%) matched prompts — a real effect on individual decisions — but aggregate reward barely moved (0.7963 vs 0.7839, diff/SE≈0.54) — not distinguishable from noise at this sample size. Not conclusive either way; would need several hundred/condition. Full detail in `brain/state/findings.md` Era 8.
10. **Found + fixed a second `orch service stop` regression**, different mechanism than the 2026-07-09 fix. `launchctl stop` sends SIGTERM; the process is killed *by* the signal rather than exiting cleanly, so `LastExitStatus` records the signal number, which `KeepAlive: {SuccessfulExit: false}` treats as a crash and respawns anyway. **Fixed 2026-07-10**: `stop` now unloads the job (`launchctl unload`) instead of sending SIGTERM to a still-loaded job; `start` re-loads it. Verified: stop → `000`/unloaded and stays that way past 5s, start → `200` with a fresh `uptime_s` and full decision history intact.

Everything above is logged to `bench_runs` (#7-#12) with notes.

## What Mahoraga is right now

A working local-first orchestrator with per-bucket bandit routing, episodic memory (semantic mode default), and a persistent background daemon. The full routing loop is verified: task → bucket classification → per-bucket UCB scoring → Ollama arm runs → reward → A/b matrix update → episode in DB.

## Active roster (3 arms, all local)

| Arm | Model | Strengths | Prior |
|---|---|---|---|
| `ollama:qwen3.5` | qwen3.5:latest (6.6 GB, Q4_K_M) | code, reasoning | 0.75 |
| `ollama:granite4.1-8b` | granite4.1:8b (5.3 GB, IBM) | test, review, structured output | 0.75 |
| `ollama:qwen3-14b` | qwen3:14b (9.3 GB) | diagnostic arm, added 2026-07-09 — see finding below | 0.75 |

`ollama:gemma4-e4b` disabled 2026-05-23 — lowest reward in every bucket in the 2026-05-20 bench; granite covers the same capability space. Cloud agents (claude, codex-cli, gemini-cli) are registered but effectively disabled — no API keys in env, gated by budget pacer.

## Candidate arms blocked on hardware (do not re-research)

- **North Mini Code 1.0** (Cohere, released 2026-06-09) — 30B-total / **3B-active** MoE (128 experts, 8/token), **Apache 2.0** (clean commercial license), on Ollama as `north-mini-code-1.0`. Genuinely SOTA-for-size on coding (SWE-bench Verified 80.2% pass@10; beats Qwen3.5-35B-A3B on the AA coding index) and would be an ideal *fast* arm — 3B active ≈ 3B-dense inference speed. **Blocker: does not fit 16 GB.** Smallest published quant (`q4_K_M`) is **19 GB** — MoE stores all 30B total params regardless of active count, so the weights alone exceed total unified memory before OS + KV cache. Target it if Mahoraga ever runs on ≥32 GB hardware or gains a remote/cloud arm tier. (Researched 2026-07-26.)
- Also evaluated + rejected same date: **Laguna S 2.1** (Poolside) — 118B-A8B, smallest quant 33.8 GB, needs ~128 GB (DGX Spark class). Not a laptop model.
- **The structural lesson:** the mid-2026 MoE trend buys quality with *total* params (memory) while keeping *active* params (compute) low — the opposite of what a 16 GB memory-bound box wants. "3B active" markets like a small model but costs like a 30B one to hold in RAM. Fittable local arms stay ≤ ~14B total.

## 2026-07-09 finding: qwen3.5 vs granite4.1-8b tie in composite reward, and it's not the reward weights

Real traffic (337 non-bench decisions as of today) shows both arms scoring 0.78–0.83 avg reward in nearly every bucket, with the "leading" agent flipping between the first and second half of each bucket's history in 7/9 buckets. Checked two hypotheses, both offline/zero-inference:

1. **Reward-weight structure** — `orch bench report reweight` (new, `routing/reweight_replay.py`) recomputes logged decisions' reward under alternate weight vectors with no new inference. Pushing quality weight from ~0.20–0.45 up to 0.55 (successs down to 0.20) only widened the agent gap 1.3–2x in most buckets (max ratio 4.9x in `general`, but off a tiny 0.0026 base; `review` actually *shrank*). Conclusion: **the tie is not a reward-weight artifact** — these two models' logged quality scores are genuinely close on this task mix.
2. **Model similarity** — registered `ollama:qwen3-14b` (already on disk, 9.3GB, unused since the May bench) as a 3rd arm to test whether a model with a real capability/speed gap breaks the tie. Live as of today; needs real traffic to accumulate before it says anything.

**Next check:** once qwen3-14b has enough samples per bucket (rough target: 15-20+), re-run `orch bench report compat-matrix` and the same per-bucket leader-stability check. If qwen3-14b also ties, that's stronger evidence the composite reward formula structurally suppresses separation for any local-only, similarly-successful roster (success + cost together are 0.65 of the weight and both are ~constant across free local arms that mostly succeed) — worth revisiting `BUCKET_WEIGHTS` then, with evidence instead of a hunch.

## 2026-07-09 (cont'd): difficulty-tier diagnostic (Q5) — no support, and semantic memory confirmed healthy

Ran `experiments/prompts_difficulty.jsonl` (12 prompts, easy/hard × code/research/security) force-explore across all 3 arms (bench_run_id=4, 36 tasks, 100% pass). At n=2/cell — underpowered, directional only — the quality gap between agents did **not** widen on hard tasks (if anything, slightly narrower), and qwen3-14b showed no quality edge on hard tasks despite legitimate, appropriately-detailed responses (spot-checked raw outputs — not truncated, hard-task answers ran 67-330 tokens vs 5-23 for easy). Tentative read: Q5's hypothesis doesn't hold here, or the quality scorer itself may not be discriminative enough to detect a real gap even when the model outputs plausibly differ in depth — worth checking the scorer's behavior independent of which model answers before spending more compute on roster diversity as the fix.

Also verified semantic episodic memory directly (not just "files exist"): queried `EpisodicMemory.query_semantic_with_confidence` live with a real prompt embedding and got back sensible, differentiated per-agent bias/confidence/count. 596 episodes stored, 100% with embeddings. Correction to the 2026-07-03 next-steps: the embedding model is `all-MiniLM-L6-v2` via `sentence-transformers` (see `routing/embeddings.py:37`), **not** nomic-embed-text — nomic-embed-text is on disk but unused, a leftover from an earlier design pass.

Set up a 7-day trial recurring job (`CronCreate`, every 6h, 15-task un-forced bandit batches) to keep accumulating data without relying on remembering to trigger it. Session-scoped — dies if this Claude session ends, auto-expires in 7 days regardless. Making it a permanent launchd job is a separate, not-yet-made decision.

## 2026-07-09 (cont'd): quality-scorer discriminability check — caps/plateaus are NOT the bottleneck

Built `routing/quality_replay.py` + `orch bench report quality-replay` (bench_run_id=6): re-scores already-captured real (prompt, output, bucket, agent) rows under generous heuristic variants — higher length plateau (300→800 words), harder length-ratio target (2.5x), uncapped security keyword bonuses, continuous (not binary) not-plan — with zero new inference. To make this possible, added `prompt_full`/`output_full` fields to `bench.py`'s JSONL output (previously only 60/120-char previews were persisted; full text wasn't stored anywhere, including the DB), then re-ran the Q5 diagnostic batch (bench_run_id=5, 34/36 succeeded) to get real full-text captures.

**Result: every generous variant, run on the exact same real text that showed no Q5 gap-widening, produced ~equal or *smaller* per-bucket agent gaps than baseline** (`max_variant_widening_ratio=1.0` — no variant widened at all; research bucket gap actually shrank 0.105→0.085 under the most generous config). Two of the suspected compression points turned out not to even be *engaged* by this data: security keyword hits topped out at 4/4 (mitigation/threat) across all 34 real answers — under, not at, the original caps of 5/4 — so uncapping changed nothing because the cap was never hit; the not-plan detector scored every single row 1.0 (not plan-shaped) so binary-vs-continuous was moot for the same reason. The length-plateau variants are the only ones that were genuinely tested and available data — and they *narrowed* the gap slightly, not widened it, because a higher plateau also dilutes the short easy-tier answers less asymmetrically than expected.

**Read on this:** the heuristic scorer's caps aren't suppressing a real signal that's present in these outputs — on this data, there just isn't more separating signal to uncover by being more generous with existing heuristic knobs. This rules out "the formula's caps are hiding real differences" as the explanation for the qwen3.5/granite/qwen3-14b tie. What's left: either (a) genuinely similar model quality on this task mix, or (b) heuristic scoring (regex/keyword/length, no semantic understanding) is structurally incapable of detecting the kind of depth difference that exists in these outputs, no matter how the same knobs are tuned. Only a stronger judge (embedding-similarity-to-a-reference-answer, or an LLM-judge used offline-only for validation, never in the live reward loop) can distinguish between (a) and (b) — next step if this is revisited.

## Architecture shape

```
User (Claude Code + /mahoraga skill)
    ↓ MCP
FastAPI (orch serve — runs as launchd daemon, always on)
    ↓
BanditRouter
    ├── classify_bucket(context) → bucket label
    ├── LinUCBPerBucketRouter.select_agent() → UCB pick from 3 arms
    ├── episodic memory (semantic mode, nomic-embed-text)
    └── quality scoring → composite reward → A[bucket][agent] update
    ↓
OllamaWorker (subprocess call to localhost:11434)
    ↓
routing_decisions.db (SQLite, ~/.mahoraga-v2/)
```

## Bandit state

- Strategy: `linucb_per_bucket` (per-bucket disjoint A/b matrices, γ=0.98 global decay)
- State file: `~/.mahoraga-v2/bandit_state.json`
- Decisions DB: `~/.mahoraga-v2/routing_decisions.db` — clean reset 2026-05-20, **207 real decisions since** (111 qwen3.5, 96 granite4.1-8b; last traffic 2026-07-02)
- Backups of pre-reset state at `~/.mahoraga-v2/*.bak`

## Infrastructure

- `orch service install` — launchd daemon, login-persistent, logs to `~/.mahoraga-v2/server.log`
- `orch service start` / `orch service stop` / `orch service status` — real on/off toggle. 2026-07-09: `KeepAlive: true` → `{SuccessfulExit: false}` (crash → auto-restart, deliberate stop → stays stopped). 2026-07-10: that alone wasn't enough — `stop` used `launchctl stop`, which sends SIGTERM to a job that stays *loaded*, so the signal-killed exit still tripped `KeepAlive`'s respawn. Fixed by having `stop` unload the job entirely (`launchctl unload`) and `start` reload it. Verified both ways.
- `orch serve` — manual start at localhost:8000
- `agents.yaml` — config-driven arm registration; `enabled: false` disables without losing bandit history
- **Experiment ledger** — `orch bench report runs` lists every experiment (live batches AND offline analyses) with a `notes` field explaining why. Live batches: pass `--notes` to `orch bench run`. Offline (`orch replay run`, `orch bench report reweight`): auto-logs a summary + accepts `--notes` too. All write to the same `bench_runs` table via `routing/reweight_replay.py:log_offline_run`. Use this instead of relying on conversation history to remember what's been tested.
- **MCP `run_task` vs `route_task`** — `run_task` always commits a real, reward-logged decision; `route_task` previews the pick with zero commit. Use `route_task` for probing/testing so casual checks don't pollute the real-traffic dataset used for convergence analysis (some early "what's up, 2+2" rows in the DB are exactly this kind of pollution).

## Ollama models (disk)

```
qwen3.5:latest       6.6 GB  ← arm 1
granite4.1:8b        5.3 GB  ← arm 2
nomic-embed-text     274 MB  ← semantic episodic memory
gemma4:e4b           9.6 GB  ← disabled arm, still on disk (rm if space needed)
qwen3:14b            9.3 GB  ← arm 3, added 2026-07-09 as diagnostic third arm
```

## Next steps (in order)

### 1. ~~Clean stale model~~ ✅ done
### 2. ~~`orch benchmark lab`~~ ✅ done (also found + fixed unexplored-arm UCB inflation bug)

### ~~Let real traffic train the bandit~~ ✅ underway
207 real decisions since the 2026-05-20 reset (111 qwen3.5, 96 granite).
Both arms are past the 20–50 pull warmup threshold — adaptive gamma (§4)
is now unblocked.

### ~~3. Cross-bucket routing check~~ ✅ done 2026-07-10
Found + fixed the classifier bug in the process (see item 1 above,
Era 6 in findings.md) — real security-intent traffic was being
misclassified as `code`.

### ~~4. Gamma (adaptive per-arm decay)~~ ✅ shipped 2026-07-03
Live in `linucb_per_bucket.py` with per-(bucket, arm) warmup, a
noise-floor-centered mapping, variance floor + outlier cap, EMA-decay
recovery, and w-weighted tracking — several deliberate deviations from
`docs/specs/gamma-spec.md`, all forced by adversarially-verified defects.
**Read `brain/decisions/2026-07-03-adaptive-gamma.md` before touching it.**
Drift ablation (`orch benchmark ablation`, exp 6): adaptive+recovery beats
global γ 11.64 vs 12.85 final regret. Remaining: full sweep grid with
detection/recovery metrics; distance-weighted episodic α (separate spec).

### 5. Semantic retrieval validation
Semantic episodic memory is wired as default (`MEMORY_MODE_SEMANTIC`) but
never verified against our 3-arm roster. After benchmark lab run:
- Check `_retrieve_memory_biases_rich()` is calling nomic-embed-text
- Verify episodic memory is growing (`.bin` file size increasing)
- Compare routing quality with `MAHORAGA_MEMORY_MODE=keyword` vs default

### 6. Q6 A/B at larger N — in progress 2026-07-10
First real A/B (semantic vs off, n≈42/condition) came back an honest
null — memory changes 14.6% of routing decisions but reward diff/SE≈0.54,
indistinguishable from noise at this sample size (Era 8, findings.md).
Re-running at several hundred prompts/condition to get a result that
can actually resolve Q6 either way.

## Key files

| File | What it does |
|---|---|
| `backend/orchestrator/service/app.py:207` | Strategy initialization (linucb_per_bucket) |
| `backend/orchestrator/routing/strategies/linucb_per_bucket.py` | The v2 bandit |
| `backend/orchestrator/routing/bandit_router.py` | Full routing loop, memory, escalation |
| `backend/orchestrator/routing/strategies/static.py` | `classify_bucket()` — bucket labels |
| `backend/orchestrator/adapters/loader.py` | agents.yaml → adapter + worker registration |
| `agents.yaml` | Arm roster, capabilities, priors |
| `backend/orchestrator/cli/commands/service.py` | launchd daemon management |
| `~/.mahoraga-v2/routing_decisions.db` | All routing decisions + rewards |
| `~/.mahoraga-v2/bandit_state.json` | Persisted A/b matrices per bucket |

## Known issues / lessons

- **Auto-logging to the repo brain is off as of 2026-07-03.** The router was appending a content-free "Routed to X" entry to `brain/decisions/log.md` on every decision (2M lines), and the daemon wrote an empty journal stub on every shutdown. Both call sites removed; SQLite (`routing_decisions.db`) is the only decision log. See ADR `brain/decisions/2026-07-03-remove-brain-auto-append.md`.
- **Never use `--mode force-explore` to seed the bandit.** Force-explore trains some arms and leaves others cold — creates UCB inflation asymmetry. If seeding is needed, use `inject_pseudo_obs` or run bandit mode.
- Cross-bucket routing unverified with real traffic — only tested via routing probe
- `_DEFAULT_PRIORS` equal across all 3 arms (by design, pure cold-start exploration) — will diverge naturally

## What we learned from the bench run (2026-05-20)

Even though the bench data was wiped from the bandit matrices (clean reset), the quality signal is informative:
- **granite4.1-8b** won 6/7 buckets — best avg reward, especially plan (0.874) and research (0.833)
- **qwen3.5** narrowly beat granite on code only (0.782 vs 0.776)
- **gemma4-e4b** underperformed across the board — lowest reward in every bucket
The bandit will rediscover this naturally from real traffic.
