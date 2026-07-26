# Current State — 2026-07-26

## Read this first — 2026-07-26

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
