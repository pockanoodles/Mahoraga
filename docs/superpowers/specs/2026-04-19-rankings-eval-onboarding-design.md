# Rankings, Evaluation, and Onboarding — Design Spec

**Date:** 2026-04-19  
**Status:** Approved  
**Source:** mahoraga_rankings_eval_handoff.md (user-provided)

---

## 1. Product Goal

A local-first rankings and evaluation layer that answers three practical questions:

1. Which agent/model performs best on this machine?
2. How easy is it to add a new agent/model and get it ranked?
3. Does Mahoraga routing actually improve outcomes versus not using it?

**Product message:** Mahoraga is not just an orchestrator. It is an adaptive local routing system that can learn from your usage, benchmark your available agents locally, rank them on your machine, and show whether routing is actually helping.

---

## 2. Non-Negotiable Decisions

### 2.1 Ranking Sources
- **Use:** live bandit routing history (SQLite) + Mahoraga's own benchmark harness
- **Do not use:** external leaderboard scraping, LMSys/HuggingFace ingestion, remote polling, cloud sync, background external freshness jobs
- **Reason:** local truth, not lab truth. Honest story: "ranked on your machine, on your tasks"

### 2.2 Onboarding UX
- Primary entry point: `orch agent add <model>`
- Single command: register → validate → smoke test → benchmark sweep → update rankings → print summary

### 2.3 Comparison Axes
- **First axis:** existing keyword classifier task buckets (code, debug, refactor, research, general, plan, test, review, explain — use whatever is canonical in the codebase)
- **Second axis:** difficulty tier (simple, medium, complex)
- **Supported scopes:** overall, by bucket, by difficulty, by bucket+difficulty

### 2.4 CLI Output
- Default: ranked table with rank, agent, win rate, 95% CI, avg latency, avg reward, N
- `--verbose`: regret, per-bucket breakdown, per-difficulty breakdown, trend
- `--json`: pipeable structured output

### 2.5 UI Placement
- New sidebar tab: **Rankings**
- No new top-level route unless forced by architecture
- Screenshot-friendly, inside existing dashboard flow

### 2.6 Staying Current
- Live bandit learning runs continuously (already working)
- Manual refresh: `orch benchmark refresh`
- No cron jobs, no background benchmark daemons

---

## 3. Feature Set

### 3.1 `orch eval ab --tasks eval/tasks.yaml`
Run the same fixed task suite with routing OFF and ON. Compare outcomes directly.

Output fields:
- Median latency delta, P90 latency delta
- Success rate delta, retry rate delta, escalation rate delta
- Reward delta
- Per-bucket and per-difficulty delta

Optional flags: `--json`, `--repeat N`, `--seed`, `--baseline fixed:<agent>`

### 3.2 `orch rankings`
Display local agent rankings from live routing history + local benchmark harness.

Filters: `--bucket`, `--difficulty`, `--agent`, `--limit`, `--json`, `--verbose`

### 3.3 `orch agent add <model>`
1. Infer or accept adapter type
2. Write minimal config entry / scaffold
3. Validate binary/model availability
4. Run health check
5. Run smoke suite (3 trivial tasks)
6. Run short benchmark sweep
7. Rebuild rankings
8. Print summary

If full automation is unrealistic: create config stub, validate what exists, tell engineer what remains, then continue once dependencies exist. Preserve the single-command story.

### 3.4 `orch benchmark refresh`
Re-run the local harness explicitly. Update benchmark result tables. Recompute ranking aggregates. Print summary.

---

## 4. Evaluation Methodology

### 4.1 Task Suite Schema (YAML)
```yaml
suite: default_ab
seed: 42
tasks:
  - id: easy_code_1
    text: "write a python function that returns the mean of a list"
    bucket: code
    difficulty: simple
    tags: [easy, code]
```
Fields: `id`, `text`, `bucket`, `difficulty`, optional `tags`, `expected_artifacts`, `timeout_s`

### 4.2 ON vs OFF Semantics
- **OFF:** deterministic baseline policy (fixed agent, configured default, or round-robin). Must be explicit in output.
- **ON:** Mahoraga normal routing — bandit selection + normal retry/escalation

### 4.3 Latency Metrics
Use median and P90. Do not rely only on mean (averages hide tail behavior from escalations/retries).

### 4.4 Scoring
Use existing Mahoraga reward model. Do not create a second competing reward system.

---

## 5. Ranking System

### 5.1 Data Sources
Treat live history and benchmark harness as separate sources, then aggregate:
```
combined_reward = 0.7 * live_reward_weighted + 0.3 * benchmark_reward_weighted
```
Keep source counts visible in verbose/JSON output.

### 5.2 Default Sort Order
1. `mean_reward DESC`
2. `win_rate DESC`
3. `median_latency_ms ASC`
4. `sample_count DESC`

### 5.3 Win Rate CI
Wilson interval (95%):
```python
def wilson_interval(successes, total, z=1.96):
    if total == 0:
        return (0.0, 0.0)
    phat = successes / total
    denom = 1 + z*z/total
    center = (phat + z*z/(2*total)) / denom
    margin = z * sqrt((phat*(1-phat) + z*z/(4*total)) / total) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))
```

---

## 6. Data Model

All tables in existing SQLite store. Extend existing models where possible.

### `routing_runs`
`id, run_type, started_at, finished_at, routing_enabled, baseline_policy, task_suite_name, repeat_index, notes`

`run_type` values: `ab_off`, `ab_on`, `benchmark`, `smoke`, `refresh`

### `routing_run_tasks`
`id, run_id, task_id, task_text, bucket, difficulty, selected_agent, worker_id, ttft_ms, latency_ms, success, quality_score, retry_count, escalation_count, cost_usd, reward, final_status, created_at`

### `benchmark_runs`
`id, agent, bucket, difficulty, started_at, finished_at, avg_latency_ms, median_latency_ms, p90_latency_ms, win_rate, reward_mean, sample_count, source`

### `model_rankings`
`id, agent, scope_type, scope_value, rank, win_rate, ci_low, ci_high, avg_latency_ms, avg_reward, sample_count, updated_at`

Scope examples: `scope_type='overall', scope_value='all'` | `scope_type='bucket', scope_value='debug'` | `scope_type='bucket_difficulty', scope_value='debug:simple'`

---

## 7. Routing Layer Change

Add explicit `routing_mode` parameter to the orchestrator's run path:

```python
async def run_task(task, routing_mode: str, baseline_policy: str | None = None):
    if routing_mode == "adaptive":
        return await run_with_normal_router(task)
    if routing_mode == "fixed":
        return await run_with_fixed_agent(task, baseline_policy)
    if routing_mode == "round_robin":
        return await run_with_round_robin(task)
```

Evaluation code must NOT depend on the shell flag file (`~/.claude/mahoraga-active`). That flag is for Claude Code routing only. The eval system needs an explicit API-level switch.

---

## 8. UI

### Rankings Sidebar Tab
Minimum contents:
- Overall rankings table
- Filters for bucket and difficulty
- Latest refresh timestamp
- Source summary (live history count vs benchmark count)

Optional if cheap:
- Sparkline/trend indicator per agent
- Click agent to expand bucket breakdown
- "Best for X" badges

Do not add a separate route. Do not add heavy charting as default view.

---

## 9. Constraints

1. No external ranking ingestion (no scraping, no LMSys, no HuggingFace)
2. No background jobs or cron-like benchmark refresh
3. No parallel metrics universe — extend existing task_metrics, reward, benchmark systems
4. No layout redesign or mockup work
5. Output must use scope-honest language: "local rankings", "on this machine", "based on live history and local benchmarks"
6. Preserve existing bucket semantics exactly

---

## 10. Acceptance Criteria

**Rankings:** `orch rankings` works, ranked table default, `--json` + scope filters, local data only, includes win rate/CI/latency/reward/N

**Evaluation:** `orch eval ab --tasks <file>` works, runs OFF vs ON, persists per-task results, prints comparison summary

**Onboarding:** `orch agent add <model>` validates, smoke tests, benchmarks, updates rankings, prints summary

**Refresh:** `orch benchmark refresh` updates benchmark data and rankings

**UI:** Rankings sidebar tab shows overall table with bucket/difficulty filters

---

## 11. Implementation Priority

| Phase | Work |
|-------|------|
| 1 | Task suite schema + `orch eval ab` + SQLite eval logging |
| 2 | Ranking aggregation + `orch rankings` + CI + JSON output |
| 3 | `orch agent add` + smoke suite + benchmark-on-onboarding + ranking rebuild |
| 4 | UI Rankings sidebar tab + filters |

---

## 12. Nice-to-Have (Not Mandatory)

- Trend indicators for agent performance over time
- Source split display (live vs harness)
- Pairwise head-to-head comparison view
- Rank badges ("best for simple debug")
- Repeated-run CI for A/B
- Export to Markdown/CSV
