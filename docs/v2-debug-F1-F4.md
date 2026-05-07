# Mahoraga v2 — Next Phase Spec

**Date:** 2026-05-07
**Status:** Ready to build
**Prereqs:** A1 semantic routing spec, A2/A3/A4 shipped as read-only signals, composer in shadow mode, implicit feedback writing to DB, 630+ tests green, 6300+ LoC this cycle.

---

## Current Stack (What Exists)

```
┌─────────────────────────────────────────────┐
│ /mahoraga skill (Claude Code prompt-level)  │  decides WHEN to delegate
├─────────────────────────────────────────────┤
│ MCP server (backend/mcp/server.py)          │  protocol bridge, 9 tools
├─────────────────────────────────────────────┤
│ Mahoraga backend (FastAPI on :8000)         │  routes + executes + learns
└─────────────────────────────────────────────┘
```

**MCP tools surfaced:** `run_task`, `run_batch`, `route_task`, `routing_stats`, `recent_decisions`, `switch_strategy`, `switch_routing_mode`, `agent_status`, `health_check`.

**What the skill does:** redirects subtask execution from "Claude pays Anthropic per token" to "Qwen 3.5 9B locally + codex-cli + gemini-cli free tier, picked by LinUCB." The big primary call still goes through Claude; the leaves don't.

**What's shipped and observing (not yet acting):**

| Module | File | Status |
|--------|------|--------|
| A1 off-policy correction | `routing/bandit_router.py` | Specced, importance weighting designed |
| A2 uncertainty/escalation | `routing/uncertainty.py` | `should_escalate` computed, no gateway hook |
| A3 quality predictor | `routing/quality_predictor.py` | Trains/evals, not in reward path, 0 labelled rows |
| A4 brain retrieval | `routing/brain_retrieval.py` | Retrieves, doesn't bias agent picks |
| Composer | `routing/composer.py` | Shadow mode, logs decisions, doesn't override |
| Implicit feedback | `routing/implicit_quality.py` | Writing to `routing_decisions.db` |
| Security scoring | `routing/quality_scorer.py` | `_score_security` differentiated, no longer flat 0.65 |
| Semantic embeddings | `routing/embeddings.py` | MiniLM-L6-v2 service, cache, encode/batch |
| Episodic memory | `routing/episodic_memory.py` | HNSW dim=384 upgrade specced, 9-dim live |

**Hardware:** MacBook Pro M-series, 16 GB unified. Qwen 3.5 9B Q4_K_M (~6.6 GB) as local default. ~6 GB headroom.

**Agent roster (v2, trimmed from 8 → 5):**

| Agent | Role | Cost | RAM |
|-------|------|------|-----|
| ollama: Qwen 3.5 9B | Code, refactor, general | Free | ~6.6 GB |
| ollama: Gemma 4 E4B | Plan, research, general | Free | ~3 GB |
| gemini-cli | Research, long-context | Free (1K req/day) | — |
| codex-cli | Sandboxed code | Free (for now) | — |
| claude API | Escalation target | Per-token | — |

---

## Three Improvement Loops

Everything in this spec maps to one of three loops. Understanding which loop a feature belongs to is how you know what it's actually for.

**Loop 1 — Online learning (already wired).** Every task → outcome → bandit/memory/learner update. Parameter-level improvement. The model's θ refines with each observation. This is what makes Mahoraga "adapt" today. Necessary but ceiling-bound: LinUCB can only become a better LinUCB.

**Loop 2 — Component calibration (partially wired).** A3 retrains on schedule. Reward weights adapt via OLS. Composer thresholds tune from shadow data. Budget pacer self-corrects via λ. Counterfactual has a calibration gate with auto-block. The supporting models stay current. Missing piece before this spec: every component should have an objective function measured continuously, with auto-rollback when a retrain regresses.

**Loop 3 — Evolutionary (this spec starts building it).** The system itself evolves. Drift detection catches agent degradation before the bandit slowly notices. Auto-quarantine routes around broken agents without human intervention. Champion/challenger tests config changes with statistical evidence. Episode replay answers counterfactuals without re-running traffic. This is the difference between "a tool that learns" and "a tool that gets structurally better at being a tool."

**The identity claims, in order of visibility:**

1. It works invisibly — toggle it on, it routes, it saves money, it escalates when worried. *(Today.)*
2. It improves continuously — every session leaves it better than it found it. *(Loops 1+2, partially today, full with F1-F4.)*
3. It adapts structurally — quarantines bad agents, detects drift, replays history under new configs. *(Loop 3 — the v2 differentiator.)*

---

## Build Order

Six features, sequenced by dependency. F1-F4 are the infrastructure. F5 is the cheapest Loop 3 win. L3 is the evolutionary framework that the data from F1-F5 enables.

```
1. Budget Pacer           (~50 LoC, unblocks safe escalation)
     │
2. Parallel Batch         (medium scope, biggest user-visible win)
     │
3. Counterfactual Est.    (paper-worthy, needs episode data from 1+2)
     │
4. Composer Flip          (needs shadow data + counterfactual from 3)
     │
5. Drift Detection +      (~70 LoC, biggest trust win relative to effort,
   Auto-Quarantine         makes "toggle and forget" actually safe)
     │
L3. Evolutionary Layer    (champion/challenger, episode replay, post-hoc
                           analysis — needs data depth from F1-F5)
```

Budget pacer goes first — it's the guard rail that makes everything else safe to turn on. Without it, enabling escalation or parallel cloud runs risks unbounded cost. Parallel batch is the biggest user-visible win and also multiplies data collection (two agents per task = 2x episodes = faster convergence for counterfactual estimation). Counterfactual estimation needs the episode data that parallel batch generates. Composer flip needs counterfactual to evaluate whether the composer's picks would have been better. Drift detection + auto-quarantine is cheap and can be built alongside R1 reliability hardening — it's what makes the system trustworthy enough to leave running unattended. The evolutionary layer (L3) is the meta-framework that uses all the data F1-F5 generate to make the system structurally better over time.

---

## F1. Budget Pacer

### Problem

Mahoraga's cost management is implicit: the reward function penalises cost, so the bandit learns to prefer free agents. But there's no hard constraint. If the bandit decides Claude is optimal for 10 tasks in a row, the user eats the cost. Escalation (A2) can't be safely enabled without a budget ceiling.

### Design

Adapted from ParetoBandit's online primal-dual budget pacer (Apache 2.0 reference implementation available). The core idea: a Lagrange multiplier λ dynamically adjusts the cost weight in the composite reward function to enforce a per-task average cost ceiling.

**Env config:**

```bash
MAHORAGA_BUDGET_CEILING=0.05       # USD per task average. 0 = no paid agents.
MAHORAGA_BUDGET_WINDOW=100         # rolling window for average calculation
MAHORAGA_BUDGET_HARD_LIMIT=0.50    # absolute per-task cap. never exceeded.
```

**State:**

```python
@dataclass
class BudgetPacer:
    ceiling: float          # target per-task average cost (from env)
    window: int             # rolling window size
    hard_limit: float       # absolute per-task cap
    spent: deque[float]     # last `window` task costs
    lambda_: float = 0.0    # Lagrange multiplier, starts relaxed
    eta: float = 0.01       # learning rate for dual update
```

**Dual update (after each task):**

```python
def update(self, task_cost: float):
    self.spent.append(task_cost)
    if len(self.spent) > self.window:
        self.spent.popleft()
    avg_cost = sum(self.spent) / len(self.spent)
    # gradient ascent on the dual variable
    self.lambda_ = max(0.0, self.lambda_ + self.eta * (avg_cost - self.ceiling))
```

**Integration with reward function:**

The composite reward is currently `r = w₁·success + w₂·quality + w₃·speed + w₄·cost` where `w₄·cost` is a fixed penalty. The pacer modifies the cost weight dynamically:

```python
def adjusted_cost_weight(self, base_cost_weight: float) -> float:
    return base_cost_weight + self.lambda_
```

When `avg_cost < ceiling`, λ stays near 0 and the bandit is free to pick expensive agents when quality justifies it. When `avg_cost → ceiling`, λ grows and the cost penalty dominates, forcing the bandit toward free agents. The hard limit is a separate check — if a candidate agent's estimated cost exceeds `hard_limit`, it's excluded from the arm set for that task entirely.

**Pre-selection filter:**

```python
def filter_arms(self, candidates: list[AgentAdapter], task_cost_estimates: dict[str, float]) -> list[AgentAdapter]:
    """Remove agents that would exceed the hard limit."""
    if self.hard_limit <= 0:
        return [c for c in candidates if task_cost_estimates.get(c.name, 0.0) == 0.0]
    return [c for c in candidates if task_cost_estimates.get(c.name, 0.0) <= self.hard_limit]
```

**Cost estimation:** Each adapter already declares `cost_usd` in its capabilities. For cloud agents, estimate per-task cost from historical mean in `routing_decisions.db`. For local agents, cost is 0.0.

### Files

| File | Change |
|------|--------|
| `routing/budget_pacer.py` | **New.** `BudgetPacer` class, ~60 LoC. |
| `routing/reward_learner.py` | Modify `compute_reward()` to accept `cost_weight_adjustment` from pacer. |
| `routing/bandit_router.py` | Instantiate pacer, call `filter_arms()` before selection, call `update()` after observation. |
| `config.py` or env loading | Read `MAHORAGA_BUDGET_*` env vars. |
| `cli/commands/budget.py` | **New.** `orch budget status` — prints ceiling, avg cost, λ, window. |
| `tests/test_budget_pacer.py` | **New.** See acceptance criteria. |

### Acceptance Criteria

1. With `BUDGET_CEILING=0.0`, no paid agent is ever selected. Verify over 100 simulated tasks.
2. With `BUDGET_CEILING=0.05`, paid agents are selected when quality justifies it but average cost stays below $0.05/task over any 100-task window.
3. λ converges: after 200 tasks at steady state, λ should be stable (variance < 0.01 over last 50 updates).
4. Hard limit: with `BUDGET_HARD_LIMIT=0.10`, a task estimated at $0.15 never routes to a paid agent regardless of λ.
5. Graceful when no cost data: if `routing_decisions.db` has no cost history for an agent, assume cost = 0.0 for local agents and cost = $0.10 default for cloud agents.
6. `orch budget status` prints human-readable state.

### Tests (12 minimum)

- `test_pacer_zero_ceiling_blocks_paid` — ceiling=0, verify only free agents selected
- `test_pacer_dual_update_increases_lambda` — feed over-budget costs, verify λ rises
- `test_pacer_dual_update_decreases_lambda` — feed under-budget costs, verify λ falls toward 0
- `test_pacer_hard_limit_excludes_expensive` — agent cost > hard_limit, verify excluded
- `test_pacer_hard_limit_zero_means_free_only` — hard_limit=0, only free agents pass
- `test_pacer_rolling_window_evicts_old` — verify window respects FIFO
- `test_pacer_lambda_non_negative` — verify λ never goes below 0
- `test_pacer_cost_weight_adjustment` — verify adjusted weight = base + λ
- `test_pacer_convergence` — 500 tasks, mixed costs, verify avg stabilises near ceiling
- `test_pacer_empty_db_defaults` — no cost history, verify default estimates used
- `test_pacer_all_filtered_fallback` — if all agents exceed hard_limit, fall back to cheapest
- `test_pacer_persistence` — pacer state serialises/deserialises correctly across restarts

---

## F2. Parallel Batch Execution

### Problem

Tasks run one at a time. The README calls this out as a known limitation. `run_batch` exists as an MCP tool but internally loops sequentially. This blocks:

- Double-run escalation (A2 strategy 2) — currently 2x latency instead of 1x.
- Batch throughput — a 10-task batch from Claude Code takes 10× single-task latency.
- `queue_depth_norm` (context feature 9) — always 0.0 because there's no queue.
- Data collection velocity — parallel = 2 episodes per task = faster bandit convergence.

### Design

Three layers of parallelism, built incrementally:

**Layer 1: Batch-level parallelism (run_batch).** When `run_batch` receives N tasks, dispatch them concurrently with `asyncio.gather`. Each task still routes to one agent. This is the simplest win — the MCP tool already accepts a list of tasks.

**Layer 2: Double-run parallelism (escalation strategy 2).** When A2's escalation gateway fires with strategy "double_run", execute the bandit's pick AND the composer's preferred agent concurrently. Return the higher-quality output. Both outcomes are logged as separate episodes.

**Layer 3: Speculative parallelism (future, not in this phase).** Speculatively run top-2 agents for every task, cancel the slower one when the faster one passes quality. Too expensive on 16 GB for now.

### Architecture

```
run_batch(tasks=[t1, t2, t3])
    │
    ├─ Semaphore(max_concurrent=N)     # prevent OOM
    │
    ├─ asyncio.gather(
    │      execute_one(t1),
    │      execute_one(t2),
    │      execute_one(t3)
    │  )
    │
    └─ Each execute_one:
         ├─ route(task) → agent
         ├─ if should_escalate and strategy == "double_run":
         │      asyncio.gather(
         │          adapter_a.execute(task),
         │          adapter_b.execute(task)
         │      )
         │      pick winner by quality score
         │      log both as episodes
         └─ else:
              adapter.execute(task)
              log one episode
```

**Concurrency control:**

```python
@dataclass
class ExecutionPool:
    max_concurrent: int = 3             # env: MAHORAGA_MAX_CONCURRENT
    max_local: int = 1                  # only 1 Ollama model at a time on 16GB
    max_cloud: int = 3                  # cloud agents are network-bound, parallelise freely
    _semaphore: asyncio.Semaphore       # global cap
    _local_semaphore: asyncio.Semaphore # local model cap
```

The `max_local=1` constraint is the hardware reality: running Qwen 3.5 9B (6.6 GB) and Gemma 4 E4B (3 GB) simultaneously on 16 GB leaves ~6.4 GB for macOS + Python + everything else. It works but under sustained load, OOM kills are likely. Safer to serialize local model access and parallelise only cloud agents, or local + cloud pairs.

**Queue depth tracking:**

```python
class QueueTracker:
    _active: int = 0
    _lock: asyncio.Lock

    async def acquire(self) -> int:
        async with self._lock:
            self._active += 1
            return self._active

    async def release(self):
        async with self._lock:
            self._active -= 1

    @property
    def depth(self) -> int:
        return self._active
```

This feeds into `queue_depth_norm` (context feature 9): `min(1.0, active_tasks / max_concurrent)`. The bandit now sees contention state — when the queue is deep, it can learn to prefer faster agents to reduce latency.

**Double-run episode logging:**

When two agents run in parallel on the same task, both get logged as full episodes with their real rewards. The bandit learns from both. The agent that wasn't selected by the bandit gets logged with `override_reason="double_run"` and `importance_weight` computed from the off-policy correction (A1 spec). This is the data multiplier — every double-run teaches the bandit about two agents instead of one.

```python
# After double-run completes:
for agent, result in [(agent_a, result_a), (agent_b, result_b)]:
    reward = compute_reward(result, task, budget_pacer)
    episode = Episode(
        task_hash=task.hash,
        bandit_pick=bandit_agent.name,
        final_pick=agent.name,
        reward=reward,
        quality_score=result.quality,
        override_reason="double_run" if agent != bandit_agent else None,
        importance_weight=compute_importance_weight(agent, bandit_probs),
        # ... other fields
    )
    bandit.observe(episode)
    episodic_memory.store(episode)
```

### Timeout and Error Handling

```python
TASK_TIMEOUT_S = int(os.getenv("MAHORAGA_TASK_TIMEOUT", "120"))

async def execute_with_timeout(adapter, task):
    try:
        result = await asyncio.wait_for(
            adapter.execute(task),
            timeout=TASK_TIMEOUT_S
        )
        return result
    except asyncio.TimeoutError:
        return TaskResult(
            success=False,
            error=f"Timeout after {TASK_TIMEOUT_S}s",
            agent=adapter.name,
            execution_time_ms=TASK_TIMEOUT_S * 1000
        )
    except Exception as e:
        return TaskResult(
            success=False,
            error=str(e),
            agent=adapter.name,
            execution_time_ms=0
        )
```

**Timeout reward:** A timeout is a clear negative signal. Reward = 0.0, same as a failure. The bandit learns to avoid slow agents on time-sensitive tasks. The spawn penalty in the existing reward function already penalises cold-load latency — the timeout adds a hard ceiling.

**Partial batch failure:** If 3/5 tasks in a batch succeed and 2 fail/timeout, return the 3 successes immediately and report failures individually. The MCP response should include per-task status so Claude Code knows which subtasks need retry.

```python
@dataclass
class BatchResult:
    results: list[TaskResult]       # one per input task, in order
    total_time_ms: int
    succeeded: int
    failed: int
    timed_out: int

    @property
    def partial_success(self) -> bool:
        return 0 < self.succeeded < len(self.results)
```

### Streaming in Parallel

Current `run_task` streams the agent's response token-by-token via SSE. In parallel batch mode, interleaved streaming from multiple agents would be chaotic. Two options:

**Option A — Buffer and return.** Each parallel task buffers its full response, then the batch result returns all responses at once. Simpler, but the caller (Claude Code) sees nothing until all tasks complete. For short tasks (the typical subtask delegation), this is fine — 5-15s total wait.

**Option B — Per-task streaming with task IDs.** Each SSE event includes a `task_id` field. The MCP client demuxes by task ID. More complex, but the user sees incremental progress. Only worth it if batch tasks are long-running.

**Recommendation: Option A for v1 of parallel batch.** The typical MCP `run_batch` call delegates small subtasks that complete in seconds. Buffering is acceptable. If we find that batch tasks are regularly >30s, revisit with Option B.

### MCP Tool Changes

The `run_batch` MCP tool signature stays the same — it already accepts a list of tasks. The change is internal: sequential loop → `asyncio.gather`. Add optional parameters:

```python
@mcp_tool("run_batch")
async def run_batch(
    tasks: list[str],
    max_concurrent: int | None = None,   # override global default
    timeout_per_task: int | None = None,  # override global default
    allow_double_run: bool = False,       # enable A2 strategy 2 for uncertain tasks
) -> BatchResult:
```

### Files

| File | Change |
|------|--------|
| `routing/execution_pool.py` | **New.** `ExecutionPool`, `QueueTracker`, semaphore management. ~120 LoC. |
| `routing/executor.py` or equivalent | Refactor `execute_task()` to be async-safe. Add `execute_batch()` with `asyncio.gather`. |
| `routing/bandit_router.py` | Feed `queue_depth_norm` from `QueueTracker.depth`. |
| `routing/context.py` | Update feature 9 computation: `min(1.0, queue_depth / max_concurrent)`. |
| `mcp/server.py` | Update `run_batch` handler to use `execute_batch()`. Add optional params. |
| `mcp/server.py` | Update `run_task` to integrate with `ExecutionPool` (acquire/release semaphore). |
| `tests/test_execution_pool.py` | **New.** |
| `tests/test_parallel_batch.py` | **New.** |

### Acceptance Criteria

1. `run_batch(["task1", "task2", "task3"])` completes in ~max(individual_times) instead of sum(individual_times). Verify with 3 cloud tasks (network-bound, should parallelise well).
2. `max_local=1` enforced: two local-model tasks in a batch run sequentially. Verify with timing.
3. Local + cloud in same batch: local task and cloud task run concurrently. Verify with timing.
4. Timeout: a task that hangs for >120s returns a failure result without blocking other tasks.
5. Partial failure: 3/5 tasks succeed → BatchResult has succeeded=3, failed=2, and the 3 successful results are accessible.
6. Queue depth: during a 3-task batch, `queue_depth_norm` reads ~1.0 (3/3). After completion, reads 0.0.
7. Double-run: with `allow_double_run=True` and a task that triggers `should_escalate`, two agents run concurrently and both episodes are logged.
8. OOM guard: `max_concurrent` respected even under burst load (rapid sequential `run_batch` calls).
9. Backwards compatible: `run_task` (single task) still works identically. No regression.

### Tests (15 minimum)

- `test_pool_semaphore_limits_concurrency` — 5 tasks, max_concurrent=2, verify only 2 run at once
- `test_pool_local_semaphore_serialises` — 2 local tasks, verify sequential execution
- `test_pool_cloud_parallelises` — 3 cloud tasks, verify concurrent execution
- `test_pool_mixed_local_cloud` — 1 local + 2 cloud, verify local serial + cloud parallel
- `test_batch_returns_all_results` — N tasks in, N results out, in order
- `test_batch_partial_failure` — 2/3 succeed, verify BatchResult counts
- `test_batch_timeout_doesnt_block` — 1 task hangs, 2 complete normally
- `test_batch_timeout_reward_zero` — timed out task gets reward 0.0
- `test_queue_depth_tracks_active` — verify depth goes up on acquire, down on release
- `test_queue_depth_norm_feeds_context` — verify feature 9 reflects queue state during execution
- `test_double_run_logs_both_episodes` — verify 2 episodes stored with correct metadata
- `test_double_run_picks_higher_quality` — verify the better result is returned to caller
- `test_double_run_importance_weights` — verify off-policy weights on non-bandit-pick episode
- `test_single_task_still_works` — run_task regression test
- `test_batch_empty_input` — empty task list returns empty BatchResult, no crash

---

## F3. Counterfactual Estimation

### Problem

The bandit only learns from the agent it selected. If Qwen handles 90% of tasks, the bandit accumulates almost no data about Gemini, Codex, or Claude on those task types. This creates a self-reinforcing loop: the bandit picks Qwen because it has the most data for Qwen, which generates more Qwen data, which makes it pick Qwen more. Exploration via UCB helps but is slow — α√(x' A⁻¹ x) only explores when variance is high, and variance drops as observations accumulate even if they're all from one agent.

Counterfactual estimation breaks this loop by predicting "what would agent X have scored on this task?" for agents that weren't selected. These predictions become pseudo-observations that keep the bandit informed about all agents, not just the one that ran.

### Related Work

The Calibration-Gated paper (Pershin et al., April 2026) does this with LLM calls — after each task, they prompt an LLM to predict counterfactual rewards for unplayed arms. Key findings:

- With task-specific prompts, counterfactual pseudo-observations reduced cumulative regret by 19%.
- Generic prompting *increased* regret on both test environments.
- Prompt design dominates all other hyperparameters.

Mahoraga's advantage: we don't need LLM calls. After A1 lands, the episodic memory has 10K+ episodes with `(semantic_embedding, agent_id, reward)`. That's a training set for k-NN regression. The counterfactual estimate comes from retrieved experience, not generated predictions. Zero cost, zero latency, and the estimates improve as the episode store grows.

### Design

**Counterfactual estimator:** A lightweight model that predicts `reward(embedding, agent)` for any (task, agent) pair.

```python
class CounterfactualEstimator:
    """Predicts reward for (task, agent) pairs using episode history."""

    def __init__(self, episodic_memory: EpisodicMemory, k: int = 20):
        self.memory = episodic_memory
        self.k = k

    def estimate(self, task_embedding: np.ndarray, agent_id: str) -> CounterfactualEstimate | None:
        """Predict what `agent_id` would score on a task with this embedding."""
        # Retrieve k nearest episodes
        neighbors = self.memory.query_semantic(task_embedding, k=self.k)

        # Filter to episodes where this specific agent ran
        agent_episodes = [ep for ep in neighbors if ep.agent_id == agent_id]

        if len(agent_episodes) < 3:
            # Not enough data for a reliable estimate
            return None

        # Distance-weighted mean reward
        rewards = np.array([ep.reward for ep in agent_episodes])
        distances = np.array([ep.distance for ep in agent_episodes])  # cosine distance from query

        # Inverse distance weighting: closer episodes matter more
        weights = 1.0 / (distances + 1e-6)
        weights /= weights.sum()

        estimated_reward = float(np.dot(weights, rewards))

        # Confidence: based on number of agent-specific neighbors and their distance spread
        confidence = min(1.0, len(agent_episodes) / self.k) * (1.0 - np.mean(distances))

        return CounterfactualEstimate(
            agent_id=agent_id,
            estimated_reward=estimated_reward,
            confidence=confidence,
            n_neighbors=len(agent_episodes),
            mean_distance=float(np.mean(distances)),
        )

    def estimate_all(self, task_embedding: np.ndarray, agents: list[str]) -> dict[str, CounterfactualEstimate]:
        """Estimate reward for all agents on this task."""
        return {
            agent: est
            for agent in agents
            if (est := self.estimate(task_embedding, agent)) is not None
        }
```

**Pseudo-observation injection:**

After each task completes and the real episode is logged, the estimator predicts counterfactual rewards for all agents that didn't run. High-confidence estimates are injected into the bandit as pseudo-observations with a decayed weight.

```python
def inject_counterfactuals(
    self,
    task_embedding: np.ndarray,
    actual_agent: str,
    all_agents: list[str],
    bandit: BanditRouter,
    context_vector: np.ndarray,
    decay: float = 0.3,               # pseudo-obs weight relative to real obs
    confidence_threshold: float = 0.5, # minimum confidence to inject
):
    estimates = self.estimate_all(task_embedding, all_agents)

    for agent_id, est in estimates.items():
        if agent_id == actual_agent:
            continue  # real observation already logged
        if est.confidence < confidence_threshold:
            continue  # not enough data to trust this estimate

        # Weight decays with uncertainty and increases with data
        weight = decay * est.confidence

        # Inject as a weighted pseudo-observation
        bandit.observe_pseudo(
            agent_id=agent_id,
            context=context_vector,
            reward=est.estimated_reward,
            weight=weight,
        )
```

**Bandit-side pseudo-observation:**

```python
# In BanditRouter or LinUCB strategy:
def observe_pseudo(self, agent_id: str, context: np.ndarray, reward: float, weight: float):
    """Update A and b with a weighted pseudo-observation.

    A_a ← A_a + weight * (x x')
    b_a ← b_a + weight * reward * x

    When weight=1.0, this is identical to a real observation.
    When weight=0.3, the update is 30% as strong.
    """
    x = context.reshape(-1, 1)
    strategy = self._get_strategy(agent_id)
    strategy.A += weight * (x @ x.T)
    strategy.b += weight * reward * x.flatten()
```

This is mathematically equivalent to seeing `weight` fraction of a real observation. The A matrix grows more slowly (less certainty), the b vector shifts proportionally (less reward signal). The UCB exploration bonus shrinks more slowly than with real observations, which is correct — we're less certain about counterfactual estimates.

### Calibration Gate

The Calibration-Gated paper's key insight: uncalibrated counterfactuals are worse than none. We need a gate.

**Calibration check:** After accumulating 200+ real episodes, split into train/test. For the test episodes, retroactively compute the counterfactual estimate (using only train episodes as neighbors) and compare to the actual observed reward. If the mean absolute error is < 0.15, the estimator is calibrated and injection is safe.

```python
class CalibrationGate:
    min_episodes: int = 200
    max_mae: float = 0.15
    recalibrate_every: int = 100  # re-check every 100 new episodes

    def check(self, episodic_memory: EpisodicMemory) -> CalibrationResult:
        episodes = episodic_memory.get_all()
        if len(episodes) < self.min_episodes:
            return CalibrationResult(calibrated=False, reason="insufficient_data", mae=None)

        # 80/20 split, chronological (no leakage)
        split = int(len(episodes) * 0.8)
        train, test = episodes[:split], episodes[split:]

        # Build temporary estimator on train set
        temp_memory = EpisodicMemory()
        for ep in train:
            temp_memory.store(ep)
        estimator = CounterfactualEstimator(temp_memory)

        # Evaluate on test set
        errors = []
        for ep in test:
            est = estimator.estimate(ep.embedding, ep.agent_id)
            if est is not None:
                errors.append(abs(est.estimated_reward - ep.reward))

        if len(errors) < 20:
            return CalibrationResult(calibrated=False, reason="insufficient_testable", mae=None)

        mae = np.mean(errors)
        return CalibrationResult(
            calibrated=(mae < self.max_mae),
            reason="calibrated" if mae < self.max_mae else f"mae={mae:.3f} > {self.max_mae}",
            mae=mae,
        )
```

**Activation flow:**

```
Episode logged → CalibrationGate.check() every 100 episodes
    │
    ├─ Not calibrated → log warning, skip injection, continue accumulating
    │
    └─ Calibrated → CounterfactualEstimator.inject_counterfactuals()
                     for every new task going forward
```

### Interaction with Budget Pacer

Counterfactual estimates should respect the budget pacer. If the pacer has filtered out a paid agent from the arm set, don't inject pseudo-observations for that agent — the bandit shouldn't learn to prefer an agent it can't select. Pass the pacer's filtered arm set to `inject_counterfactuals()`.

### Interaction with Parallel Batch

Double-run (F2) produces real observations for two agents per task. When a double-run happens, the counterfactual estimator still runs for the remaining agents (the ones that didn't run in either slot). This means a double-run with 5 total agents produces: 2 real episodes + up to 3 pseudo-episodes = the bandit learns about all 5 agents from one task. This is the data velocity win.

### Files

| File | Change |
|------|--------|
| `routing/counterfactual.py` | **New.** `CounterfactualEstimator`, `CalibrationGate`, `CounterfactualEstimate` dataclass. ~200 LoC. |
| `routing/bandit_router.py` | Add `observe_pseudo()` method to LinUCB strategy. Wire counterfactual injection after each `observe()`. |
| `routing/episodic_memory.py` | Add `get_all()` method for calibration (or iterate existing store). |
| `cli/commands/counterfactual.py` | **New.** `orch counterfactual status` — calibration state, MAE, episode count. `orch counterfactual evaluate` — run calibration check on demand. |
| `tests/test_counterfactual.py` | **New.** |

### Env Config

```bash
MAHORAGA_COUNTERFACTUAL_ENABLED=1       # default: 0 (off until calibrated)
MAHORAGA_COUNTERFACTUAL_DECAY=0.3       # pseudo-obs weight
MAHORAGA_COUNTERFACTUAL_CONFIDENCE=0.5  # minimum confidence to inject
MAHORAGA_COUNTERFACTUAL_K=20            # neighbors for estimation
MAHORAGA_COUNTERFACTUAL_MIN_EPISODES=200  # minimum before calibration check
MAHORAGA_COUNTERFACTUAL_MAX_MAE=0.15    # calibration threshold
```

### Acceptance Criteria

1. With 500 synthetic episodes (known rewards), counterfactual estimates for held-out tasks have MAE < 0.15.
2. Calibration gate blocks injection when < 200 episodes exist.
3. Calibration gate blocks injection when MAE > 0.15 (e.g., on random/adversarial episode data).
4. Pseudo-observations produce smaller A/b updates than real observations (weight < 1.0).
5. After 1000 episodes with counterfactual injection, the bandit has non-trivial A/b matrices for ALL agents, not just the frequently-selected ones.
6. Budget pacer integration: filtered-out agents don't receive pseudo-observations.
7. Double-run integration: agents that ran in a double-run don't receive pseudo-observations (they have real data).
8. `orch counterfactual status` prints calibration state, MAE, last check timestamp, episode count.

### Tests (14 minimum)

- `test_estimate_basic` — known episodes, verify weighted mean reward is correct
- `test_estimate_distance_weighting` — closer episodes weighted more than distant ones
- `test_estimate_insufficient_data` — <3 agent-specific neighbors → returns None
- `test_estimate_all_agents` — verify estimates for all agents in roster
- `test_pseudo_observe_updates_Ab` — verify A and b change by weight fraction
- `test_pseudo_observe_weight_zero` — weight=0 produces no change
- `test_calibration_gate_insufficient_episodes` — <200 episodes → not calibrated
- `test_calibration_gate_calibrated` — good predictions → calibrated=True
- `test_calibration_gate_uncalibrated` — bad predictions → calibrated=False
- `test_injection_skips_actual_agent` — the agent that ran is not injected
- `test_injection_skips_low_confidence` — confidence < threshold → skip
- `test_injection_respects_budget_filter` — filtered agents not injected
- `test_injection_respects_double_run` — double-run agents not injected
- `test_end_to_end_all_agents_learn` — after 500 eps + injection, all agents have non-trivial matrices

---

## F4. Composer Flip

### Problem

The composer runs in shadow mode — it observes the bandit's picks and logs what it would have done differently, but never actually overrides. The shadow data is accumulating. The question is: when is it safe to flip the composer from "shadow" to "active"?

### What the Composer Does (Recap)

The composer sits between the bandit and the executor. It takes the bandit's pick and three additional signals:

- **A2 (uncertainty):** posterior variance of the selected arm, decision gap (top1 − top2 UCB).
- **A3 (quality predictor):** P(success) for the bandit's pick vs. alternative agents.
- **A4 (brain retrieval):** relevant brain entries that might bias toward a specific agent.

In shadow mode, it logs: `bandit_pick`, `composer_would_pick`, `override_reason`, `a3_predictions`, `brain_hit_count`. It doesn't change the routing decision.

### Flip Criteria

The composer should go active when the shadow data shows it would have performed better than the bandit alone. Specifically:

**Primary metric: counterfactual cumulative reward.**

After F3 (counterfactual estimation) lands, we can retroactively compute: "if the composer had been active for the last 200 tasks, what would cumulative reward have been?" This requires:

1. For tasks where `composer_would_pick == bandit_pick`: reward is identical (composer agreed).
2. For tasks where `composer_would_pick != bandit_pick`: use the counterfactual estimator to predict the composer's pick's reward.

Compare:
- `actual_cumulative_reward` = sum of real rewards from bandit picks
- `composer_counterfactual_reward` = sum of (real rewards where agreed) + (counterfactual estimates where disagreed)

If `composer_counterfactual_reward > actual_cumulative_reward` by a statistically significant margin (e.g., paired t-test, p < 0.05, over 200+ tasks where the composer disagreed), the composer is outperforming the bandit and should be activated.

**Secondary metric: override hit rate.**

For the subset of tasks where the composer disagreed with the bandit AND a double-run happened (from F2), we have real reward data for both agents. The "override hit rate" = fraction of disagreements where the composer's pick scored higher. If override hit rate > 0.60 over 50+ double-run disagreements, the composer's judgment is reliable.

### Activation Ramp

Don't flip 0% → 100%. Use a ramp:

```python
@dataclass
class ComposerActivation:
    mode: str = "shadow"              # shadow | ramp | active
    ramp_rate: float = 0.0            # fraction of tasks where composer overrides
    ramp_step: float = 0.10           # increase per evaluation window
    eval_window: int = 100            # tasks between ramp evaluations
    min_improvement: float = 0.02     # minimum reward improvement to continue ramp
    tasks_since_last_eval: int = 0
    ramp_history: list[RampEvaluation] = field(default_factory=list)
```

**Ramp schedule:**

```
shadow → ramp(10%) → evaluate → if improved → ramp(20%) → ... → ramp(100%) = active
                   → if not improved → ramp back to previous rate
                   → if degraded → revert to shadow
```

At each ramp step, the composer overrides the bandit's pick with probability `ramp_rate`. After `eval_window` tasks, compare cumulative reward of composer-overridden tasks vs. bandit-only tasks. If the composer's tasks averaged higher reward, increase the ramp. If not, decrease or revert.

```python
def evaluate_ramp(self, recent_episodes: list[Episode]) -> RampDecision:
    overridden = [ep for ep in recent_episodes if ep.override_reason is not None]
    not_overridden = [ep for ep in recent_episodes if ep.override_reason is None]

    if len(overridden) < 10 or len(not_overridden) < 10:
        return RampDecision(action="hold", reason="insufficient_data")

    mean_override = np.mean([ep.reward for ep in overridden])
    mean_bandit = np.mean([ep.reward for ep in not_overridden])

    improvement = mean_override - mean_bandit

    if improvement > self.min_improvement:
        new_rate = min(1.0, self.ramp_rate + self.ramp_step)
        return RampDecision(action="increase", new_rate=new_rate, improvement=improvement)
    elif improvement < -self.min_improvement:
        new_rate = max(0.0, self.ramp_rate - self.ramp_step)
        if new_rate == 0.0:
            return RampDecision(action="revert_to_shadow", improvement=improvement)
        return RampDecision(action="decrease", new_rate=new_rate, improvement=improvement)
    else:
        return RampDecision(action="hold", reason="marginal_difference")
```

### Off-Policy Correction During Ramp

During the ramp, some tasks are routed by the composer (override) and some by the bandit (no override). The bandit needs to learn correctly from both. This is exactly the importance-weighted off-policy correction from A1:

- Tasks where composer didn't override: `importance_weight = 1.0` (bandit's own pick).
- Tasks where composer overrode: `importance_weight = bandit_prob(composer_pick) / composer_prob(composer_pick)`. This down-weights the observation to account for the distributional shift.

The Episode schema already has `importance_weight` and `override_reason` fields. The A1 off-policy correction already handles this math. The composer flip just needs to set these fields correctly.

### Interaction with Other Features

**Budget pacer (F1):** The composer respects the budget filter. If the pacer has excluded paid agents, the composer cannot override to a paid agent. The composer's candidate set = bandit's candidate set after budget filtering.

**Parallel batch (F2):** During a batch, the composer evaluates each task independently. Override decisions are per-task, not per-batch. The ramp rate applies independently to each task.

**Counterfactual (F3):** The counterfactual estimator is what makes the ramp evaluation possible. Without it, we can only evaluate the composer on tasks where it happened to override AND the original agent also ran (double-run). With counterfactual, we can evaluate on all tasks where the composer disagreed.

### Files

| File | Change |
|------|--------|
| `routing/composer.py` | Add `ComposerActivation` state, ramp logic, `evaluate_ramp()`. Modify `compose()` to apply override based on `ramp_rate`. |
| `routing/bandit_router.py` | Wire composer's override decision into the execution path. Set `importance_weight` and `override_reason` on episodes. |
| `cli/commands/composer.py` | **New or extend.** `orch composer status` — mode, ramp rate, override hit rate, last eval. `orch composer evaluate` — run ramp evaluation on demand. `orch composer activate` — manual override to active mode. `orch composer deactivate` — revert to shadow. |
| `tests/test_composer_flip.py` | **New.** |

### Env Config

```bash
MAHORAGA_COMPOSER_MODE=shadow           # shadow | ramp | active
MAHORAGA_COMPOSER_RAMP_RATE=0.0         # initial ramp rate (0.0 = shadow equivalent)
MAHORAGA_COMPOSER_RAMP_STEP=0.10        # ramp increment per eval
MAHORAGA_COMPOSER_EVAL_WINDOW=100       # tasks between evaluations
MAHORAGA_COMPOSER_MIN_IMPROVEMENT=0.02  # minimum reward delta to ramp up
```

### Acceptance Criteria

1. In shadow mode, composer logs decisions but never changes routing. Verify over 100 tasks.
2. At ramp_rate=0.10, approximately 10% of tasks are overridden (±5% due to randomness). Verify over 200 tasks.
3. Ramp-up: when override tasks consistently score higher, ramp_rate increases by ramp_step.
4. Ramp-down: when override tasks consistently score lower, ramp_rate decreases.
5. Revert: when override tasks score much lower and ramp_rate hits 0.0, mode reverts to shadow.
6. Off-policy correction: importance weights on overridden episodes reflect the distributional shift.
7. Budget respect: composer never overrides to a paid agent when budget pacer has filtered it.
8. Manual controls: `orch composer activate` immediately sets mode=active, `deactivate` reverts to shadow.
9. Persistence: composer state survives server restart (serialised to `~/.mahoraga-v2/composer_state.json`).

### Tests (13 minimum)

- `test_shadow_mode_never_overrides` — shadow mode, verify 0 overrides
- `test_ramp_rate_approximate` — ramp=0.10, verify ~10% override rate over 200 tasks
- `test_ramp_evaluation_increase` — override tasks score higher → ramp increases
- `test_ramp_evaluation_decrease` — override tasks score lower → ramp decreases
- `test_ramp_evaluation_revert` — sustained bad performance → revert to shadow
- `test_ramp_evaluation_hold` — marginal difference → hold current rate
- `test_ramp_insufficient_data` — <10 overrides in window → hold
- `test_active_mode_always_overrides` — mode=active, verify composer always decides
- `test_importance_weight_override` — overridden episode has correct weight
- `test_importance_weight_no_override` — non-overridden episode has weight=1.0
- `test_budget_filter_respected` — composer can't pick a budget-filtered agent
- `test_manual_activate_deactivate` — CLI commands change mode correctly
- `test_state_persistence` — save/load composer state across restart

---

## F5. Drift Detection + Auto-Quarantine

### Problem

The bandit notices agent degradation, but slowly. If codex-cli starts failing silently (e.g., API key expires, rate limit hits, model quality degrades), the bandit's dLinUCB discount (γ=0.98) takes ~50 episodes to meaningfully reduce its score. During those 50 episodes, tasks route to a broken agent. That's 50 failed or low-quality results before the system self-corrects.

Drift detection catches this in ~10 episodes. Auto-quarantine routes around it immediately.

### Design

**Drift detector:** A rolling window per (bucket, agent) that fires when recent reward drops significantly below the agent's historical mean.

```python
@dataclass
class DriftDetector:
    window_size: int = 50          # rolling window
    min_observations: int = 20     # need this many before alerting
    sigma_threshold: float = 2.0   # fire when mean drops > 2σ below historical
    check_interval: int = 10       # check every N observations per (bucket, agent)

    # State: per (bucket, agent)
    _windows: dict[tuple[str, str], deque[float]]     # recent rewards
    _historical: dict[tuple[str, str], RunningStats]   # all-time mean/std

@dataclass
class RunningStats:
    """Welford's online algorithm for mean/variance."""
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0

    def update(self, value: float):
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self.m2 += delta * delta2

    @property
    def std(self) -> float:
        if self.count < 2:
            return float('inf')
        return math.sqrt(self.m2 / (self.count - 1))

    @property
    def lower_bound(self) -> float:
        """2σ below historical mean."""
        return self.mean - 2.0 * self.std
```

**Drift check (after each observation):**

```python
def check(self, bucket: str, agent: str, reward: float) -> DriftAlert | None:
    key = (bucket, agent)

    # Update historical stats
    self._historical.setdefault(key, RunningStats()).update(reward)

    # Update rolling window
    window = self._windows.setdefault(key, deque(maxlen=self.window_size))
    window.append(reward)

    hist = self._historical[key]
    if hist.count < self.min_observations:
        return None

    # Only check every N observations to avoid alert spam
    if hist.count % self.check_interval != 0:
        return None

    window_mean = sum(window) / len(window)
    if window_mean < hist.mean - self.sigma_threshold * hist.std:
        return DriftAlert(
            bucket=bucket,
            agent=agent,
            window_mean=window_mean,
            historical_mean=hist.mean,
            historical_std=hist.std,
            deviation_sigmas=(hist.mean - window_mean) / max(hist.std, 1e-6),
            window_size=len(window),
        )
    return None
```

**Auto-quarantine:** When drift fires, the agent is quarantined — removed from the bandit's candidate set for that bucket. Quarantine is not permanent. The agent stays quarantined until either a human reviews it (`orch agent unquarantine <agent>`) or a scheduled probe task succeeds.

```python
@dataclass
class QuarantineManager:
    quarantined: dict[tuple[str, str], QuarantineEntry] = field(default_factory=dict)
    probe_interval: int = 50       # every N tasks, send a probe to quarantined agents
    auto_release_threshold: int = 3 # consecutive successful probes to auto-release

@dataclass
class QuarantineEntry:
    agent: str
    bucket: str
    quarantined_at: datetime
    reason: DriftAlert
    probe_successes: int = 0       # consecutive successful probes
    probe_attempts: int = 0
```

**Probe tasks:** Every `probe_interval` tasks, if any agents are quarantined, route one matching task to the quarantined agent as a probe (in addition to routing to the normal pick — the user still gets a real result from the healthy agent). If the probe succeeds (quality above the bucket's mean threshold), increment `probe_successes`. After `auto_release_threshold` consecutive successes, auto-release from quarantine. If the probe fails, reset `probe_successes` to 0.

```python
def maybe_probe(self, bucket: str, task: Task) -> str | None:
    """Return a quarantined agent to probe, or None."""
    key_prefix = bucket
    candidates = [
        (k, entry) for k, entry in self.quarantined.items()
        if k[0] == bucket and entry.probe_attempts % self.probe_interval == 0
    ]
    if not candidates:
        return None
    # Pick the one with the most consecutive successes (closest to release)
    candidates.sort(key=lambda x: x[1].probe_successes, reverse=True)
    return candidates[0][1].agent

def record_probe(self, bucket: str, agent: str, success: bool):
    key = (bucket, agent)
    entry = self.quarantined.get(key)
    if entry is None:
        return
    entry.probe_attempts += 1
    if success:
        entry.probe_successes += 1
        if entry.probe_successes >= self.auto_release_threshold:
            del self.quarantined[key]
            logger.info("agent_unquarantined", agent=agent, bucket=bucket, reason="auto_release")
    else:
        entry.probe_successes = 0
```

**Integration with bandit_router:**

Before arm selection, filter out quarantined agents:

```python
def route(self, task: Task) -> str:
    bucket = classify(task)
    candidates = self.get_available_agents(bucket)

    # Budget filter (F1)
    candidates = budget_pacer.filter_arms(candidates, cost_estimates)

    # Quarantine filter (F5)
    candidates = [a for a in candidates if not quarantine_mgr.is_quarantined(bucket, a.name)]

    if not candidates:
        # All agents quarantined or budget-filtered — fall back to least-bad option
        candidates = self._fallback_candidates(bucket)

    # ... normal LinUCB selection on remaining candidates
```

### Env Config

```bash
MAHORAGA_DRIFT_ENABLED=1               # default: 1 (on by default — it's cheap)
MAHORAGA_DRIFT_WINDOW=50               # rolling window size
MAHORAGA_DRIFT_SIGMA=2.0               # sigma threshold for alert
MAHORAGA_DRIFT_MIN_OBS=20              # minimum observations before checking
MAHORAGA_QUARANTINE_PROBE_INTERVAL=50  # tasks between probes
MAHORAGA_QUARANTINE_AUTO_RELEASE=3     # consecutive probe successes to release
```

### Files

| File | Change |
|------|--------|
| `routing/drift_detector.py` | **New.** `DriftDetector`, `RunningStats`, `DriftAlert`. ~80 LoC. |
| `routing/quarantine.py` | **New.** `QuarantineManager`, `QuarantineEntry`, probe logic. ~90 LoC. |
| `routing/bandit_router.py` | Wire drift check after `observe()`, quarantine filter before arm selection, probe dispatch. |
| `cli/commands/agent.py` | Extend: `orch agent status` shows quarantine state. `orch agent quarantine <agent> <bucket>` manual quarantine. `orch agent unquarantine <agent> <bucket>` manual release. |
| `tests/test_drift_detector.py` | **New.** |
| `tests/test_quarantine.py` | **New.** |

### Acceptance Criteria

1. Agent with 50-episode rolling mean 2σ below historical mean triggers DriftAlert.
2. DriftAlert → agent quarantined → no longer selected by bandit for that bucket.
3. Agent quarantined in "code" bucket can still be selected for "research" bucket (quarantine is per-bucket).
4. Probe task sent every `probe_interval` tasks to quarantined agent.
5. 3 consecutive successful probes → auto-release from quarantine.
6. 1 failed probe after 2 successes → probe_successes resets to 0.
7. `orch agent status` shows quarantine entries with reason, timestamp, probe history.
8. Manual `orch agent unquarantine` releases immediately.
9. All agents quarantined → fallback to least-bad (lowest drift deviation).
10. Drift detection is cheap: < 1ms per check (just arithmetic on a deque).

### Tests (12 minimum)

- `test_drift_no_alert_below_threshold` — normal variation doesn't trigger
- `test_drift_alert_on_degradation` — reward drops 2σ → alert fires
- `test_drift_needs_min_observations` — <20 observations → no alert even if bad
- `test_drift_rolling_window_evicts` — old rewards fall out of window
- `test_quarantine_blocks_selection` — quarantined agent not in candidate set
- `test_quarantine_per_bucket` — quarantine in code doesn't affect research
- `test_probe_success_increments` — successful probe → probe_successes++
- `test_probe_failure_resets` — failed probe → probe_successes = 0
- `test_auto_release_after_threshold` — 3 successes → unquarantined
- `test_manual_quarantine_unquarantine` — CLI commands work
- `test_all_quarantined_fallback` — all agents quarantined → fallback selection
- `test_drift_check_performance` — 10K checks in < 100ms

---

## L3. Evolutionary Framework (Post-F1-F5)

This section is not a buildable spec — it's the design direction for what comes after F1-F5 land and the episode store has depth (500+ organic episodes). These features make the system structurally better at being a system.

### L3.1 Champion/Challenger A/B Testing

**What:** Run two configurations in parallel on a fraction of traffic. Measure the reward difference with statistical significance before promoting a change system-wide.

**Why now:** Every config change today (new strategy, new threshold, new agent, tuned α) is deployed blind. We trust benchmarks, but benchmarks are synthetic. Champion/challenger tests on real traffic.

**Design sketch:** The `bench_run_id` field already exists in the decisions DB. Add a `config_id` field. A challenger config (e.g., "α=1.5 instead of α=1.0") gets 10% of traffic. After N tasks, run a two-sample t-test on rewards between champion and challenger config_ids. If challenger wins at p<0.05, promote. If challenger loses, kill. If inconclusive, extend.

```python
@dataclass
class Experiment:
    name: str
    champion_config: dict
    challenger_config: dict
    traffic_fraction: float = 0.10    # 10% to challenger
    min_samples: int = 100            # per arm before testing
    status: str = "running"           # running | promoted | killed | inconclusive
```

**CLI:** `orch experiment create --name "alpha_1.5" --param "alpha=1.5" --traffic 0.10`. `orch experiment status`. `orch experiment promote <name>`. `orch experiment kill <name>`.

**Dependency:** Needs enough traffic volume that 10% is still statistically useful. At 50 tasks/day, that's 5 challenger tasks/day, needing ~20 days for 100 samples. At 200 tasks/day (with parallel batch), ~5 days. F2 parallel batch makes this practical.

### L3.2 Episode Replay

**What:** Re-run logged decisions under a hypothetical configuration to answer "what if?" without re-running real traffic.

**Why:** "If we'd used per-bucket bandits from day one, what would the regret curve look like?" is a question the decisions DB can answer. Every episode has the full context vector, all UCB scores, and the reward. Re-simulate the bandit's selection logic under a new config, using the logged rewards as ground truth for the agents that actually ran and counterfactual estimates (F3) for the ones that didn't.

**Design sketch:**

```python
def replay(episodes: list[Episode], config: BanditConfig) -> ReplayResult:
    """Simulate routing decisions under a hypothetical config."""
    bandit = BanditRouter(config)
    cumulative_reward = 0.0
    regret = 0.0

    for ep in episodes:
        # What would this config have picked?
        hypothetical_pick = bandit.route(ep.context_vector, ep.bucket)

        if hypothetical_pick == ep.final_pick:
            # Same agent — use real reward
            reward = ep.reward
        else:
            # Different agent — use counterfactual estimate
            est = counterfactual.estimate(ep.embedding, hypothetical_pick)
            reward = est.estimated_reward if est else ep.reward * 0.5  # conservative fallback

        cumulative_reward += reward
        regret += (ep.oracle_reward - reward)  # oracle = best agent for this task

        bandit.observe(hypothetical_pick, ep.context_vector, reward)

    return ReplayResult(cumulative_reward=cumulative_reward, regret=regret, n_episodes=len(episodes))
```

**CLI:** `orch replay --config alpha=1.5,gamma=0.95 --episodes last_500`. Prints cumulative reward and regret comparison vs. actual.

**Dependency:** Needs F3 counterfactual estimation for agents that didn't run. Quality of replay is bounded by counterfactual MAE.

### L3.3 Post-Hoc Decision Analysis

**What:** Automated queries against the decisions DB that answer specific operational questions.

**Why:** The decisions DB now logs `bandit_pick`, `composer_would_pick`, `a3_predictions`, `importance_weight`, `escalation_strategy`, `drift_alert`, `quarantine_state` on every decision. That's a goldmine for questions like:

- "When A3 disagreed with the bandit by margin > 0.2, who was right empirically?"
- "Did escalation events actually produce higher quality, or were we wasting Claude budget?"
- "What's the mean reward when composer would have overridden vs. when it agreed?"
- "Which (bucket, agent) pairs have the most unstable rolling rewards?"

**Design sketch:** A set of SQL queries + Python analysis that run against `routing_decisions.db` and produce a report. Not a dashboard — a periodic analysis that surfaces actionable insights.

```python
ANALYSES = {
    "composer_counterfactual": """
        SELECT
            CASE WHEN composer_would_pick != bandit_pick THEN 'disagreed' ELSE 'agreed' END as alignment,
            AVG(reward) as mean_reward,
            COUNT(*) as n
        FROM decisions
        WHERE created_at > datetime('now', '-7 days')
        GROUP BY alignment
    """,
    "escalation_roi": """
        SELECT
            escalation_strategy,
            AVG(reward) as mean_reward,
            AVG(cost_usd) as mean_cost,
            COUNT(*) as n
        FROM decisions
        WHERE should_escalate = 1 AND created_at > datetime('now', '-7 days')
        GROUP BY escalation_strategy
    """,
    "a3_accuracy": """
        SELECT
            CASE WHEN ABS(a3_predicted_quality - reward) < 0.15 THEN 'accurate' ELSE 'inaccurate' END as accuracy,
            COUNT(*) as n,
            AVG(ABS(a3_predicted_quality - reward)) as mean_error
        FROM decisions
        WHERE a3_predicted_quality IS NOT NULL AND created_at > datetime('now', '-7 days')
        GROUP BY accuracy
    """,
}
```

**CLI:** `orch analyze weekly` — runs all analyses, prints summary. `orch analyze escalation-roi` — runs one specific analysis.

**Dependency:** Needs organic traffic data. Most useful after 200+ decisions with all signals logged.

### L3.4 Active Exploration Scheduling

**What:** When the bandit's confidence is below threshold for a (bucket, agent) cell, explicitly schedule that combo on the next matching task instead of relying on UCB's implicit exploration.

**Why:** UCB explores proportional to uncertainty, but slowly. If Gemini CLI has never run a "refactor" task, its variance is high but it might take 50+ tasks before UCB picks it over Qwen (which has a strong empirical mean on refactor). Active scheduling says "the next refactor task goes to Gemini, period" and fills the gap in one episode.

**Design sketch:** Maintain a priority queue of (bucket, agent) cells sorted by information value (high variance * low observation count). Before each routing decision, check if the current task's bucket has a scheduled exploration. If so, route to the scheduled agent (with an exploration flag so the reward is logged correctly). Limit to 1 exploration per 20 tasks to avoid degrading user experience.

**Dependency:** Useful immediately, but more impactful after counterfactual (F3) proves that the bandit benefits from broader data coverage.

### L3.5 Auto-Retire Dominated Agents

**What:** If agent X is strictly dominated by agent Y across all overlapping buckets for 100+ episodes, surface a retirement recommendation.

**Why:** The roster trimmed from 8 → 5 was a manual decision. With enough data, the system can recommend further trimming. Fewer arms = faster convergence = better routing.

**Design sketch:** After every 100 episodes, compute pairwise dominance: for each pair (X, Y), check if Y's mean reward exceeds X's in every bucket where both have ≥20 observations. If so, flag X as "dominated by Y." Don't auto-remove — surface as a recommendation via `orch agent recommendations`.

**Dependency:** Needs 100+ episodes per (bucket, agent) pair. Only meaningful after the roster has been running for a while.

### L3 Priority Order

```
1. Post-hoc analysis (L3.3)     — cheapest, immediate value, just SQL queries
2. Champion/challenger (L3.1)   — biggest operational trust win
3. Episode replay (L3.2)        — paper-worthy, needs F3 counterfactual
4. Active exploration (L3.4)    — targeted, quick implementation
5. Auto-retire (L3.5)           — needs data depth, lowest urgency
```

---

## R1. Reliability Hardening

These aren't features — they're the connective tissue that makes F1-F5 trustworthy in production. Build them alongside or immediately after each feature.

### R1.1 MCP Server Resilience

The MCP server is the bridge between Claude Code and Mahoraga. If it fails, Claude Code falls back to inline execution (paying Anthropic per token). Every failure mode here directly costs money.

**Health check with detailed status:**

The `health_check` MCP tool currently returns a boolean. Upgrade to structured status:

```python
@dataclass
class HealthStatus:
    server_up: bool
    fastapi_reachable: bool
    ollama_available: bool
    ollama_model_loaded: str | None      # which model is warm
    budget_remaining: float | None       # from pacer
    composer_mode: str                   # shadow | ramp | active
    counterfactual_calibrated: bool
    episodes_total: int
    last_task_age_s: float | None        # seconds since last task completed
    queue_depth: int
    agents_quarantined: list[str]        # names of quarantined agents
    drift_alerts_active: int             # number of unresolved drift alerts
```

**Automatic retry with backoff in MCP layer:**

```python
MAX_MCP_RETRIES = 2
MCP_RETRY_DELAY = [1.0, 3.0]  # seconds

async def mcp_call_with_retry(func, *args, **kwargs):
    for attempt in range(MAX_MCP_RETRIES + 1):
        try:
            return await func(*args, **kwargs)
        except (ConnectionError, TimeoutError) as e:
            if attempt == MAX_MCP_RETRIES:
                raise
            await asyncio.sleep(MCP_RETRY_DELAY[attempt])
```

**Graceful degradation ladder:**

```
Full system up     → route via bandit, all features active
Agent drifting     → drift alert fired, agent quarantined, route around it
Ollama down        → route to cloud agents only, log warning
FastAPI down       → MCP returns error, Claude Code falls back to inline
Budget exhausted   → route to free agents only (local + gemini free tier)
All agents down    → MCP returns clear error: "no agents available"
```

Each degradation level should be logged and surfaced via `health_check` so the skill can make informed decisions about whether to delegate.

### R1.2 Episode Data Integrity

Counterfactual estimation (F3) is only as good as the episode data. Corrupt or missing episodes produce bad estimates, which produce bad pseudo-observations, which degrade the bandit.

**Schema validation on write:**

```python
def validate_episode(episode: Episode) -> list[str]:
    errors = []
    if episode.reward < 0.0 or episode.reward > 1.0:
        errors.append(f"reward {episode.reward} out of [0, 1]")
    if episode.embedding is not None and len(episode.embedding) != 384:
        errors.append(f"embedding dim {len(episode.embedding)} != 384")
    if episode.agent_id not in KNOWN_AGENTS:
        errors.append(f"unknown agent {episode.agent_id}")
    if episode.importance_weight < 0.0:
        errors.append(f"negative importance weight {episode.importance_weight}")
    return errors
```

Reject episodes with validation errors. Log them to a dead-letter table for debugging but don't let them into the HNSW index or the bandit.

**SQLite WAL mode:**

`routing_decisions.db` gets concurrent writes from parallel batch execution. Enable WAL mode:

```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=5000")
```

**Periodic integrity check:**

`orch data check` — verify episode count in DB matches HNSW index size, check for orphaned embeddings, verify reward distribution is within expected range (mean 0.3-0.9, no spikes at exactly 0.0 or 1.0 that would indicate a bug).

### R1.3 Bandit State Backup and Recovery

The bandit's A/b matrices are the learned model. Losing them means cold-starting from scratch.

**Auto-save after every N observations:**

```python
BANDIT_SAVE_INTERVAL = 50  # save every 50 observations

def observe(self, ...):
    # ... existing update logic ...
    self._obs_since_save += 1
    if self._obs_since_save >= BANDIT_SAVE_INTERVAL:
        self.save_state()
        self._obs_since_save = 0
```

**State file:** `~/.mahoraga-v2/bandit_state.json` — serialised A, b, observation counts per agent per bucket.

**Backup rotation:** Keep last 3 state files. If the current state is corrupt (matrix not positive definite, NaN values), fall back to the previous backup. If all 3 are corrupt, cold-start with warm-start from compatibility matrix.

```python
def load_state(self) -> bool:
    for path in [STATE_PATH, BACKUP_1, BACKUP_2]:
        try:
            state = json.loads(path.read_text())
            self._validate_state(state)
            self._apply_state(state)
            return True
        except (json.JSONDecodeError, ValidationError, FileNotFoundError):
            continue
    # All backups failed — warm-start from compatibility matrix
    self._warm_start_from_matrix()
    return False
```

### R1.4 Observability

All four features need telemetry that's visible without digging through logs.

**Structured logging:**

```python
import structlog
logger = structlog.get_logger()

# Every routing decision:
logger.info("route_decision",
    task_hash=task.hash,
    bucket=bucket,
    bandit_pick=bandit_pick,
    composer_pick=composer_pick,
    override=override,
    override_reason=override_reason,
    escalation=should_escalate,
    escalation_strategy=strategy,
    queue_depth=queue_depth,
    budget_lambda=budget_pacer.lambda_,
    budget_avg_cost=budget_pacer.avg_cost,
    counterfactual_injected=n_pseudos,
)
```

**Dashboard endpoint:**

`GET /api/v1/dashboard` — returns a JSON summary consumable by a future web UI or by the skill:

```json
{
  "total_episodes": 1234,
  "bandit_strategy": "dlinucb",
  "composer_mode": "ramp",
  "composer_ramp_rate": 0.3,
  "budget_ceiling": 0.05,
  "budget_avg_cost": 0.02,
  "budget_lambda": 0.008,
  "counterfactual_calibrated": true,
  "counterfactual_mae": 0.11,
  "queue_depth": 0,
  "drift_alerts_active": 0,
  "quarantined_agents": [],
  "agent_stats": {
    "ollama:qwen3.5-9b": {"tasks": 800, "avg_reward": 0.78, "avg_latency_ms": 6100, "quarantined_buckets": []},
    "gemini-cli": {"tasks": 200, "avg_reward": 0.82, "avg_latency_ms": 14500, "quarantined_buckets": []}
  },
  "last_task_at": "2026-05-07T14:30:00Z"
}
```

**MCP tool: `routing_stats` upgrade.**

The existing `routing_stats` tool returns basic bandit stats. Extend it to include budget, composer, and counterfactual state so the Claude Code skill can make informed delegation decisions:

```python
@mcp_tool("routing_stats")
async def routing_stats() -> dict:
    return {
        **bandit_router.get_stats(),
        "budget": budget_pacer.status(),
        "composer": composer.status(),
        "counterfactual": counterfactual.status(),
        "queue": execution_pool.status(),
        "drift": drift_detector.status(),
        "quarantine": quarantine_mgr.status(),
    }
```

### R1.5 Skill-Level Reliability

The `/mahoraga` skill in Claude Code decides WHEN to delegate. Bad decisions here are expensive — delegating a task that Qwen can't handle wastes time and then Claude does it anyway (paying double). Not delegating a task that Qwen handles well wastes money.

**Skill should check health before delegating:**

```
Before calling mcp__mahoraga__run_task:
  1. Call mcp__mahoraga__health_check
  2. If server_up=false → do inline, don't waste time
  3. If ollama_available=false → only delegate if task suits cloud agents
  4. If budget_remaining < estimated_cost → delegate (it'll route to free agent)
  5. If agents_quarantined includes the likely best agent → still delegate,
     the bandit routes around quarantined agents automatically
  6. If drift_alerts_active > 0 → delegate with caution, check result quality
```

**Skill should use routing_stats to decide complexity:**

```
Before delegating a "complex" task:
  1. Call mcp__mahoraga__route_task (dry run, no execution)
  2. If routed to claude (escalation) → skip delegation, Claude is already here
  3. If routed to local/free → delegate, that's the whole point
```

This avoids the pathological case where the skill delegates to Mahoraga, Mahoraga escalates to Claude, and Claude pays twice.

**Skill should surface batch results clearly:**

When `run_batch` returns partial failures, the skill needs to:
1. Process successful results normally.
2. Retry failed tasks inline (Claude does them directly).
3. Log which tasks failed for the bandit's implicit quality signal.

---

## R2. Integration Test Suite

Beyond unit tests for each feature, we need end-to-end tests that verify the features work together correctly.

### E2E Scenarios

**Scenario 1: Budget-constrained batch with escalation.**

Setup: `BUDGET_CEILING=0.02`, `BUDGET_HARD_LIMIT=0.10`, 5 agents available, 10 tasks in batch.

Expected: Batch runs in parallel. Most tasks route to free agents. If bandit picks a paid agent, pacer's λ adjusts. Hard limit prevents routing to expensive agents. Budget stays under ceiling over the 10-task window.

Verify: `budget_pacer.avg_cost <= 0.02`, all tasks have results, no OOM.

**Scenario 2: Counterfactual estimation with double-run.**

Setup: 300 episodes pre-loaded, counterfactual calibrated, one task triggers `should_escalate` with `strategy=double_run`.

Expected: Two agents run in parallel. Both real episodes logged. Counterfactual estimates injected for remaining 3 agents. Bandit has A/b updates for all 5 agents from this one task.

Verify: 2 real episodes + 3 pseudo-observations = 5 total updates.

**Scenario 3: Composer ramp with budget override.**

Setup: Composer at ramp_rate=0.30, budget_ceiling=0.01 (very tight), composer wants to pick codex-cli (paid).

Expected: Budget filter blocks codex-cli. Composer falls back to next-best free agent or defers to bandit pick.

Verify: No paid agent selected, override logged with `override_reason="budget_filtered_fallback"`.

**Scenario 4: Full degradation ladder.**

Setup: Start with all systems up. Kill Ollama. Kill one cloud agent. Exhaust budget.

Expected: System degrades gracefully at each step. Health check reflects current state. Tasks still route to available agents. No crashes, no hangs.

Verify: Health check status changes at each step. Tasks complete (possibly with lower quality). Error messages are clear.

**Scenario 5: Cold start to convergence.**

Setup: Fresh install, no episodes, no bandit state, compatibility matrix available.

Expected: Warm-start from matrix. First 50 tasks explore heavily (high UCB variance). Budget pacer starts relaxed (λ=0). Counterfactual not yet calibrated (< 200 episodes). Composer in shadow mode. After 200 tasks, counterfactual activates. After 300+ tasks, composer ramp begins if shadow data is positive.

Verify: Cumulative regret is sublinear. Budget stays under ceiling. No crashes during the bootstrap period.

**Scenario 6: Agent drift and auto-quarantine.**

Setup: 200 episodes accumulated, codex-cli averaging 0.80 reward on code bucket. Simulate codex-cli degradation (next 20 tasks return reward < 0.30).

Expected: Drift detector fires after ~10-15 degraded episodes. Codex-cli quarantined for code bucket. Subsequent code tasks route to Qwen/Gemini. Probe tasks sent every 50 tasks. After codex-cli "recovers" (probes succeed 3 times), auto-released.

Verify: Quarantine fires before 20 bad episodes. No code tasks route to codex-cli while quarantined. Auto-release works after recovery.

**Scenario 7: Cascading quarantine (stress test).**

Setup: 3/5 agents drift simultaneously on the "code" bucket (simulating an API outage that affects cloud agents).

Expected: All 3 quarantined. Remaining 2 agents handle all code tasks. Probes sent to quarantined agents. When API recovers, agents auto-release one by one as probes succeed. System doesn't crash or deadlock with 3/5 agents quarantined.

Verify: At least 1 agent always available (fallback logic). Quarantine entries have correct timestamps and reasons. System recovers fully after API restoration.

---

## Implementation Sequence

Concrete file-by-file build order within each feature, designed so each commit is testable.

### Week 1: Budget Pacer (F1)

```
Day 1:
  ├─ routing/budget_pacer.py (BudgetPacer class, ~60 LoC)
  ├─ tests/test_budget_pacer.py (12 tests)
  └─ Verify: all tests pass in isolation

Day 2:
  ├─ Wire into routing/bandit_router.py (filter_arms, update after observe)
  ├─ Wire into routing/reward_learner.py (adjusted cost weight)
  ├─ config.py env loading
  └─ Verify: orch benchmark simulate with BUDGET_CEILING=0.02 stays under budget

Day 3:
  ├─ cli/commands/budget.py (orch budget status)
  ├─ mcp/server.py (add budget to routing_stats)
  └─ Verify: MCP health_check includes budget state
```

### Week 2: Parallel Batch (F2)

```
Day 1:
  ├─ routing/execution_pool.py (ExecutionPool, QueueTracker, ~120 LoC)
  ├─ tests/test_execution_pool.py (5 tests: semaphores, tracking)
  └─ Verify: pool correctly limits concurrency

Day 2:
  ├─ Refactor executor to async-safe execute_task()
  ├─ Add execute_batch() with asyncio.gather
  ├─ tests/test_parallel_batch.py (partial: 5 tests)
  └─ Verify: batch of 3 cloud tasks runs in ~max(times) not sum(times)

Day 3:
  ├─ Wire queue_depth_norm into context.py (feature 9)
  ├─ Wire double-run into escalation gateway (A2 strategy 2)
  ├─ tests/test_parallel_batch.py (remaining: 5 tests)
  └─ Verify: double-run logs both episodes, queue depth feeds context

Day 4:
  ├─ Update mcp/server.py run_batch handler
  ├─ Timeout handling (execute_with_timeout)
  ├─ tests/test_parallel_batch.py (remaining: 5 tests)
  └─ Verify: MCP run_batch works end-to-end, timeouts don't block
```

### Week 3: Counterfactual Estimation (F3)

```
Day 1:
  ├─ routing/counterfactual.py (CounterfactualEstimator, ~100 LoC)
  ├─ tests/test_counterfactual.py (partial: 5 tests: estimate, weighting)
  └─ Verify: estimates match expected values on synthetic data

Day 2:
  ├─ routing/counterfactual.py (CalibrationGate, ~80 LoC)
  ├─ tests/test_counterfactual.py (partial: 4 tests: calibration)
  └─ Verify: gate blocks when uncalibrated, allows when MAE < threshold

Day 3:
  ├─ Wire observe_pseudo into bandit_router.py
  ├─ Wire injection after observe() with budget filter
  ├─ tests/test_counterfactual.py (remaining: 5 tests: integration)
  └─ Verify: all agents have non-trivial A/b after 500 episodes

Day 4:
  ├─ cli/commands/counterfactual.py (orch counterfactual status|evaluate)
  ├─ Wire into routing_stats MCP response
  └─ Verify: CLI and MCP report consistent state
```

### Week 4: Composer Flip (F4) + Drift/Quarantine (F5) + Reliability (R1)

```
Day 1:
  ├─ routing/composer.py (ComposerActivation, ramp logic, ~150 LoC)
  ├─ tests/test_composer_flip.py (partial: 6 tests: modes, ramp)
  └─ Verify: shadow never overrides, ramp at 10% overrides ~10%

Day 2:
  ├─ Wire ramp evaluation into routing loop
  ├─ Wire off-policy correction for overridden episodes
  ├─ tests/test_composer_flip.py (remaining: 7 tests)
  └─ Verify: ramp up on good performance, ramp down on bad

Day 3:
  ├─ routing/drift_detector.py (DriftDetector, RunningStats, ~80 LoC)
  ├─ routing/quarantine.py (QuarantineManager, probe logic, ~90 LoC)
  ├─ Wire into bandit_router.py (check after observe, filter before select)
  ├─ tests/test_drift_detector.py (6 tests)
  ├─ tests/test_quarantine.py (6 tests)
  └─ Verify: degraded agent gets quarantined, probe recovery works

Day 4:
  ├─ R1.1: MCP health_check upgrade (HealthStatus with drift/quarantine fields)
  ├─ R1.2: Episode validation + WAL mode
  ├─ R1.3: Bandit state backup rotation
  └─ Verify: health_check returns structured status, corrupt state falls back to backup

Day 5:
  ├─ R1.4: Dashboard endpoint + routing_stats upgrade (budget + composer + counterfactual + drift + quarantine)
  ├─ R1.5: Skill-level routing_stats check guidance
  ├─ cli/commands/agent.py (orch agent status, quarantine, unquarantine)
  ├─ R2: E2E integration tests (7 scenarios including drift/quarantine)
  └─ Verify: full system exercised end-to-end
```

---

## Metrics That Matter

After all five features land, these are the numbers to watch. Grouped by which loop they measure.

**Loop 1 — Online Learning (is the bandit getting better?)**

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Cumulative regret | Sublinear (β < 1.0) | `orch benchmark simulate` |
| Mean reward (rolling 100) | > 0.80 | `routing_stats` |
| Bandit convergence speed | < 100 episodes to stable arm preferences | `orch benchmark simulate` with/without counterfactual |
| All-agent data coverage | No agent with < 5% of total observations | `routing_stats` agent distribution |

**Loop 2 — Component Calibration (are the supporting models staying current?)**

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Cost per task (rolling 100) | < $0.05 (or whatever BUDGET_CEILING is) | `orch budget status` |
| Counterfactual MAE | < 0.15 after 200 episodes | `orch counterfactual status` |
| Override hit rate | > 0.60 when composer is active | `orch composer status` |
| Batch throughput | > 2x sequential for 3+ cloud tasks | Timing comparison in integration tests |

**Loop 3 — Evolutionary (is the system structurally adapting?)**

| Metric | Target | How to Measure |
|--------|--------|----------------|
| Drift detection latency | < 15 episodes from degradation start to quarantine | `orch agent status` |
| Quarantine false positive rate | < 5% (quarantined agents that were actually fine) | Post-hoc analysis on probe results |
| Auto-release success rate | > 90% (released agents don't re-drift within 50 episodes) | Decision DB query |
| System uptime | 0 MCP failures per 100 delegations | Health check logs |
| Week-over-week reward trend | Positive slope | `orch analyze weekly` (L3.3, when built) |

---

## Research Methodology Shift

The features in this spec aren't only new code — they change how we *test* and *research* Mahoraga. v1 testing was "did regret go down on 200 synthetic tasks." v2 testing is "is the system measurably improving on real traffic, week over week, with every config change validated against evidence." This section names the shift explicitly so we don't accidentally keep evaluating v2 with v1 instruments.

### What Stays

The existing benchmark suite still answers a valid question — *does the algorithm work in principle on synthetic tasks?* Keep all of it:

- `orch benchmark simulate` — strategy comparison on synthetic oracle data
- `orch benchmark lab` — forced round-robin with quality scoring
- `orch benchmark memory-mode` — multi-seed sweeps over memory configurations
- `orch benchmark paraphrase-eval` — train/test split for transfer accuracy
- `orch benchmark bootstrap` — synthetic tasks through the real BanditRouter

These are unit-level tests for the routing math. They detect regressions in algorithm correctness. They do not detect "is this config better in *this user's* daily workload" — that's a different question requiring different instruments.

### What's New

Three layers added on top of the existing suite, each addressing a question the v1 suite cannot answer:

**1. Counterfactual reductions over the decisions DB.** Scriptable analyses that answer specific operational hypotheses. The decisions DB now logs `bandit_pick`, `composer_would_pick`, `a3_predictions`, `importance_weight`, `escalation_strategy`, `drift_alert`, `quarantine_state` on every decision. After 200+ organic episodes, we can run targeted SQL + Python reductions:

- Was reward higher when composer would have overridden vs. when it agreed?
- When A3 disagreed with the bandit by margin > 0.2, who was right empirically?
- Did escalation events produce higher quality, or did we waste Claude budget?
- How often did A3's predicted P(success) match observed success?

These are spec'd in L3.3 (post-hoc decision analysis). They are the cheapest "is the system getting better?" instrument because they require zero new traffic — only that we've been logging.

**2. Online metrics dashboard.** From R1.4. Instead of running a one-shot benchmark and reading numbers, the dashboard exposes rolling metrics that update continuously: cumulative reward, per-bucket convergence, memory hit rate, escalation rate, A3 calibration error, importance-weight distribution, routing latency. The shift is from *batch report* to *live signal*. You can answer "is Mahoraga healthy right now?" without running anything.

**3. Champion/challenger A/B framework.** From L3.1. Every config change goes through a controlled experiment before adoption. Two configs run in parallel on a deterministic traffic split (default 10%); after K decisions per arm, a stat-sig test decides whether the challenger wins. This is the gold-standard test — slower than replay, definitive in conclusion.

### The Triangulation Workflow

For any v2-and-beyond config change — new strategy, new threshold, new agent, modified prompt — the workflow is:

```
1. Replay first  (L3.2)         — does the change look promising on history?
   ↓ if yes
2. A/B test      (L3.1)         — does the change actually win in live traffic?
   ↓ if yes
3. Promote       (operator)     — adopt as champion, log promotion
   ↓ ongoing
4. Watch dashboard (R1.4)       — second-order effects in metrics not in the test
5. Drift detector catches regressions (F5) — if the change introduces a problem,
                                              the system protects itself
```

Each step is faster but more biased than the next. Replay is fast and cheap but biased by counterfactual estimation error. A/B is slow but unbiased over a sufficient window. Dashboard catches second-order effects you didn't think to test. Drift detector is the safety net.

A change that loses on replay almost certainly loses live — replay is a screening tool. A change that wins on replay needs A/B confirmation before adoption. A change that wins A/B and shows no second-order regressions in the dashboard is a real win.

This workflow is what turns Mahoraga from "we tested it once, it worked" to "every change has evidence, every regression is caught, every adoption is auditable." It is the methodological core of claim 3 ("it adapts structurally") in the identity statement at the top.

### What This Replaces

Today, "should we enable composer override?" is answered by: edit env var, watch what happens, hope it's better. Under the new methodology:

1. `orch replay --composer-enabled` over the decisions DB → does it look promising?
2. If yes: `orch ab create --challenger=composer-enabled --split=0.10` → does it win?
3. If yes: `orch ab promote --to=composer-enabled` → champion changes
4. Dashboard shows updated rolling reward, drift detector watches for regressions

Same question, but the answer is *evidence-backed* and the trail is *auditable*. That difference is what makes Mahoraga trustworthy for daily use rather than a clever toy that occasionally surprises you.

### What's Out of Scope (Methodology)

- **Auto-bucket discovery** (clustering the actual task distribution to find new buckets). Research-grade; revisit when episode count is high enough that the keyword classifier is genuinely lossy.
- **Long-term reward attribution.** "Did this routing decision contribute to the user shipping faster?" requires linking routing decisions to downstream outcomes (PR merged, test passed, deploy succeeded). Out of scope until we have those signals.
- **Cross-user comparison.** Mahoraga is single-user today. When/if it becomes multi-user, cohort analyses become possible. Out of scope for v2.

The methodology in this section is calibrated to a single-user, single-machine deployment evaluating routing decisions over the decisions DB. Anything beyond that is a separate research problem.

---

## Open Questions (Decide During Build)

1. **Counterfactual decay schedule.** Fixed `decay=0.3` or decay that shrinks as real observations accumulate? The Calibration-Gated paper found prompt design dominates hyperparameters, suggesting a fixed decay is fine. But our episode-based approach might benefit from adaptive decay. Try fixed first, tune later.

2. **Composer override granularity.** Per-bucket or global ramp rate? A per-bucket ramp lets the composer be active for "research" tasks (where it's confident) while staying shadow for "code" tasks (where the bandit is already good). More complex but more precise. Start global, split per-bucket if the global ramp keeps oscillating.

3. **Double-run agent selection.** When the escalation gateway fires double-run, which two agents? Current spec: bandit's pick + composer's pick. Alternative: bandit's top-2 by UCB. The first is more interesting (bandit vs. composer), the second is simpler (no composer dependency). Depends on whether F4 lands before or after F2's double-run. If building in order (F2 before F4), start with top-2 by UCB, switch to bandit+composer when F4 lands.

4. **Parallel local model handling.** `max_local=1` is conservative. On 16GB with Qwen 3.5 9B (6.6GB) as the only local model, there's no second local model to run in parallel anyway. But if the roster includes Gemma 4 E4B (3GB) as a second local model, we'd need model switching — unload one, load the other. Ollama handles this, but the cold-load penalty is 5-10s. The spawn penalty in the reward function already captures this. Decision: keep `max_local=1` for now, revisit when/if MLX enables faster model switching.

5. **Streaming in parallel batch.** Option A (buffer and return) is specced. If real-world batch tasks turn out to be long-running (>30s), revisit with Option B (per-task streaming with task IDs). Monitor average batch task latency for the first 2 weeks.

6. **Drift sigma threshold.** 2σ is standard but may be too sensitive for agents with high-variance rewards (e.g., research tasks where quality is inherently noisy). Per-bucket sigma thresholds might be needed — research at 2.5σ, code at 1.5σ. Start with global 2σ, tune per-bucket if quarantine false positives are too high.

7. **Probe task selection.** Current spec probes quarantined agents with the next matching task. Alternative: maintain a fixed probe set per bucket (3-5 representative tasks with known ground-truth quality) and always use those. The fixed set gives more consistent probe results but doesn't test the agent on real workload. Start with real tasks, add a fixed probe set if probe results are too noisy.

8. **Champion/challenger traffic fraction.** 10% is the default. At low traffic volumes (<50 tasks/day), 10% means ~5 challenger tasks/day, requiring 20 days for statistical significance. Options: increase to 20% at low volume (faster convergence, more exposure to potentially worse config), or accept the slower evaluation cycle. Decide based on actual traffic volume after F2 parallel batch lands.

9. **Continuous evaluation vs. batch benchmarking.** The v1 benchmarks (regret curves, win-rate, per-bucket convergence) still have value as regression tests. But the primary evaluation loop shifts to continuous metrics from the decisions DB. Keep `orch benchmark simulate` for regression testing; add `orch analyze weekly` for continuous evaluation. Don't let the batch benchmarks become the source of truth — they test synthetic tasks, not your traffic.