# Mahoraga v2 Specification

**Status:** Active
**Owner:** @pockanoodles
**Reviewers:** Claude Code (codebase-resident senior eng)
**Target:** local-only routing system with verified correctness, ready for 200+ real routing decisions through Claude Code MCP

---

## 0. Why This Spec Exists

v1 shipped with phantom integrations. Bucket names diverged silently between classifier and scorer for months — every task scored by the generic prose path, security row flat at 0.650 because the security scorer never ran. The drift detector and OLS reward learner had a known false-positive interaction patched with a test-only `monkeypatch` that hid the bug rather than closing it. Two model rosters lived in parallel with no enforcement that priors matched enabled agents. The 192-task v1 benchmark scored every task through a broken-but-internally-consistent path, which means most of its per-bucket numbers were measurement noise dressed as signal.

v2 is the correctness pass. Not new features. Not capability expansion. The job is to make the system honest about what it does, verifiable end-to-end, and ready for real traffic.

Specifically: cut the agent roster to two local arms, restore the bucket vocabulary to a single source of truth, fix the OLS/drift interaction in production code (not just test environment), enforce contracts between every pair of interacting modules with adversarial tests, and ship a versioned benchmark that future runs can be compared against. Parallel execution is wired but stays inactive — it lands in v3 once the correctness story is verified on sequential traffic.

The deliverable is a daemon you start once and forget about, MCP-callable from Claude Code, routing intelligently across qwen3.5 and granite4.1-8b on your hardware, learning from every routing decision.

---

## 1. Goals and Non-Goals

### Goals

- Single source of truth for bucket names, agent IDs, capability tags (`routing/vocab.py`)
- All 9 buckets reachable from classifier, scored by bucket-specific paths, weighted by bucket-specific reward weights
- Per-bucket bandit (`linucb_per_bucket`) as the default routing strategy
- OLS reward learner and drift detector interact correctly under production conditions — not just in tests
- Warm-start matrix from versioned benchmark consumed on cold start
- Adversarial integration tests for every module-pair contract
- Versioned benchmark artifact (prompts + matrix + model hashes + Ollama version) committed per commit SHA
- Local-only roster: qwen3.5 (Ollama) + granite4.1-8b (Ollama), 2 arms total
- MCP integration with Claude Code: clean success-or-fail returns with metadata
- 200+ real routing decisions through MCP after deploy, with a measurable learning criterion

### Non-Goals (Explicit Deferrals to v3)

- Parallel execution and `queue_depth_norm` wiring
- Cloud agents (claude, codex-cli, gemini-cli, aider, goose, opencode) — adapters stay in codebase, `enabled: false` in agents.yaml
- Fast-lane small model arm (phi-mini class) — task distribution doesn't justify it currently
- Quality predictor activation in reward path (~673 lines, never seen real data)
- Drift detector activation beyond the OLS interaction fix (broader drift detection)
- Brain retrieval / Obsidian context signal as a context feature
- Counterfactual estimation, composer, policy correction live use
- Multi-user session isolation, federated learning across users
- Hosted/SaaS deployment, public API

If a feature is not on the Goals list, it is not in v2. Adding scope mid-implementation is how v1's phantom integrations happened.

---

## 2. Architecture Overview

Task arrives via MCP → keyword classifier emits one of 9 buckets → per-bucket LinUCB selects an arm from the 9-dim context vector + episodic memory bias → selected Ollama model runs the task → 4-layer quality scorer evaluates output → reward computed from per-bucket weights → bandit's per-bucket A/b matrices update through OLS Fix B smoothed transition → episode written to HNSW index + SQLite → result returns to MCP caller with metadata.

```
┌──────────────┐     ┌──────────────┐
│  Claude Code │────▶│  MCP Server  │
└──────────────┘     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │  Classifier  │   ← vocab.py BUCKETS
                     └──────┬───────┘
                            │ bucket
                     ┌──────▼───────┐
                     │ Per-Bucket   │   ← warm-start matrix
                     │   LinUCB     │   ← episodic memory (HNSW)
                     └──────┬───────┘
                            │ arm
                     ┌──────▼───────┐
                     │ Ollama Arm   │
                     │ (qwen/gran)  │
                     └──────┬───────┘
                            │ output
                     ┌──────▼───────┐
                     │ Quality      │   ← vocab.py BUCKETS
                     │ Scorer       │
                     └──────┬───────┘
                            │ score
                     ┌──────▼───────┐
                     │ Reward       │   ← vocab.py BUCKETS
                     │ Calculator   │   ← OLS Fix B smoothed weights
                     └──────┬───────┘
                            │ reward
                     ┌──────▼───────┐
                     │ Bandit       │
                     │ Update       │
                     └──────────────┘
```

vocab.py sits beside this diagram, not in it. Every module reads bucket names, agent IDs, and capability tags from there. No module hardcodes strings.

---

## 3. The vocab.py Contract

### 3.1 Module location

`backend/orchestrator/routing/vocab.py`

### 3.2 Contents

```python
# Single source of truth for routing identifiers.
# Any string used as a bucket, agent ID, or capability tag MUST come from here.
# CI lint enforces no hardcoded matches of these literals elsewhere.

BUCKETS: tuple[str, ...] = (
    "code",
    "debug",
    "plan",
    "research",
    "review",
    "refactor",
    "security",
    "test",
    "general",
)

ENABLED_AGENTS: tuple[str, ...] = (
    "ollama:qwen3.5",
    "ollama:granite4.1-8b",
)

DISABLED_AGENTS: tuple[str, ...] = (
    "ollama:gemma4-e4b",
    "ollama:lfm2",
    "ollama:deepseek-r1",
    "claude",
    "codex-cli",
    "gemini-cli",
    "aider",
    "goose",
    "opencode",
)

CAPABILITY_TAGS: tuple[str, ...] = (
    "code", "debug", "plan", "research", "review",
    "refactor", "security", "test", "general", "explain",
)
```

CAPABILITY_TAGS may include values not in BUCKETS (e.g., `explain`) — this is intentional. Capability tags govern adapter compatibility checks (`router.py:_capable()`); bucket names govern routing and scoring. They are not required to be identical sets.

### 3.3 Enforcement tests

Four reachability/coverage tests must pass before vocab.py is considered correctly enforcing:

1. **Classifier reachability** — for every `bucket` in `BUCKETS`, there exists at least one input string `s` such that `classify_bucket(TaskContext(description=s)) == bucket`. Enumerated explicitly per bucket in `test_vocab_classifier_reachability.py`.

2. **Scoring path coverage** — for every `bucket` in `BUCKETS`, the quality scorer module has a code path that fires when the bucket equals that value. Validated by introspection or by calling the scorer with a stub task per bucket and asserting non-default code path execution.

3. **Reward weight coverage** — for every `bucket` in `BUCKETS`, `reward.BUCKET_WEIGHTS[bucket]` exists and is a valid weight vector.

4. **Warm-start vector coverage** — for every `bucket` in `BUCKETS`, `warm_start._BUCKET_VECTORS[bucket]` exists.

5. **Prior agent subset** — `set(linucb_per_bucket._DEFAULT_PRIORS.keys()) ⊆ set(ENABLED_AGENTS)`. No prior key references a disabled or non-existent agent. Fails immediately on import if violated.

6. **Lint check** — grep-based or AST-based CI check that no `.py` file outside `vocab.py` contains hardcoded string literals matching any value in `BUCKETS` or `ENABLED_AGENTS`. Allow-list: test files asserting against vocab values themselves. `agents.yaml` is exempt — it is the authoritative config source for agent IDs, not a duplication.

### 3.4 Migration

Files that currently hardcode bucket names: `static.py`, `quality.py`, `reward.py`, `warm_start.py`, `linucb_per_bucket.py`. Each gets a single `from routing.vocab import BUCKETS` (or specific names) at the top and all string literals replaced with references. No behavior change — pure refactor.

Same migration for agent IDs across `linucb_per_bucket.py:_DEFAULT_PRIORS` and any `.py` location. `agents.yaml` is config, not code — agent IDs there are the definition and are not subject to the lint check.

---

## 4. The 9 Buckets

### 4.1 Bucket revival statement

**`test` and `refactor` are revived from dead code in v2.** Both had correct downstream scaffolding in v1 — reward weights in `reward.py`, quality scorer paths in `quality.py`, warm-start vectors in `warm_start.py` — but the classifier never emitted either bucket, so every task that should have routed to `test` or `refactor` was misclassified as `code`. The downstream code was unreachable. v2 makes them reachable.

The other 7 buckets (`code`, `debug`, `plan`, `research`, `review`, `security`, `general`) already existed end-to-end. The bucket-name mismatch (classifier emitting `code_generation` / `debugging` while downstream expected `code` / `debug`) is also closed in v2 — vocab.py is now the only place those strings exist.

### 4.2 Classification rules

The classifier (`backend/orchestrator/routing/static.py:classify_bucket`) emits exactly one of `BUCKETS` per task. Rules are keyword-gate based, applied in order, first match wins:

| Bucket     | Trigger keywords (representative, not exhaustive) |
|------------|---------------------------------------------------|
| `debug`    | error, exception, traceback, stack trace, fix the bug, crashing |
| `test`     | write tests, add unit test, test coverage, pytest, test case, assert that |
| `refactor` | refactor, clean up, restructure, extract method, simplify, rewrite this to |
| `security` | vulnerability, CVE, exploit, sanitize, injection, auth bypass, OWASP |
| `review`   | review, audit, critique, code review, look over this |
| `research` | explain, compare, summarize, survey, how does, what is the difference |
| `plan`     | design, architect, strategy, plan, roadmap, approach for |
| `code`     | write, implement, build, create function, generate code |
| `general`  | catch-all when no other rule fires |

Keyword sets must be distinctive enough that the bench prompt completeness gate (§ 8.4) passes for every intended bucket.

`test` and `refactor` classification uses dedicated frozensets added to `context.py`, matching the pattern established by `SECURITY_KEYWORDS` and `REVIEW_KEYWORDS`:

```python
TEST_KEYWORDS = frozenset({
    "write tests", "add test", "unit test", "test coverage", "pytest",
    "test case", "test suite", "assert that", "integration test",
    "mock", "fixture", "test the", "tests for",
})

REFACTOR_KEYWORDS = frozenset({
    "refactor", "clean up", "restructure", "extract method", "simplify",
    "rewrite this", "reorganize", "decompose", "decouple", "rename",
    "move this", "split this",
})
```

Both get corresponding metadata fields in `TaskContext` — `has_test_keywords: float = 0.0` and `has_refactor_keywords: float = 0.0` — with defaults, placed after `queue_depth_norm`. Neither is added to `to_vector()`, preserving d=9.

### 4.3 Scoring paths

Each bucket has a code path in `backend/orchestrator/quality.py`:

- `_score_code` — compilation check, code block presence, import/def/class patterns, syntax closure
- `_score_debug` — variant of `_score_code` with bias toward containing fix-language and pre/post comparisons
- `_score_test` — variant of `_score_code` checking for `def test_`, `assert`, `pytest` patterns
- `_score_refactor` — variant of `_score_code` (no reference-input comparison in v2; v3 hook for before/after diff)
- `_score_security` — checks for specific vulnerability classes, mitigation language, references to mitigations (parameterize, sanitize, validate, bcrypt, argon2)
- `_score_review` — structural feedback patterns, presence of constructive critique markers
- `_score_research` — substance check, citation/reference patterns, comparison structure
- `_score_plan` — sequential structure check, milestone language, scope/risk language
- `_score_general` — generic prose substance check (length, content, embedding similarity)

`_score_security` and `_score_test` and `_score_refactor` and `_score_debug` are all reachable for the first time in v2. They were dead code in v1.

### 4.4 Reward weights

`reward.BUCKET_WEIGHTS` defines per-bucket `(success, quality, speed, cost)` weights. Each bucket's weight vector is committed in vocab-aligned form. The OLS learner refits these weights once `MIN_SAMPLES` observations accumulate per bucket, using OLS Fix B smoothed transition (§ 6).

---

## 5. Agent Roster

### 5.1 Active arms

| Agent ID                  | Model                        | Quantization | RAM   | Role |
|---------------------------|------------------------------|--------------|-------|------|
| `ollama:qwen3.5`          | Qwen3.5 (8B-class)           | Q4_K_M       | ~5GB  | Code, refactor specialist; competitive generalist |
| `ollama:granite4.1-8b`    | IBM Granite 4.1 8B           | Q4_K_M       | ~5GB  | Plan, research, review, structured-output specialist |

Both arms symmetric in size (~8B), both Q4_K_M, both Ollama-served. No fast lane, no escalation arm. The bandit's job is to learn per-bucket preferences between two genuinely-competitive arms.

### 5.2 Disabled arms

All other adapters stay in the codebase with `enabled: false` in `agents.yaml`, each with a one-line comment explaining why:

```yaml
ollama:
  models:
    - id: gemma4-e4b
      enabled: false
      # Lost every bucket in v2 bench (2026-05-21). 9.6GB on disk, worst per-bucket reward.
    - id: lfm2
      enabled: false
      # Quality ceiling too low for routing arm; v1 bench data inconclusive.
    - id: deepseek-r1
      enabled: false
      # 123s avg latency on 16GB; reasoning overhead impractical as default.

claude:
  enabled: false
  # Requires Anthropic API credits; v2 is local-only by design.

codex:
  enabled: false
  # CLI tool with non-deterministic backend swaps; reproducibility risk.

gemini:
  enabled: false
  # CLI tool with non-deterministic backend swaps; reproducibility risk.

aider:
  enabled: false
  # CLI tool, multi-file editor; v3 candidate if multi-file workflows become primary.

goose:
  enabled: false
  # Not benchmarked in v1 oracle set; no validated capability priors.

opencode:
  enabled: false
  # Not benchmarked in v1 oracle set; no validated capability priors.
```

Re-enabling any of these is a config flip — no architectural lift required.

### 5.3 Model hash pinning

`benchmarks/v2/{commit_sha}/roster.json` records:

```json
{
  "ollama_version": "...",
  "agents": [
    {
      "id": "ollama:qwen3.5",
      "ollama_tag": "qwen3.5:8b",
      "blob_sha256": "...",
      "quantization": "Q4_K_M"
    },
    {
      "id": "ollama:granite4.1-8b",
      "ollama_tag": "granite4.1:8b",
      "blob_sha256": "...",
      "quantization": "Q4_K_M"
    }
  ]
}
```

The benchmark harness reads this file before any bench run and asserts `ollama list` output matches the recorded hashes. Mismatch → harness errors loudly. Silent model swaps are a real failure mode (Ollama may pull updated quants under the same tag); this gate detects them.

---

## 6. The OLS / Drift Fix (Fix B)

### 6.1 The bug

The OLS reward learner (`backend/orchestrator/reward_learner.py:_fit`) refits per-bucket reward weights when a bucket accumulates `MIN_SAMPLES` (default 100) observations. The fit is a hard discontinuous replacement: `weights = new_weights`. Predicted reward for every arm in that bucket shifts immediately by the magnitude of the weight delta — not because any arm actually got better or worse, but because the function used to predict reward changed.

The drift detector reads prediction errors and, on a spike, lowers γ (the discount factor) toward γ_min — telling the bandit to forget historical observations and explore aggressively. Because the OLS shift affects every arm's prediction simultaneously, the drift detector sees prediction-error spikes on every arm at once and mass-quarantines every arm in the bucket.

This is a false positive. Nothing in the real world changed. The reward function recalibrated.

### 6.2 The current band-aid

`tests/orchestrator_v2/test_drift_quarantine_integration.py` uses `monkeypatch.setattr(_rl, "MIN_SAMPLES", 10_000)` to push OLS firing far past the test window. The production code is unchanged. The test stopped failing because OLS is prevented from firing in the test environment, not because the false-positive path was closed.

The production false-positive will fire the moment any bucket accumulates 100 real observations. At post-deploy traffic rates, this is weeks away — and v2 is meant to run for hundreds of real episodes. The bug will fire during the v2 lifecycle if not fixed.

### 6.3 Fix B: smoothed weight transition

On every `_fit()` call after `MIN_SAMPLES` has been reached:

```python
β = 1.0 / K               # K = 20
θ_effective = (1 - β) * θ_prev + β * θ_new
θ_prev = θ_effective       # store for next iteration
```

`θ_new` is the freshly-computed OLS weights from the latest observations. `θ_prev` is the weights currently in use. `θ_effective` becomes the new in-use weights.

This is **exponential smoothing against a moving target**, not finite-step convergence to a fixed value. `_fit()` recomputes `θ_new` on every call from a growing observation buffer, so the target shifts slightly each step. K=20 bounds the per-step predicted-reward perturbation to ≤ 5% of the current OLS-shift magnitude — that 5% cap is what keeps the drift detector quiet, not a guarantee of convergence in 20 steps.

K = 20 chosen because:
- Per-step weight delta is ≤ 5% of the full OLS-shift magnitude
- Drift detector absorbs 5% prediction-error perturbations as normal variance (well below quarantine threshold)
- At a steady-state bucket fill rate of ~5 observations/day, the effective weights closely track OLS within a week — fast enough that OLS responsiveness isn't sacrificed

### 6.4 Required tests (both directions)

`tests/orchestrator_v2/test_ols_drift_interaction.py` must include both:

**Positive direction (catches Fix B regression):**
```
Arrange: bandit with one bucket, one arm. Inject 100 observations
         with constant reward (e.g., r=0.7 for all 100).
Trigger: OLS _fit() fires at observation 100.
Assert:  No DriftAlert is raised during the OLS fit + 20-step smoothed
         transition window. Assert arm remains un-quarantined throughout.
# Note: γ-bounds assertions become available in v3 when adaptive γ is wired.
```

**Negative direction (catches missing-degradation regression):**
```
Arrange: bandit with one bucket, two arms. Inject 200 observations of r=0.8
         on arm A and arm B alternately (stable, healthy state).
Trigger: Switch arm A's reward to 0.0 for the next 50 observations.
Assert:  Drift detector DOES quarantine arm A.
         Assert arm B remains un-quarantined throughout.
```

Both tests must pass for the drift module to be considered correct. CI fails loudly if either regresses.

### 6.5 What is explicitly NOT being fixed in v2

The drift detector still does not perform residualization (Fix A from the gamma spec). The OLS-shift false positive is closed via smoothed transition (Fix B), not by making the drift detector explicitly aware of OLS updates. If real-world traffic surfaces edge cases Fix B doesn't cover (e.g., OLS shifts that exceed 5%/step due to high observation variance), residualization is the v3 follow-up.

---

## 7. The Per-Bucket Bandit

### 7.1 Default strategy

Service startup defaults to `strategy="linucb_per_bucket"`. The single-matrix `linucb` strategy stays in the codebase for rollback (§ 11) but is no longer the default.

### 7.2 Context vector

d = 9 dimensions. Features 1–8 carry real signal. Feature 9 (`queue_depth_norm`) is fixed at 0.0 throughout v2 because execution is sequential. It is documented as a reserved hook for v3 parallel execution; preserving d = 9 means no matrix migration is needed when v3 wires the feature.

| # | Feature                  | Range | v2 behavior |
|---|--------------------------|-------|-------------|
| 1 | `word_count_norm`        | [0,1] | active      |
| 2 | `code_keyword_density`   | [0,1] | active      |
| 3 | `is_question`            | {0,1} | active      |
| 4 | `complexity_tier`        | {.33,.67,1} | active |
| 5 | `file_count`             | [0,1] | active      |
| 6 | `has_error_keywords`     | {0,1} | active      |
| 7 | `has_creation_keywords`  | {0,1} | active      |
| 8 | `has_research_keywords`  | {0,1} | active      |
| 9 | `queue_depth_norm`       | [0,1] | **always 0.0 in v2** |

`TaskContext` also carries `has_security_keywords` and `has_review_keywords` as metadata fields. These are not in `to_vector()` (preserving d=9 and the warm-start matrix shape) but are available for the classifier and scorer to use.

### 7.3 Warm-start consumption

On daemon cold start, before serving any traffic:

1. Read `~/.mahoraga-v2/compatibility_matrix.json` (produced by `orch benchmark simulate --save-matrix` against the active commit-pinned bench).
2. For each `(bucket, arm)` cell, inject pseudo-observations into the corresponding per-bucket A/b matrices.
3. Number of pseudo-observations per cell determined by the matrix-generation harness (typically 3–10, calibrated so the bandit treats the warm-start as informative but easily overwritten by real data).
4. Mark warm-start as complete in daemon state.

Two tests verify this:

- **Unit:** `test_warm_start_consumption_unit.py` — feed a known matrix file with non-default values, instantiate the bandit, assert per-bucket A matrices contain non-identity values matching the file's contribution.
- **Integration:** `test_warm_start_consumption_integration.py` — in-process test (no subprocess): instantiate `BanditRouter` with a temp path for state, delete any prior `routing_decisions.db`, route 1 task per bucket, assert UCB scores differ from cold-start-without-matrix defaults. Without this test, the warm-start path can silently no-op.

### 7.4 Bucket isolation

Per-bucket bandit means each bucket has its own A and b matrices. Updates from one bucket must NOT mutate another bucket's matrices.

Test: `test_per_bucket_isolation.py`:
```
Arrange: bandit cold-started, all per-bucket matrices at identity / zero.
Act:     Route 20 code tasks, all selecting qwen, all receiving r=0.9.
Assert:  code's A and b matrices have been updated (non-identity, non-zero).
Assert:  research's A matrix remains identity, research's b remains zero,
         research's UCB scores match cold-start defaults.
         Repeat assertion for all 8 non-code buckets.
```

Bucket isolation is the property v1 could not have because v1 had no buckets at the bandit level. v2 must verify it explicitly.

---

## 8. Benchmark and Warm-Start Artifact

### 8.1 Scope

- 9 buckets × 3 prompts/bucket × 2 arms = **54 runs per bench**
- Forced round-robin (every arm runs every prompt) — no bandit routing during bench
- Scored by the v2 4-layer heuristic quality scorer (bucket-aware paths now reachable)
- Output: compatibility matrix consumed by warm-start

### 8.2 Artifact format

Committed to repo at `benchmarks/v2/{commit_sha}/`:

```
benchmarks/v2/{commit_sha}/
├── prompts.json         # 27 prompts, each with intended_bucket field
├── matrix.json          # compatibility matrix, 9 buckets × 2 arms, raw scores
├── roster.json          # model hashes, Ollama version, agent IDs
└── run_metadata.json    # date, machine, python version, dependencies
```

`{commit_sha}` is the git commit hash at which the bench was run. Pinning to commit (not date) enables exact reproducibility.

### 8.3 Prompt completeness gate

The bench harness, before executing any run, asserts for every prompt:

```python
ctx = TaskContext(description=prompt.text)
assert classify_bucket(ctx) == prompt.intended_bucket, (
    f"Prompt {prompt.id} intended for {prompt.intended_bucket} "
    f"but classifies as {classify_bucket(ctx)}"
)
```

If any prompt misclassifies, the harness errors loudly and the bench does not run. This prevents the failure mode where a "security" prompt classifies as "code" (because it contains the word "implement") and the matrix records a security cell from a code prompt — silently wrong data.

### 8.4 Model hash verification

Before any bench run, harness reads `roster.json` and shells out to `ollama list`. If the SHA of any active agent's blob doesn't match the recorded hash, harness errors with:
```
ERROR: ollama:qwen3.5 hash mismatch.
  Expected: sha256:abc...
  Found:    sha256:def...
  Re-pull the recorded version or re-bench against the new model.
```

This catches silent Ollama upgrades.

### 8.5 Re-benchmark triggers

A new versioned benchmark run is required when:
- Any active arm's model hash changes
- A new arm enters `ENABLED_AGENTS`
- Any bucket's classification rules change in a way that affects existing bench prompts
- The 4-layer quality scorer's logic changes (not weights — logic)
- Any Ollama version bump that changes inference behavior per the changelog

Bench is not re-run for: prior weight tuning, reward function constant tweaks, drift detector parameter changes.

---

## 9. Adversarial Integration Tests

The bucket-name and OLS/drift bugs both lived in contracts between modules with no test exercising the contract. v2 closes this by requiring at least one adversarial integration test per module-pair interaction.

### 9.1 The six pairs

1. **(classifier, scorer)** — Classifier emits a bucket; scorer must have a code path that fires for that bucket. Adversarial: classify a known-security prompt, route through scorer, assert `_score_security` (specifically) was invoked. Mock the scoring functions to record which got called.

2. **(scorer, reward)** — Scorer outputs a quality score; reward calculator uses bucket-specific weights from `BUCKET_WEIGHTS[bucket]`. Adversarial: stub a high-quality output for a security task, assert the reward computation references `BUCKET_WEIGHTS["security"]` (not `["general"]`).

3. **(reward, OLS learner)** — Reward observations stream into the OLS learner; learner refits per-bucket weights. Adversarial: feed 100 constant-reward observations to one bucket's OLS, assert the learner's stored `θ_new` matches the constant-reward optimum.

4. **(OLS learner, drift detector)** — This is the false-positive bug. Adversarial: trigger OLS `_fit()` with smoothed transition active (Fix B); assert drift detector's per-arm `γ` does not drop below `γ_default - 0.05`. (Same as the Fix B positive-direction test from § 6.4.)

5. **(drift detector, quarantine)** — When drift detector signals an arm is drifting hard, quarantine manager removes it from selection. Adversarial: feed degrading rewards to arm A until drift fires, assert arm A is in the quarantine set and `select_arm()` no longer returns it.

6. **(quarantine, bandit selection)** — Bandit's `pick_arm()` must respect the quarantine set. Adversarial: place arm A in quarantine, call `pick_arm()` 50 times across varied contexts, assert arm A never appears in the returned selections.

### 9.2 Discipline

Adding a new module that interacts with any existing module requires adding at least one adversarial integration test against each touched module. This is enforced at code review.

The pattern: "what does module B do that module A might misinterpret?" If you can't think of an adversarial case, the interaction isn't yet understood well enough to ship.

---

## 10. MCP Return Behavior

### 10.1 Contract

Every MCP call to `/mahoraga` returns either:

**Success:**
```json
{
  "status": "success",
  "output": "<model output>",
  "metadata": {
    "arm": "ollama:qwen3.5",
    "bucket": "code",
    "quality_score": 0.87,
    "retry_count": 0,
    "latency_ms": 4823,
    "context_vector": [0.42, 0.31, 0.0, 0.67, 0.0, 0.0, 1.0, 0.0, 0.0]
  }
}
```

**Failure:**
```json
{
  "status": "failure",
  "reason": "all_arms_failed_quality_threshold",
  "metadata": {
    "attempts": [
      {"arm": "ollama:qwen3.5", "quality_score": 0.32, "reason": "below_threshold"},
      {"arm": "ollama:granite4.1-8b", "quality_score": 0.41, "reason": "below_threshold"}
    ],
    "bucket": "research"
  }
}
```

### 10.2 No best-effort garbage

If Mahoraga's quality scorer marks every retry as below threshold, the MCP return is `status: "failure"`. Mahoraga does NOT return a "best of the bad attempts with a warning flag." The caller (Claude Code, in practice) sees a clean failure and falls back to handling the task itself.

This is a behavior decision, not a contract fix. v1 was ambiguous; v2 commits to fail-clean.

### 10.3 Correctness is not claimed

Mahoraga's quality scorer is heuristic. It catches obvious failures (syntax errors, empty outputs, off-topic responses) but cannot detect semantic correctness. The README and MCP documentation must explicitly state: **Mahoraga routes well but does not verify correctness.** The caller is responsible for evaluating outputs.

---

## 11. Rollback

v2 changes the default bandit strategy, the bucket vocabulary, and the OLS learner's update mechanism. If post-deploy something goes wrong and bisecting is impractical, the rollback path must be clear.

### 11.1 Pre-deploy backups

Before merging v2:
- `cp ~/.mahoraga/routing_decisions.db ~/.mahoraga/routing_decisions.db.pre-v2`
- `cp ~/.mahoraga/compatibility_matrix.json ~/.mahoraga/compatibility_matrix.json.pre-v2`
- `cp ~/.mahoraga/episodic_memory.bin ~/.mahoraga/episodic_memory.bin.pre-v2` (if exists)

### 11.2 Config flag

In `config.yaml` or env:
```
MAHORAGA_BANDIT_STRATEGY=linucb_per_bucket  # v2 default
MAHORAGA_BANDIT_STRATEGY=linucb             # v1 single-matrix fallback
```

The single-matrix `linucb` code path remains intact through v2. Switching the flag reverts the bandit strategy without code changes. (Note: single-matrix bandit was operating on broken bucket-name plumbing in v1, so reverting bandit-only doesn't restore the v1 behavior exactly — it just gives a working single-matrix as a known-good fallback.)

### 11.3 Cold-start reset

```bash
orch memory clear           # clears HNSW index + episode metadata
rm ~/.mahoraga/routing_decisions.db
rm ~/.mahoraga/compatibility_matrix.json
orch serve                  # daemon starts fresh, cold bandit, no warm-start
```

Use this if matrix corruption is suspected or if behavior is so divergent from expectation that historical data is more confusing than helpful.

### 11.4 OLS Fix B disable

In emergency, OLS Fix B can be disabled by setting `K = 1`:
```
MAHORAGA_OLS_TRANSITION_STEPS=1   # default: 20
```
With K=1, smoothed transition collapses to hard replacement (i.e., reverts to v1 behavior including the false-positive bug). This exists only as an emergency lever — never set it without a recovery plan.

---

## 12. Test Surface

### 12.1 Required new tests

| Test name                                       | What it asserts                              |
|-------------------------------------------------|----------------------------------------------|
| `test_vocab_classifier_reachability.py`         | Every bucket reachable from `classify_bucket`|
| `test_vocab_scoring_coverage.py`                | Every bucket has a scoring code path         |
| `test_vocab_reward_coverage.py`                 | Every bucket has reward weights              |
| `test_vocab_warm_start_coverage.py`             | Every bucket has warm-start vector           |
| `test_vocab_prior_agent_subset.py`              | All prior keys are enabled agents            |
| `test_vocab_lint.py` (or CI step)               | No hardcoded vocab strings outside vocab.py  |
| `test_per_bucket_isolation.py`                  | Updates to bucket A don't mutate bucket B    |
| `test_warm_start_consumption_unit.py`           | Matrix loaded → A matrices reflect contents  |
| `test_warm_start_consumption_integration.py`    | Cold start with matrix → UCB scores differ   |
| `test_ols_drift_positive_direction.py`          | OLS shift alone does NOT quarantine          |
| `test_ols_drift_negative_direction.py`          | Real degradation DOES quarantine             |
| `test_classifier_scorer_contract.py`            | Classifier output reaches correct scoring path |
| `test_scorer_reward_contract.py`                | Scoring → bucket-specific reward weights     |
| `test_reward_ols_contract.py`                   | Rewards stream into correct bucket's OLS     |
| `test_drift_quarantine_contract.py`             | Drift signal → quarantine set updated        |
| `test_quarantine_bandit_contract.py`            | Quarantined arms never selected              |
| `test_bench_prompt_classification_gate.py`      | Bench prompts classify to intended buckets   |
| `test_mcp_failure_clean.py`                     | All retries fail → MCP returns `status: failure` |

### 12.2 Existing tests

The 1098 tests passing today were largely written against v1's broken-but-internally-consistent code. They protect against regression on the implementation but do not validate v2's intended behavior. All must continue to pass after v2 changes; any that need updating due to vocab.py refactors get updated in the same commit.

### 12.3 Test discipline going forward

- Every new module gets adversarial integration tests against every module it interacts with — at least one per pair.
- No "the test passes so we're good" without exercising the adversarial path.
- Test fixtures using `monkeypatch` to suppress behavior must include a comment explaining what real-world condition the monkeypatch represents, and a corresponding test elsewhere that exercises the un-monkeypatched path. (This rule, applied to v1, would have caught the OLS/drift band-aid.)

---

## 13. Definition of Done

v2 is shipped when all of the following are verifiable:

1. `orch serve` starts cleanly; both Ollama models load; MCP endpoint responds to a sample task.
2. A task submitted via MCP routes through `linucb_per_bucket`, executes on the selected arm, returns a structured result with metadata per § 10.
3. The 54-prompt bench (`orch benchmark simulate --save-matrix` against the committed `benchmarks/v2/{commit_sha}/` artifact) produces a warm-start matrix that the bandit consumes on cold start.
4. All required new tests (§ 12.1) exist and pass.
5. All 1098+ existing tests pass after vocab refactor.
6. After 200 real routing decisions through MCP from Claude Code usage, **at least 3 of the 9 buckets show an estimated mean reward spread (θᵀx, UCB exploration bonus excluded) > 0.1 between `ollama:qwen3.5` and `ollama:granite4.1-8b`**, evaluated at each bucket's representative context vector from `warm_start._BUCKET_VECTORS`. Compute as `(A⁻¹b)ᵀx` for each arm's per-bucket A and b matrices. This is the learning criterion. UCB score (which includes the exploration bonus) is not used here — it can show apparent spread before any learning has occurred. If the bandit hasn't differentiated mean-reward estimates on at least 3 buckets, the reward signal or per-bucket isolation is suspect and v2 isn't actually delivering on its learning claim.
7. README updated with a "v2 Benchmark" section; v1 benchmark section marked `Historical (v1)` with a note explaining the bucket-name bug invalidates per-bucket numbers.
8. `agents.yaml` shows 2 active agents, all others disabled with explanatory comments.

If any item fails, the corresponding section gets debugged before v2 is considered shipped. There is no "ship with known issues" path. v1 already did that; v2 doesn't repeat it.

---

## 14. v3 Deferred List (Explicit)

The following are NOT in v2. They are named here so the spec is unambiguous about scope:

- **Parallel execution + `queue_depth_norm` wiring.** Wired infrastructure stays; activation deferred. v3 enables `asyncio.gather` in the executor, normalizes queue depth as `min(depth, 3) / 3`, adds lock-based bandit updates, adds memory-pressure-aware queueing.
- **Claude (or any cloud) escalation arm.** v3 only if API access becomes available again and the workflow problem (budget caps, user-visible cost, surprise prevention) gets a separate design.
- **Fast-lane small-model arm.** Phi-class. v3 only if task distribution analysis post-v2 shows a meaningful fraction of trivial subtasks that warrant a faster, smaller model.
- **Quality predictor activation.** ~673 lines exist and have never seen real data. v3 turns it on with v2's accumulated episodes as training data.
- **Drift detector activation beyond OLS interaction.** Fix B closes the OLS false positive. The full drift detector (handling non-stationarity from agent degradation, model swaps, network drift in cloud agents) activates when there's enough real-traffic data to validate detection thresholds.
- **Brain retrieval / Obsidian context signal.** Built but inactive. Activates after the per-bucket bandit has stable baseline behavior to compare against.
- **Counterfactual estimation, composer, policy correction.** All built, all dormant. v3+.
- **Residualization (Fix A).** If Fix B's smoothed transition turns out insufficient under high-variance traffic, residualization is the v3 follow-up.
- **Multi-user, federated, hosted.** Out of scope indefinitely; not on any current roadmap.

---

## 15. Sequencing and Ownership

Ordered work items, do not skip:

1. **vocab.py exists, populated, imported by all consumers.** No string literals outside it. CI lint added.
2. **Test/refactor bucket revival.** Classifier rules added, scoring paths wired, reward weights confirmed, warm-start vectors confirmed.
3. **Per-bucket bandit as default.** One-line config change in `app.py`. Verify with integration smoke test.
4. **OLS Fix B implemented in `reward_learner.py`.** K = 20. Both-direction tests added.
5. **Warm-start consumption verified.** Unit + integration tests added.
6. **All adversarial integration tests (6 pairs) implemented.**
7. **Bench harness updated.** Prompt classification gate added. Model hash verification added. 54-prompt set committed.
8. **Bench run committed.** `benchmarks/v2/{commit_sha}/` populated.
9. **agents.yaml trimmed.** Disabled arms explicitly commented.
10. **README updated.** v2 section added, v1 marked historical.
11. **Deploy daemon, route real traffic via MCP.** Accumulate 200 episodes.
12. **200-episode review.** Check UCB spread criterion. If fails, debug per § 13.6.

Items 1–10 are pre-deploy. 11–12 happen post-deploy and are part of v2 being "done."

---

## 16. Out-of-Spec Notes

A few things came up during spec design that are not v2 deliverables but are worth recording so they don't get lost:

- **Mahoraga is not a Claude replacement.** It's an MCP-callable delegation tool that lets Claude (the orchestrator) hand subtasks to local models when local quality is sufficient. The bandit learns the boundary between "local is fine here" and "local isn't trying" implicitly through reward signal.
- **The 2-arm regime makes the bandit's job thin but the infrastructure is sound.** With only 2 arms, much of the bandit's selection collapses to "qwen for code, granite for everything else." That's correct routing; the bandit isn't underperforming, the regime is just simple. When a 3rd arm with genuinely differentiated capability enters (v3+), the bandit has somewhere more interesting to put its weight.
- **The v1 benchmark numbers in the README are technically suspect.** Every task in v1 was scored by the generic prose path with general-bucket reward weights, because the bucket-name mismatch meant per-bucket scoring never reached the per-bucket code paths. Agent-level averages are probably still meaningful (an agent that scores well generically scores consistently); per-bucket distinctions in the v1 matrix are mostly noise. Mark v1 explicitly in the README as "historical, pre-vocab-fix."
- **Phantom integration is a class of bug, not an instance.** Bucket names. OLS/drift interaction. Disabled-agent priors. All the same shape: two modules with an implicit contract, no test exercising the contract, bug stays invisible until forced into light. v2's adversarial integration test discipline is the structural fix. Future Mahoraga work — and arguably any non-trivial system work — should adopt the same discipline.

---

*End of spec. Implementation begins at § 15 item 1.*