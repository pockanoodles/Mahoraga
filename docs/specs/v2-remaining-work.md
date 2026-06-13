# Mahoraga v2 — Remaining Work + Strategic Direction

**Date:** 2026-05-06
**Context:** Post-debugging session. 630 tests passing, 1,318 LoC added. Composer live in shadow mode. Implicit feedback now writing to DB. Security bucket scoring differentiated. This document covers what's left to build and where the project goes next.

---

## Part A: Implementation Spec

Four items remain from the debugging session, plus the off-policy correction for composer overrides. Each section is a buildable spec with enough detail to hand to an agent.

---

### A1. Off-Policy Correction for Composer Overrides

**Problem.** When the composer flips codex-cli → ollama, the bandit calls `update(context, "ollama", reward)`. LinUCB treats this as "I chose ollama and got this reward." But the bandit didn't choose ollama — it chose codex-cli. The A/b matrices for ollama get credit for a decision the bandit didn't make. Over time this distorts exploration: the bandit over-credits agents the composer favours and under-explores agents the composer overrides away from.

**Solution: importance-weighted updates.**

Standard LinUCB update (no override):

```
A_a ← γ·A_a + x·x'
b_a ← γ·b_a + r·x
```

Importance-weighted update (override happened):

```
w = P_bandit(a) / P_composer(a)

A_a ← γ·A_a + w·x·x'
b_a ← γ·b_a + w·r·x
```

`P_composer(a)` is 1.0 (the composer forced this agent). `P_bandit(a)` is derived from UCB scores via softmax:

```
P_bandit(a) = exp(UCB_a / τ) / Σ_i exp(UCB_i / τ)
```

τ (temperature) should be set to the standard deviation of UCB scores across agents for that decision. This auto-scales: when UCB scores are tightly clustered (bandit is uncertain), probabilities are near-uniform and weights are moderate. When one agent dominates (bandit is confident), probabilities are peaked and the override weight is very low — the bandit barely learns from a decision it strongly disagreed with, which is correct.

**Behaviour by case:**

| Scenario | w | Effect |
|----------|---|--------|
| No override (common case) | P_bandit(a) / P_bandit(a) = 1.0 | Standard update, zero overhead |
| Override, bandit had 80% on final agent | 0.80 | Nearly full update, bandit mostly agreed |
| Override, bandit had 10% on final agent | 0.10 | Weak update, bandit is surprised |
| Override, bandit had 1% on final agent | 0.01 | Near-zero update, bandit strongly disagreed |

**The overridden agent (bandit's original pick that didn't run) gets no update.** No reward was observed. Don't fabricate one. Once counterfactual estimation lands (post-A1), the logged UCB scores and embedding enable retroactive estimation. Until then, silence is correct.

**Episode schema addition:**

```python
@dataclass
class Episode:
    # existing fields...
    bandit_pick: str                    # who the bandit wanted
    final_pick: str                     # who actually ran
    ucb_scores: dict[str, float]        # full UCB scores at decision time
    bandit_probs: dict[str, float]      # softmax(UCB / τ)
    override_reason: str | None         # "a3_confidence", "a3_delta", None
    importance_weight: float            # w used in the actual update (1.0 if no override)
```

Log all of these unconditionally — even when there's no override. The UCB scores and probabilities are already computed at selection time. Storing them costs ~200 bytes per episode and gives you full off-policy replay capability later.

**Files changed:** `routing/bandit_router.py` (update method), `routing/composer.py` (pass through bandit_probs), episode storage schema.

**Tests:** Override with w≈0.01 produces near-zero A/b delta. No override produces identical A/b to current code. τ auto-scaling produces reasonable probabilities (not all 0/1, not all uniform) on real UCB score distributions from the benchmark.

---

### A2. Escalation Gateway

**Problem.** `should_escalate=True` is computed but nothing consumes it. The flag exists in the composed decision but the execution layer doesn't branch on it.

**Solution.** Three escalation strategies, selected by the composer based on the uncertainty level and budget state:

**Strategy 1 — Claude escalation.** Route the task to the Claude API adapter. This is the existing escalation path (retry → Claude) but triggered proactively by confidence, not reactively by failure. Only fires when `ANTHROPIC_API_KEY` is set. If no key, fall through to strategy 2.

**Strategy 2 — Double-run.** Execute the bandit's pick AND the composer's preferred agent in parallel (or sequentially if parallel isn't implemented yet). Return the higher-quality output per the 4-layer scorer. Both outcomes are logged as episodes — the bandit learns from both. This costs 2x latency but zero API dollars if both agents are local/free.

**Strategy 3 — Aggressive verify.** Execute the bandit's pick, but run the quality evaluator with a stricter threshold (e.g., quality ≥ 0.70 instead of the default pass threshold). If it fails the strict check, retry with the composer's preferred agent. This is the cheapest escalation — same agent, higher bar, fallback on failure.

**Selection logic (in composer):**

```python
if should_escalate:
    if anthropic_key_available and budget_permits:
        strategy = "claude_escalation"
    elif final_pick != bandit_pick:
        # composer already has a preferred alternative
        strategy = "double_run"
    else:
        strategy = "aggressive_verify"
```

**Gateway hook.** The execution layer in `service/app.py` checks `composed_decision.escalation_strategy`:

- `None` → normal execution (current path).
- `"claude_escalation"` → swap the agent adapter to Claude before executing.
- `"double_run"` → execute both, pick best, log both episodes.
- `"aggressive_verify"` → execute normally, apply strict quality threshold, retry on fail.

**Budget interaction.** If ParetoBandit-style budget pacing lands later, the `budget_permits` check becomes a real constraint. For now, it's a boolean flag: `MAHORAGA_ALLOW_PAID_ESCALATION=true/false` (default false). When false, escalation never routes to Claude — only double-run or aggressive verify.

**Files changed:** `routing/composer.py` (add escalation_strategy field), `service/app.py` (gateway hook consuming the strategy), new `routing/escalation_strategies.py` for the three strategy implementations.

**Tests:** Escalation fires when should_escalate=True and correct strategy is selected per conditions. Double-run logs two episodes. Aggressive verify retries on quality < strict threshold. Budget flag suppresses Claude escalation.

---

### A3. Quality Model Retraining Schedule

**Problem.** `orch quality train` runs manually. The model goes stale as the decisions DB grows. After 500 new episodes, the model trained on the first 250 is increasingly miscalibrated.

**Solution: staleness check on startup + periodic retrain.**

**On `orch serve` startup:**

1. Check `~/.mahoraga/quality_model_meta.json` for `trained_at_episode_count`.
2. Query decisions DB for current episode count.
3. If `current_count > trained_count * 1.5` (50% growth) or `current_count - trained_count > 500` (absolute growth), log a warning: "Quality model is stale (trained on N episodes, DB has M). Run `orch quality train` or enable auto-retrain."

**Auto-retrain (opt-in):**

`MAHORAGA_AUTO_RETRAIN=true` enables a lifespan hook in the FastAPI app that triggers `orch quality train` under two conditions:

- On startup, if the staleness check above triggers.
- Every 500 new episodes logged during the session (tracked by a counter in `ImplicitQualityTracker`).

Retrain runs in a background thread. While retraining, the old model continues serving predictions. On completion, the new model is hot-swapped via the mtime-based cache invalidation that's already wired (from the edge case fixes). The serving path is never blocked.

**Retrain is fast.** OLS on 500–5,000 rows with 9 features takes <1 second. Even a logistic regression with cross-validation takes <5 seconds. This is not a GPU training job.

**Model versioning.** Each trained model writes `quality_model_meta.json`:

```json
{
    "trained_at": "2026-05-06T15:30:00Z",
    "trained_at_episode_count": 750,
    "train_auc": 0.89,
    "test_auc": 0.91,
    "spearman": 0.56,
    "feature_importances": {
        "embedding_sim": 0.31,
        "novelty_ratio": 0.24,
        "length_fit": 0.19,
        ...
    }
}
```

`orch quality inspect` prints this. Useful for debugging and for tracking whether the model is actually improving as more data comes in.

**Safeguard.** If the newly trained model has test AUC < 0.55 (barely above random), reject it and keep the old model. Log a warning. This prevents a degenerate retrain (e.g., if the decisions DB gets corrupted or the implicit feedback distribution shifts pathologically) from replacing a working model with garbage.

**Files changed:** `service/app.py` (lifespan hook), `routing/quality_predictor.py` (staleness check, background retrain), `cli/quality.py` (inspect command).

**Tests:** Staleness check fires at correct thresholds. Background retrain doesn't block routing. Hot-swap works mid-session. Degenerate model is rejected.

---

### A4. Brain Integration — Context Augmentation, Not Agent Bias

**Problem.** Brain/journal hits are currently read-only context. The original framing was "map brain hits to per-agent biases" — e.g., if the brain says "we use PostgreSQL," somehow boost agents that are good at PostgreSQL tasks. This requires either a hand-maintained tagging convention (brittle, doesn't scale) or an LLM call per routing decision (violates zero-API-cost constraint).

**Reframing: brain hits are embedding tags, not agent selectors.**

The brain doesn't tell the router *which agent* to pick. It tells the *embedding* what the task really means in context. This falls out of A1's tag-enhanced embedding naturally.

**How it works:**

1. Task arrives: "Fix the race condition in the connection pool."
2. Brain/journal is queried (existing path). Returns hits: "Project uses PostgreSQL with pgBouncer connection pooling. Team decided on optimistic locking in sprint 12."
3. Before semantic encoding, the task description is augmented with brain context:

```python
# Before A4:
embedding_input = f"{bucket}: {task_description}"

# After A4:
brain_summary = summarise_brain_hits(hits, max_tokens=50)
embedding_input = f"{bucket}: {task_description} [{brain_summary}]"
# e.g., "code: Fix the race condition in the connection pool 
#         [PostgreSQL, pgBouncer, optimistic locking]"
```

4. The augmented text is embedded via MiniLM (384-dim). The embedding now captures not just "race condition in a connection pool" but "race condition in a PostgreSQL/pgBouncer connection pool where the team uses optimistic locking."
5. Episodic memory retrieves episodes that are semantically similar to *this project's* race conditions — not generic race conditions. The reward bias reflects agent performance in this project context.

**The agent preference emerges from retrieved rewards.** If past episodes show that Qwen 3.5 9B nailed PostgreSQL concurrency tasks with 0.90 reward while Gemini CLI scored 0.72, the memory bias naturally favours Qwen. No explicit mapping needed. The brain context just makes the retrieval more precise.

**`summarise_brain_hits` is a pure function, not an LLM call.** It extracts keywords/entities from the brain hits using simple heuristics: take the nouns and proper nouns from the top-3 hits by similarity score, deduplicate, truncate to 50 tokens. No NLP model needed. The embedding model (MiniLM) does the semantic heavy lifting — it just needs the keywords as input signal.

If keyword extraction is too noisy, the fallback is even simpler: take the first sentence of the top-1 brain hit. That's usually the most information-dense.

**Dependency: this requires A1 (semantic embedding) to be in place.** Without semantic encoding, the brain context has nowhere to flow into. The 9-dim handcrafted vector doesn't consume free text. This is a post-A1 feature.

**Files changed:** `routing/brain_integration.py` (new: `summarise_brain_hits`), `routing/embeddings.py` (accept optional context parameter), `routing/bandit_router.py` (pass brain context to embedding).

**Tests:** Augmented embedding for "fix race condition [PostgreSQL, pgBouncer]" has higher cosine similarity to past PostgreSQL episodes than un-augmented "fix race condition." Brain hits with no useful content (empty, irrelevant) produce unchanged embeddings. Token truncation at 50 doesn't cut mid-word.

---

### A5. Additional Debugging and Hardening

**Bucket scoring audit.** The security bucket was misclassified through `_score_code`. Audit all buckets to verify that the scoring pathway matches the expected output format:

| Bucket | Expected output | Correct scorer | Verify |
|--------|----------------|----------------|--------|
| code | Code blocks, imports, functions | `_score_code` | ✓ (already correct) |
| debug | Mix of prose + code | `_score_code` with prose fallback | Check: does a prose-only debug answer (no code fix, just diagnosis) get scored fairly? |
| plan | Prose with structure | `_score_general` | Check: are numbered lists getting structural credit? |
| research | Prose, citations, comparisons | `_score_general` | Check: is a research answer that quotes sources getting novelty credit or penalty? |
| review | Prose with code references | `_score_general` | Check: inline code references don't trigger `_score_code` pathway |
| refactor | Code blocks | `_score_code` | ✓ (should be correct) |
| test | Code blocks (test functions) | `_score_code` | Check: test code often has no imports/class defs — does the structural check penalise that? |
| security | Prose with domain terms | `_score_security` (new) | ✓ (just fixed) |
| general | Prose | `_score_general` | ✓ (should be correct) |

The `debug`, `plan`, `test`, and `review` buckets are the ones most likely to have the same misclassification pattern that hit security. Quick check: run 3 representative prompts per bucket through the scorer, verify the scores are distributed (not clustered at a single value).

**Decision log completeness.** Now that implicit feedback writes to the DB, verify that the schema captures everything the off-policy correction needs:

```sql
-- These columns must exist and be populated:
bandit_pick       TEXT       -- who the bandit selected (before composer)
final_pick        TEXT       -- who actually ran (after composer)
ucb_scores        TEXT       -- JSON dict of {agent: score}
bandit_probs      TEXT       -- JSON dict of {agent: probability}
override_reason   TEXT       -- NULL if no override
importance_weight REAL       -- 1.0 if no override
```

If these columns don't exist yet, add them via migration. Backfill `importance_weight = 1.0` and `override_reason = NULL` for all existing rows (they pre-date the composer, so no overrides occurred).

**Composer shadow telemetry.** While the composer runs in shadow mode, log its decisions alongside the bandit's actual decisions:

```python
{
    "task_hash": "abc123",
    "bandit_pick": "codex-cli",
    "composer_would_pick": "ollama",
    "composer_would_override": True,
    "override_reason": "a3_delta",
    "a3_predictions": {"ollama": 0.74, "codex-cli": 0.16, ...},
    "brain_hit_count": 2,
    "brain_top_sim": 0.61
}
```

This lets you evaluate the composer's decision quality before enabling it. After 200+ shadow episodes, compute: "if we had followed the composer, what would cumulative reward have been?" Compare against actual cumulative reward. If the composer's counterfactual reward is higher, enable it. If not, tune the thresholds.

---

## Part B: Strategic Direction

This section doesn't contain implementation specs. It contains decisions and reasoning about where Mahoraga v2 goes as a system — new models, new environment, new papers. Nothing here needs to be built now, but all of it shapes what gets built next.

---

### B1. Agent Roster Overhaul

The v1 roster was experimental: 8 agents, some overlapping, the bandit's job was to figure out who's good at what. v2 should be deliberate. Fewer arms means faster convergence, and the arms should be maximally separated in capability.

**The v2 roster:**

| Tier | Agent | Role | Cost | Why it's here |
|------|-------|------|------|---------------|
| Local default | Ollama: Qwen 3.5 9B (Q4_K_M) | Code, refactor, general | Free | Best quality-to-speed ratio on 16GB. Replaces Qwen3 4B. ~6.6GB RAM, leaves room for context + embedding model. |
| Local alt | Ollama: Gemma 4 E4B | Plan, research, general | Free | Strong on planning and research. Different strengths from Qwen — gives the bandit a real local choice. |
| Free cloud | Gemini CLI | Research, complex code, long-context | Free (1K req/day) | 1M token context, Plan Mode, 1000 free requests/day. The research workhorse. |
| Free cloud | Codex CLI | Sandboxed code, single-file | Free (for now) | Rebuilt in Rust, 77.3% Terminal-Bench, kernel-level sandboxing. Strong on contained code tasks. |
| Paid escalation | Claude API | Complex multi-file, quality ceiling | Per-token | 80.9% SWE-bench. Only used when the escalation gateway fires. Budget-gated. |

**Dropped from v1:** DeepSeek-R1 (unusable at 123.5s on 16GB), LFM2 (speed doesn't justify quality gap vs Qwen 3.5), Goose (overlaps with Gemini CLI without the free tier), OpenCode (overlaps with Codex CLI without the free tier), Aider (strong tool but overlaps with the local Ollama agents for most tasks; can be re-registered by users who want it).

**Dropped count: 8 → 5 agents.** The bandit now has 5 arms per bucket instead of 8. With the 9-dim context vector, that means faster convergence (fewer A/b matrices to learn, less exploration needed). With semantic memory, even faster (better priors from day one).

**Model update cadence.** The local models will shift. Qwen 3.6 is already out. The adapter interface (`AgentAdapter`) means swapping Qwen 3.5 for 3.6 is a config change, not a code change. But the bandit's learned A/b matrices for "ollama" become stale when the underlying model changes. Options:

- Reset the arm's A/b to average-initialised (current behaviour for new agents). Loses history but avoids stale priors.
- Apply the warm-start logic: use the old arm's A/b as the prior for the new arm, with a decay factor. The bandit converges faster because it starts from "Qwen 3.5 was good at code" rather than "I know nothing."
- Do nothing. The dLinUCB discount (γ=0.98) will naturally decay stale observations over ~50 episodes. If the new model is similar to the old one, this is fine. If it's dramatically different, it's slow to adapt.

Recommendation: warm-start with decay. It's already implemented in `warm_start.py` for new agents added at runtime. Extend it to model-swap events. Detect model changes by comparing the Ollama model hash (available via `ollama list`) against a stored hash in `~/.mahoraga/agent_meta.json`.

---

### B2. Paper-Informed Additions

Five papers from the last 3 months that are directly on-topic. What to take from each:

**Calibration-Gated LLM Pseudo-Observations (Pershin et al., April 2026).** They augment LinUCB with counterfactual reward predictions for unplayed arms, reducing cold-start regret by 19%. Their approach uses LLM calls for the counterfactual estimates. Mahoraga can do this cheaper after A1: with 10K episodes in semantic memory, a k-NN regression over the embedding space predicts `reward(embedding, agent)` for any agent without an LLM call. This is the natural extension of A1 — call it A1.5 (counterfactual estimation from episodic memory). It directly addresses the "overridden agent gets no update" gap from the off-policy correction above. Build it after A1's benchmark proves semantic retrieval works.

**MixLLM (Feb 2026).** Tag-enhanced embeddings before routing: prepend semantic tags extracted from the task before encoding. Already incorporated into the A1 spec as a benchmark variant (`--embedding-mode={raw, tag-enhanced}`). The A4 brain integration extends this further: brain hits become tags.

**ParetoBandit (Taberner-Miller, March 2026).** Now open-source. Three relevant mechanisms: online primal-dual budget pacer (enforces dollar-denominated cost ceilings), geometric forgetting (Mahoraga already has this via γ=0.98), and hot-swap model registry (Mahoraga partially has this via AdapterRegistry). The budget pacer is the missing piece. Implementation: a `MAHORAGA_BUDGET_CEILING` env var (e.g., `0.05` = $0.05/task average), tracked via a running average in the reward function. When approaching the ceiling, the cost weight in the composite reward increases, pushing the bandit toward free agents. ~50 lines of code. ParetoBandit's Apache 2.0 repo has reference implementation. Build after the escalation gateway, since escalation is the primary cost driver.

**CQB-MNL — Contextual Queueing Bandits (Bae et al., Feb 2026).** Joint routing and scheduling using retrial-based implicit feedback. The retrial signal is already captured by Mahoraga's implicit quality tracker. The queueing theory becomes relevant when parallel execution lands — that dormant `queue_depth_norm` feature (context feature 9) finally gets a value. Not actionable now, but validates the placeholder.

**LLM Routing with Dueling Feedback (Oct 2025).** Pairwise preference feedback instead of scalar rewards. Mahoraga's implicit signals are binary (retry = bad, accept = good) which fits a dueling formulation more naturally than forcing them into the composite reward function. This is a deeper bandit-core change — replacing LinUCB with FGTS.CDB (Feel-Good Thompson Sampling for Contextual Dueling Bandits). Only consider this if the current LinUCB + importance weighting + semantic memory combination plateaus. It's a v3 question, not a v2 question.

**Priority order for paper-informed additions:**

1. Tag-enhanced embeddings (part of A1 benchmark, nearly free)
2. Budget pacer (from ParetoBandit, ~50 LoC, practical value)
3. Counterfactual estimation / A1.5 (requires A1 to land first)
4. Queue-aware routing (requires parallel execution, not yet implemented)
5. Dueling bandit formulation (v3, requires bandit-core rewrite)

---

### B3. Environment Changes

**Hardware.** MacBook Pro M-series, 16GB unified memory. This is the floor for 2026 — 8GB is no longer viable for local AI. The 16GB constraint shapes every decision: model sizes, HNSW RAM, embedding model footprint. The 1TB portable LaCie drive is a storage extension, not a compute extension — useful for archival data, Obsidian vault indexing, and large episode histories, but the drive's connection/disconnection cycle means it can't host anything the routing system depends on in real-time.

**Memory budget (16GB):**

| Component | RAM | Notes |
|-----------|-----|-------|
| macOS | ~3 GB | Baseline |
| Ollama + Qwen 3.5 9B (Q4_K_M) | ~6.6 GB | Primary local model |
| MiniLM embedding model | ~90 MB | Lazy-loaded, stays resident |
| HNSW index (dim=384, 10K episodes) | ~50 MB | Episodic memory |
| LRU + SQLite caches | ~5 MB | Embedding cache |
| FastAPI server + Python | ~200 MB | Routing service |
| **Total** | **~10 GB** | **~6 GB headroom** |

The 6GB headroom is enough to load a second Ollama model (Gemma 4 E4B at ~3GB) for double-run escalation. It's not enough to run both simultaneously under sustained load. If parallel execution lands, model switching latency (cold-load a model when needed) is the constraint, not RAM. The spawn penalty in the reward function already penalises cold-load agents — this is the mechanism that handles it.

**Inference runtime.** Ollama remains the default. MLX is faster on Apple Silicon (no CPU-GPU data copying) but doesn't expose an OpenAI-compatible API that the Ollama adapter talks to. If MLX gets a compatible server mode, switching the backend is a config change — the adapter interface abstracts it. Monitor `mlx-lm` for API server support.

**Cloud agent state (May 2026):**

- Gemini CLI v0.40.1. Context compression, project-scoped memory, Plan Mode, browser agent. 1000 free requests/day. Actively developed (Google Summer of Code 2026).
- Codex CLI: rebuilt in Rust. Faster startup, MCP support, kernel sandboxing. Currently free for ChatGPT Free users (no published end date — treat as temporary).
- Claude Code: 80.9% SWE-bench, Agent Teams for multi-agent coordination. Not used directly as a routed agent (it's the escalation target via API), but its capabilities set the quality ceiling.
- OpenCode: v0.48+, 75+ providers, LSP integration. Not in the default roster but available via adapter registration. Worth watching for the LSP feature — if it exposes type diagnostics to the routing context, that's a quality signal the 9-dim vector could capture.

**All four major CLIs have converged on sub-agent architecture.** They all support MCP, subagents, and parallel task decomposition. This convergence means the *agent interface* is increasingly standardised — Mahoraga's adapter abstraction was the right call. The differentiators are now model quality, context window, cost, and tool access — exactly the features the bandit learns to route on.

---

### B4. The Research Story

v1's story: "Online contextual bandit routing across heterogeneous agents with episodic memory."

v2's story adds three layers that no existing paper combines:

1. **Semantic episodic memory** (A1). HNSW over 384-dim sentence embeddings of past task descriptions. No paper in the routing literature uses episode-level semantic retrieval to bias bandit selection. RouteLLM, BaRP, MixLLM, CQB-MNL — they all learn from the bandit update rule alone. Mahoraga learns from the bandit AND from retrieved experience.

2. **Importance-weighted off-policy correction** with composer overrides (this document, A1). The composer is a bridge between "bandit hasn't converged" and "trained model knows." The importance weighting ensures the bandit learns correctly from overridden decisions. No existing system combines a contextual bandit with a learned override layer and off-policy correction.

3. **Tag-enhanced embeddings with project context** (A1 + A4). Brain/journal hits flow into the embedding as tags, making episodic retrieval project-aware. "Fix the race condition" in a PostgreSQL project retrieves different episodes than "fix the race condition" in a Redis project. No paper addresses project-level context as a routing feature.

The combination is what's novel: semantic memory × off-policy learning × project-context embedding, all running online, all local-first, across heterogeneous agents spanning CLI tools, local models, and cloud APIs.

**What to cite in the updated README (additions to Related Work):**

```
Pershin, M., et al. (2026).
  Calibration-Gated LLM Pseudo-Observations for Online Contextual Bandits.
  arXiv:2604.14961.

Wang, W., et al. (2025).
  MixLLM: Dynamic Routing in Mixed Large Language Models.
  arXiv:2502.18482.

Bae, S., Son, J., & Lee, D. (2026).
  Learning to Route and Schedule LLMs from User Retrials
  via Contextual Queueing Bandits.
  arXiv:2602.02061.

Bhatti, A., Vaddina, V., & Birru, D. (2026).
  PROTEUS: SLA-Aware Routing via Lagrangian RL
  for Multi-LLM Serving Systems.
  arXiv:2601.19402.
```

Update BaRP's status: still under review at ICLR 2026 main conference. ParetoBandit's citation should be updated with the full paper (arXiv:2604.00136, March 2026) and the open-source repo.

---

### B5. Build Order

Everything in Part A and the first items in Part B, sequenced by dependency:

```
Phase 0 (now):
  ├─ A1: Off-policy correction (importance-weighted updates)
  ├─ A5: Bucket scoring audit
  └─ Agent roster update (Qwen 3.5 9B, trim to 5 agents)

Phase 1 (A1 — semantic routing):
  ├─ Embedding service + cache
  ├─ Episodic memory upgrade (dim=384)
  ├─ Router integration + backfill
  ├─ Tag-enhanced embedding variant
  └─ Benchmark (standard + adversarial, 10 seeds)

Phase 2 (escalation + quality):
  ├─ A2: Escalation gateway (3 strategies)
  ├─ A3: Auto-retrain schedule
  └─ Budget pacer (from ParetoBandit)

Phase 3 (brain + counterfactual):
  ├─ A4: Brain context → embedding tags
  └─ A1.5: Counterfactual estimation from episodic memory

Phase 4 (parallel execution — future):
  ├─ asyncio.gather for concurrent tasks
  ├─ queue_depth_norm becomes live
  └─ CQB-MNL queueing theory applies
```

Phase 0 is cleanup and re-baselining. Phase 1 is the core v2 contribution. Phase 2 is operational hardening. Phase 3 is the intelligence layer that makes Mahoraga uniquely capable. Phase 4 is the next major architectural change and a v3 question.