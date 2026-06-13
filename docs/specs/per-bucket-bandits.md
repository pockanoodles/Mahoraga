# Per-Bucket Bandits — design scope

**Status:** scoping (pre-implementation)
**Motivation:** A1 §15.3 + §15.5 + §15.6 empirical findings — the v1/v2 LinUCB θ is global across all routing buckets, and the resulting bucket-coupling explains why simple per-bucket gating doesn't work and why semantic memory faithfully reproduces the bandit's wrong picks.
**Goal:** decouple buckets at the bandit-state layer so wrong picks in one bucket don't poison routing in others. Side benefits: per-bucket warm-start becomes meaningful, memory bias becomes additive on top of bucket-specialised exploit, A2/A3 confidence-aware routing has a cleaner uncertainty signal.

---

## 1. Problem statement

Today, `LinUCBRouter` maintains one A matrix and one b vector per *agent*, all sharing a 9-dim TaskContext input space. The same θ is used for routing every task regardless of bucket. Three observed consequences:

**(1) Bucket coupling on rewards.** A bandit observation on a research-bucket task with a wrong agent updates θ in ways that affect routing decisions on subsequent debugging-bucket tasks (and every other bucket). The shared θ is a single linear function fit to the entire heterogeneous task distribution.

**(2) Per-bucket gating doesn't isolate.** Even when α=0 disables memory bias for a specific bucket (§15.5), bucket-A's outcomes still update the global θ that bucket-B uses. The retrieval-layer gating recovers some signal but cannot recover the off-baseline because the bandit-state layer is still coupled.

**(3) Memory faithfully reproduces wrong picks.** When LinUCB converges on the wrong agent for a prompt class because the 9-dim handcraft features are insufficient to discriminate, semantic memory at test time retrieves exactly those wrong-agent episodes (§15.6). Per-bucket bandits should reduce mis-convergence in the first place by allowing per-bucket θ to specialise.

---

## 2. Design

### 2.1 Core change: nested per-bucket state

```
v1/v2 state shape:                v3 (per-bucket) state shape:
  A: { agent: (d×d) }              A: { bucket: { agent: (d×d) } }
  b: { agent: (d×1) }              b: { bucket: { agent: (d×1) } }
  t: scalar                        t: { bucket: scalar }   (or global)
```

When routing a task:
1. Classify bucket via `classify_bucket(context)` — already shared across the codebase as of the per-bucket gating commit.
2. Look up bucket-specific A, b. Initialise on first use.
3. Compute UCB scores from bucket-specific θ.
4. Pick max-UCB. Update bucket-specific A, b on observe().

The classifier covers 7 canonical buckets: `research`, `simple_qa`, `debugging`, `code_generation`, `complex`, `code_editing`, `default`. Worst case the per-bucket A/b grows by 7×, which is still ~570 floats per agent at d=9 — trivial.

### 2.2 Bucket initialisation

When a (bucket, agent) pair is first seen:

- **Cold start (no observations in this bucket yet for any agent):** initialise A = I, b = prior · 1, just like v1. The exploration coefficient α dominates early picks.
- **Bucket exists, agent is new:** average-init from existing per-bucket arms (same logic as v1's `_init_agent`, scoped to the current bucket). Apply compatibility-matrix warm-start if available.
- **Agent exists in other buckets, new in this bucket:** here is the architectural choice.
  - **Option A — fully specialised.** Initialise as a brand-new arm (cold or average). The agent has to re-learn its strengths in this bucket. Pro: clean isolation. Con: slow learning; loses transfer signal.
  - **Option B — pooled init.** Initialise from a weighted mix of the agent's other-bucket A/b matrices, with a configurable pooling weight. Pro: fast warm-start from cross-bucket experience. Con: re-introduces some coupling.
  - **Option C — hierarchical.** Maintain a global "shared" A_global, b_global per agent, plus per-bucket residuals. The bandit picks via shared + bucket-residual. Pro: principled hierarchical Bayes. Con: more state, more code, harder to debug.

  **Recommendation: Option B** for the first prototype. Simpler than (C), keeps useful cross-bucket prior. Tunable with a `bucket_pooling_weight` parameter (0 = full specialisation = Option A, 1 = full pooling = current global behaviour).

### 2.3 dLinUCB discount factor

Currently `decay = 0.98` is applied per-update globally. With per-bucket state, the discount should apply per-bucket on each bucket's observations. This is mechanical — the existing `update()` already operates per-arm; with per-bucket scoping it operates per-(bucket, arm).

### 2.4 Warm start

`compatibility_matrix.json` currently shapes a global A/b warm-start. Two paths:

- **(easy)** Apply the same compatibility matrix to every bucket's A/b — every bucket starts from the same prior. The warm-start signal is replicated across buckets.
- **(better)** Per-bucket compatibility matrices: `compatibility_matrix.json` keyed by bucket → agent → reward. Lab benchmarks (which already produce per-bucket data) generate these. Defer.

### 2.5 Public API changes

```python
# v1/v2:
strategy.select_agent(context, available_agents) -> str
strategy.update(context, agent, reward) -> None
strategy.compute_scores(context, available_agents) -> dict[str, dict]

# v3 — same signatures, with bucket extracted internally from context:
strategy.select_agent(context, available_agents) -> str
strategy.update(context, agent, reward) -> None
strategy.compute_scores(context, available_agents) -> dict[str, dict]
```

The bucket is derived from `context` via `classify_bucket(context)` inside the strategy. This keeps the existing `BanditRouter` call sites unchanged. A future iteration could pass bucket explicitly to enable A/B testing of different classifiers, but for v1 of per-bucket bandits we co-evolve the classifier and the bucketed state.

The existing UCB1 / Thompson strategies do not gain per-bucket state automatically — that's a separate design question. For v1, only LinUCB ships per-bucket; the others stay global. Switching strategies (`set_strategy`) resets state regardless.

### 2.6 Memory and reward learner

- **EpisodicMemory:** unchanged. Memory already stores per-episode bucket implicitly (via task description); retrieval still operates over the embedding/handcraft space across all buckets. Per-bucket retrieval is a separate concern — the v1 of per-bucket bandits keeps the existing two-tower memory.
- **RewardWeightLearner:** already per-bucket (`learner.observe(bucket=...)`). No change.

---

## 3. Persistence

Schema bump: `bandit_state.json` v3.

```json
{
  "version": 3,
  "d": 9,
  "alpha": 1.0,
  "decay": 0.98,
  "buckets": {
    "research": {
      "t": 47,
      "agents": {
        "ollama":     { "A": [[...]], "b": [[...]] },
        "gemini-cli": { "A": [[...]], "b": [[...]] }
      }
    },
    "code_generation": { ... },
    "default": { ... }
  }
}
```

Migration from v2 (current schema):

1. On load, detect `version == 3`.
2. If `version` missing / `version < 3`: this is v1/v2 state with global A/b.
   - Path A: discard, reinitialise (clean break).
   - Path B: load v2 state into bucket "default" (or "_global"). Subsequent observations on other buckets bootstrap fresh per-bucket state.
   - **Recommendation: Path B.** Preserves the user's accumulated bandit knowledge as a starting point.

---

## 4. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Per-bucket θ undertrained → worse exploration than global | High | Medium | Bucket pooling (Option B) blends in cross-bucket prior. Tunable weight. |
| Classifier mis-bucketing → state thrashing | Medium | High | The classifier is deterministic over `TaskContext` → same input → same bucket. Already deployed and tested via §15.5 work. |
| `default` bucket becomes a catch-all dump for ambiguous tasks | High | Low | Already happens in the static classifier (~30% of adversarial set lands in `default`). With per-bucket state, default gets its own θ — actually fine. |
| Storage / persistence size grows 7× | Certain | Low | At d=9, ~570 floats per agent total (vs 81). Trivial. |
| dLinUCB discount per bucket creates uneven decay | Medium | Medium | Buckets with more traffic decay faster. May want bucket-normalised decay. Defer to first benchmark. |
| Warm-start signal weakens (one matrix to seed N buckets) | Medium | Low | Replicate the same matrix across buckets; future work generates per-bucket matrices. |
| Bandit needs more total observations before per-bucket θ stabilises | High | Medium | Plot per-bucket convergence in the lab benchmark. If problematic, raise the bucket-pooling weight or lower the bucket count. |

---

## 5. Implementation plan

### Phase 1 — `LinUCBPerBucketRouter` (parallel, opt-in)

Add a new strategy `linucb_per_bucket` alongside the existing `linucb`. Same API, different state shape. Selectable via `BanditRouter(strategy="linucb_per_bucket")` or env var.

Files:
- `routing/strategies/linucb_per_bucket.py` — new strategy class
- `routing/strategies/__init__.py` — register it
- `routing/strategies/tests/test_linucb_per_bucket.py` — unit tests
- `routing/bandit_router.py` — register in `STRATEGIES` map (no other changes; same API)

Tests cover:
- per-bucket initialisation (cold, average-init, pooled)
- separate updates don't cross-contaminate
- persistence round-trip
- v2-state compatibility (loads as bucket "default")
- compute_scores returns same shape per agent

### Phase 2 — Run paraphrase + memory-mode benchmarks under both strategies

Re-run the §15.3 / §15.6 benchmarks with `--strategy linucb_per_bucket`. Compare to `linucb` baseline.

Hypothesis: per-bucket bandit reduces wrong-pick lock-in → off-baseline test accuracy improves on both benchmarks. Memory-bias signal becomes cleaner because it's now layered on top of a bucket-specialised exploit.

### Phase 3 — If Phase 2 wins, promote to default

If per-bucket bandits show clear empirical wins (≥ 5 pp test accuracy on paraphrase, ≥ 3 pp aggregate on memory-mode), promote `linucb_per_bucket` to the default strategy. Otherwise keep both available behind a config flag and document tradeoffs.

### Phase 4 (deferred) — Hierarchical / pooled per-bucket bandit

If pooled init (Option B) is insufficient, implement true hierarchical Bayes (Option C). This is research-grade work; not in scope for the v1 ship.

---

## 6. Effort estimate

- Phase 1 implementation + tests: 1-2 sessions
- Phase 2 benchmarks: half a session (re-run existing harnesses with new strategy flag)
- Phase 3 decision + spec update: half a session
- Total: ~3-4 sessions

This compares favourably to other paths (e.g., gathering real-workload data for paraphrase eval would take weeks of dogfooding).

---

## 7. Open questions

- Should the `default` bucket exist or should every task pick a non-default classifier output? Affects dispersion of state.
- Is the bucket-pooling weight tunable per env/config, or fixed at a single value? Argues for env var following the existing pattern (`MAHORAGA_BUCKET_POOLING_WEIGHT`).
- Should EpisodicMemory retrieval also become per-bucket? Probably not for v1 — keep memory as a separate concern.
- What's the right way to surface per-bucket state in `get_stats()`? Add a `per_bucket_summary` dict with arm counts, mean reward, etc.

These resolve during implementation.
