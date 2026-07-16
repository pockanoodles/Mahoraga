# Mahoraga — Research Protocol & Experimental Roadmap

**Date:** April 24, 2026
**Author:** Nicole (Kaito) Soeno
**Hardware:** MacBook Pro M4, 16GB unified memory
**Repository:** `pockanoodles/Mahoraga`
**Status:** Active research — UI complete, quality scorer shipped, new agents registered, testing underway

---

## Part 0 — Reproducibility & Stop Conditions

Before running any phase, these must be locked in writing. Without them, the numbers produced are anecdotes, not a result.

### 0.1 Determinism contract

| Control | Value | Rationale |
|---|---|---|
| Bandit RNG seed | `MAHORAGA_BANDIT_SEED=42` | Phase 2 convergence must be reproducible across runs |
| Prompt order seed | `MAHORAGA_PROMPT_SEED=42` | Random order is shuffled once per phase, persisted to disk, replayed verbatim |
| Ollama temperature | per-model as configured (Qwen3 default, DeepSeek-R1 0.6, LFM2 0.3) — **never override at runtime** | Temperature is a hidden confound; freeze it, record it |
| CLI agent flags | Pinned per agent in `configs/agents/*.toml`; record the flag set with each run | Upstream CLI updates change defaults silently |
| `top_p`, `repeat_penalty`, `min_p` | As already configured per model; record in metadata | Same reason as temperature |
| Quantization | Q4_K_M for Qwen3, default for others; record the exact tag | Quant changes quality and speed |
| Ollama server version | Pin + record `ollama --version`; rerun if upgraded mid-phase | Model execution can change between minor versions |
| Hardware | M4 MacBook Pro, 16GB; record thermal state (idle temp, on charger, display state) | M-series throttles under load; battery power changes perf |

### 0.2 Record schema (every task)

Every task — Phase 1 override, Phase 2 natural, Phase 3 counterfactual, Phase 4/5 comparison — writes one row with:

```
task_id, phase, replication_idx, prompt_id, bucket, agent, model, quant,
git_sha, ollama_version, timestamp_utc, mode (bandit|override|ablation),
bandit_seed, prompt_seed, temperature, top_p, repeat_penalty,
wall_time_ms, agent_spawn_time_ms, prompt_eval_t_s, eval_t_s,
tokens_in, tokens_out,
quality_structural, quality_novelty, quality_plan, quality_embed, quality_length,
quality_composite, reward_success, reward_speed, reward_cost, reward_total,
bandit_ucb_score, bandit_context_vector, bandit_variance,
hardware_thermal_state, on_charger (bool), raw_output_path
```

If any column is null on a completed task, the row is invalid and must be rerun. No silent drops.

### 0.3 Per-phase stop conditions

| Phase | Stop when | Fail if |
|---|---|---|
| Phase 1 | 10 replications per (prompt × eligible-agent) cell complete | Any cell has <3 successful completions after 3 retries → investigate that agent, not just skip |
| Phase 2 | Exploration <15% (trailing 50), top-agent-per-bucket stable 50 tasks, reward variance <0.05 | No convergence by task 400 → diagnose, don't extend |
| Phase 3 | 10-15 counterfactual prompts, each with ≥2 alternative agents | Regret estimate variance >0.10 → widen sample |
| Phase 4 | 20 tasks per arm (Mahoraga + Claude Code) complete | Claude Code arm methodology not locked (fresh session? same system prompt? tools on/off?) — **define before running** |
| Phase 5 | 100 tasks per ablation × 4 ablations | Ablation state bleed — must reset bandit/memory/OLS state between ablations; verify with before/after state hash |

### 0.4 Methodology freeze for Phase 4

Phase 4 (Mahoraga vs Claude Code) is the most methodologically fragile phase in the protocol. Lock these before the first task runs:

- **Claude Code invocation:** Fresh session per task? Same chat thread? What system prompt?
- **Tool availability:** Tools on (Claude Code uses Read/Edit/Bash) or tools off (pure completion)?
- **Repository state:** Empty cwd, Mahoraga repo, or a neutral fixture dir?
- **Scoring:** The 5-layer scorer runs on *both* arms' outputs. Claude Code's output is plain text — does it get graded on the same bucket-aware criteria?
- **Cost accounting:** Claude Code API tokens × current Sonnet pricing; record the pricing date.

If we don't freeze this before running, the result is uncomparable to anything.

---

## Part 1 — State of the System

### 1.1 What Exists Today

Mahoraga is a self-hosted, agent-agnostic LLM orchestration framework with online bandit routing. It unifies nine agents — four local Ollama models and five external CLI/API agents — under a single LinUCB contextual bandit that learns from every routing decision. The system runs on a FastAPI backend at `localhost:8000` with a web UI, an MCP server for Claude Code integration, and a SQLite-backed metrics layer.

**Agent roster (9 agents):**

| Agent | Type | Model/Runtime | Cost | Buckets |
|---|---|---|---|---|
| `ollama:qwen3-4b` | Local, Ollama | Qwen3 4B Q4_K_M | Free | code, plan, general, research |
| `ollama:gemma4-e4b` | Local, Ollama | Gemma 4 E4B (Google DeepMind) | Free | code, general, research, review, plan |
| `ollama:deepseek-r1` | Local, Ollama | DeepSeek-R1 0528 Qwen3 8B distill | Free | code, security, review, refactor, research, test |
| `ollama:lfm2` | Local, Ollama | LFM2 8B-A1B (Liquid AI) | Free | plan, general, research |
| `codex-cli` | Subprocess | OpenAI Codex CLI | API cost | code, refactor, test |
| `aider` | Subprocess | git-native editor via Ollama | Free | code, refactor, review |
| `gemini-cli` | Subprocess | Google Gemini CLI | Free tier | code, research, general, plan |
| `goose` | Subprocess | Block's open-source agent | Free/API | code, general |
| `opencode` | Subprocess | sst/opencode | Free/API | code |

**Routing stack:** Keyword classifier → capability bucket (code, plan, general, security, test, research, review, refactor) → LinUCB bandit selects agent within bucket. 9-dimensional context vector. dLinUCB (γ=0.97) with episodic memory (HNSW, α=0.20) and OLS reward learner with simplex projection.

**Benchmark baseline:** LinUCB is the only strategy with sublinear regret (β=0.659). Static routing β=1.569. UCB1 β=0.950. Thompson Sampling β=1.175. Proven over 200 simulated tasks with ground-truth compatibility matrix.

### 1.2 What Changed This Session

**Three new Ollama agents registered and operational:**

| Model | Architecture | Active Params | Measured Speed | Measured Avg Tokens |
|---|---|---|---|---|
| Gemma 4 E4B | Dense transformer + PLE | ~4B effective | 34.7 t/s | 152 |
| DeepSeek-R1 0528 | Dense transformer (distilled from R1 671B) | 8B | 34.4 t/s | 146 |
| LFM2 8B-A1B | Hybrid conv + GQA MoE | 1.5B active / 8.3B total | 31.9 t/s | 151 |

Seed batch: 90/90 pass across all three. 30 tasks each. 4.5-7.3s average wall time.

**Five-layer quality scorer deployed:**

| Layer | What It Measures | Weight | Cost |
|---|---|---|---|
| Structural | Existing length/vocab/sentence scoring | 0.35 | Zero |
| Novelty ratio | Fraction of response tokens not in prompt (post-stopword removal) | 0.25 | Zero |
| Plan detection | Binary penalty if ≥60% short numbered lines AND bucket ≠ plan | 0.20 | Zero |
| Banded embedding | Cosine similarity via nomic-embed-text, sweet spot 0.35-0.80 | 0.10 | Zero (local) |
| Length ratio | Response-to-prompt word ratio vs per-bucket target | 0.10 | Zero |

Additive formula, not multiplicative. No single layer can zero out a response. Weights per bucket. Plan bucket gets an escape hatch — numbered lists ARE the correct output there.

**Embedding endpoint fixed:** `nomic-embed-text` pulled, `/api/embeddings` verified returning 768-dim vectors. Layer 3 is now computing real similarity instead of silently falling back.

**Bench scheduler optimized:** Inverted to agent-major ordering — 1 cold start per agent instead of N. Batch time from ~20+ minutes to ~8 minutes.

### 1.3 Discovered Failures & Findings

These are empirical findings from live testing, not hypotheses. Each one changes how we interpret the data or what we build next.

**Finding 1: 42% of historical data was plan-style restatements.**

Offline validation across 132 historical prompt/output pairs: 56 of 132 were plans-as-answers. Previously scored ~0.9 quality by the old validator. Now scoring 0.56-0.68 under the new scorer. Real substantive answers land 0.85-1.0. Plans in the plan bucket correctly score 0.93 (no penalty). The three lowest-scored outputs were manually verified as genuine plan-style restatements. The three highest were real code answers. Scorer is calibrated correctly.

**Implication:** The bandit was effectively a speed+cost optimizer for its entire history. With quality now continuous and discriminative, every new task produces genuinely different learning signal across agents. This is a before/after inflection point for the research.

**Finding 2: gemini-cli returns planner output, not answers.**

When gemini-cli handles research or general prompts, it decomposes the prompt into numbered steps ("1. Define MoE routing. 2. Explain components...") but never executes them. The plan IS the response. The old validator scored this 1.0 (on-topic, non-empty, structurally valid). The new plan-detection layer catches it. The verifier marked these as "passed" with reward 1.0, actively teaching the bandit that gemini-cli is excellent at research — which is wrong.

**Implication:** The reward history for gemini-cli on research tasks is corrupted. The bandit has learned a false positive. Backfilling corrected rewards over historical data would fix the learned weights, but the simpler approach is to let the new scorer produce correct rewards going forward and let dLinUCB's discount factor (γ=0.97) naturally downweight the bad history. After ~100 new tasks, the old signal has weight 0.97^100 ≈ 0.048. It'll wash out.

**Finding 3: aider treats every prompt as a file operation.**

When aider receives "Write a Python function that finds duplicates," it creates `duplicates.py` on disk and returns the terminal output including file headers, thinking traces, "Applied edit to..." footers, and token counts. It also wrote the function in JavaScript despite the prompt specifying Python. The model string `ollama_chat/qwen3:4b-q4_K_M` triggers an aider warning because aider doesn't recognize the quantization suffix — it needs `ollama_chat/qwen3:4b` without the quant tag.

**Implication:** Aider is a file-editing agent, not a chat agent. It should be deprioritized or excluded from chat-context routing. The aider adapter needs output postprocessing to strip terminal artifacts if it's going to appear in the chat UI. The model string needs the quant suffix stripped.

**Finding 4: codex-cli's 0.97 reward is a 3-row artifact.**

56 routing decisions to codex-cli, but only 3 have rewards recorded. The other 53 never closed their reward loop — tasks routed but the verifier never ran. The "dominant" 0.97 is three data points, not convergence. Similarly, 73 of 198 ollama tasks are missing reward (37% pipeline gap).

**Implication:** The reward pipeline has a silent failure path where tasks that route and complete never get a reward recorded. Likely the `PassthroughVerifier` path when no `ANTHROPIC_API_KEY` is set, where some error condition skips reward assignment entirely. Every unrewarded task is wasted learning signal. At 10 tasks/day, losing 37% means weeks to convergence instead of days.

**Finding 5: Bandit exploration was actually balanced.**

The routing distribution — claude 261, ollama 198, aider 197, goose 114 — is healthy. The earlier interpretation that "the bandit locked onto CLI agents" was wrong; it was caused by a display bug from the DB migration gap (historical rows under `selected_agent = 'ollama'` not matching the new name `ollama:qwen3-4b`). The bandit's exploration parameter is working correctly.

**Finding 6: LFM2 is slower than advertised.**

Liquid AI claims 2x faster than standard transformers on CPU. Measured: 31.9 t/s vs 34.4-34.7 t/s for dense models. LFM2 is the slowest of the four Ollama agents on M4 Apple Silicon. Likely cause: the community Ollama port doesn't include Liquid AI's custom MoE kernel, and Apple Silicon's unified memory architecture may negate the MoE active-parameter speed advantage. LFM2's only theoretical edge (speed) doesn't materialize on this hardware.

**Implication:** LFM2 must compete on quality alone, where it's benchmarked at 3-4B dense tier (MMLU 64.84). The bandit will likely route away from it over time. That's a valid experimental outcome — "non-transformer hybrid architecture does not outperform dense transformers on Apple Silicon under bandit routing" is a publishable negative result.

---

## Part 2 — Research Questions

Each question below is answerable with the infrastructure that now exists. Ordered by priority and dependency.

### Q1: Does the quality scorer change the bandit's learned routing policy?

**Hypothesis:** With quality as a constant (~1.0), the bandit optimized on speed and cost. With quality now continuous (0.4-1.0 range), the bandit should shift traffic away from agents that produce plan-style restatements and toward agents that produce substantive answers.

**Method:** Compare agent distribution before and after the scorer change. The "before" snapshot is the current agent_distribution from the Performance page (aider 197, goose 114, gemini-cli 58, codex-cli 56, etc.). Run 100+ new tasks through the bandit with the new scorer. Take an "after" snapshot. Measure: did the bandit meaningfully reallocate traffic? Did any agent's average reward change by more than 0.1?

**Expected outcome:** gemini-cli's share on research tasks drops. Ollama agents' share increases as their quality scores become competitive once plan-penalty stops inflating competitors.

### Q2: Which agent actually performs best on each capability bucket?

**Hypothesis:** The hand-tuned oracle compatibility matrix (aider dominates refactoring, codex-cli dominates file ops, gemini-cli dominates research) may be wrong. Real measured quality × speed scores may tell a different story, especially now that quality is discriminative.

**Method:** Forced round-robin — run 10 standardized prompts per bucket through every eligible agent. Record quality score, wall time, tokens, success. Build the empirical compatibility matrix: `agent × bucket → avg(quality × speed)`. Compare to the oracle. Where do they agree? Where do they diverge?

**Implementation:** Requires `agent_override` parameter on `run_task` MCP tool (or the `/api/tasks` HTTP endpoint). One optional field that bypasses the bandit and dispatches directly to the specified agent. Still logs everything — wall time, tokens, quality — but with `mode: "override"` so the bandit doesn't learn from forced assignments.

### Q3: Does LinUCB converge on real traffic with the corrected quality scorer?

**Hypothesis:** The 200-task simulation proved β=0.659 (sublinear regret). Real traffic is non-stationary, task distributions shift, and quality scoring is noisier. The question is whether dLinUCB (γ=0.97) converges under these conditions.

**Method:** After 200+ tasks with the new scorer, compute: per-agent average reward (trailing 50-task window), exploration rate over time, estimated regret via `task_hash` matches (same prompt, different agents → compare rewards). Plot convergence curve. Compute β from real data.

**Expected outcome:** Exploration rate ~40-50% in first 50 tasks → <15% after 200. If exploration stays high after 300+ tasks, something is wrong with α or the feature vector.

### Q4: What are the actual tokens-per-second and swap latencies on M4 Apple Silicon?

**Hypothesis:** The quant benchmark phase already produced initial numbers (Gemma 4: 34.7 t/s, DeepSeek-R1: 34.4 t/s, LFM2: 31.9 t/s, Qwen3 4B: 21-23 t/s from prior measurement). These need validation across quant levels and with swap cost measured.

**Method:** For each model × quant combination, run the 3-prompt benchmark (easy/medium/hard). Record eval t/s, prompt eval t/s, cold load time. For swap latency: load model A, run a prompt, immediately route to model B, measure agent_spawn_time_ms. Six unique swap pairs across four models.

**Note on quant selection:** Apple Silicon unified memory means quantization doesn't always improve speed — lower quant can sometimes be slower due to dequantization overhead. The benchmark must confirm whether Q4 is actually faster than Q5 for each model on M4 specifically.

### Q5: Is Mahoraga faster or slower than raw Claude Code?

**Hypothesis:** On easy tasks (simple code gen, chat), Mahoraga routes to free local inference and should be faster + cheaper. On hard tasks (complex multi-file edits), Claude Code's native capabilities may be superior. The crossover point determines the product thesis.

**Method:** Define 20 real-world tasks: 5 easy, 5 medium, 5 hard, 5 non-code. Run each through Mahoraga's MCP tools. Run each by prompting Claude Code directly. Compare: total time, per-task time, quality score, cost.

**This answers the product question.** If Mahoraga is consistently slower with no quality gain, the routing layer is overhead. If it matches Claude Code on hard tasks but beats it on easy tasks (free local inference), the cost-aware routing thesis is validated.

### Q6: Does the retrieval-augmented bandit outperform vanilla dLinUCB?

**Hypothesis:** Episodic memory (HNSW over past prompt embeddings × agent × reward) provides a similarity-weighted prior that reduces early exploration waste on new but similar tasks.

**Method:** After 500+ tasks accumulated, enable the episodic memory layer (α=0.20 blending). Compare convergence speed and final average reward over the next 200 tasks with vs. without retrieval augmentation. A/B test via the strategy switcher.

**This is the architectural differentiator.** RouteLLM can't do this — it's a static offline classifier. Mahoraga learns online from every task, forever, with prompt-level similarity priors. If the retrieval layer measurably improves routing quality, that's the headline result for the paper.

---

## Part 3 — Quality Scorer Specification

### 3.1 Architecture

The quality scorer replaces the old pass/fail validator with a continuous signal. It runs after every task execution, before reward computation. The output feeds directly into the bandit's reward function as the `quality` component.

```
quality = w_struct · structural_score
        + w_novel · novelty_score
        + w_plan  · plan_penalty_score
        + w_embed · embedding_distance_score
        + w_len   · length_ratio_score
```

Additive, not multiplicative. No single layer can zero a response. This is a deliberate design choice — multiplicative scoring causes pathological zeros where one noisy signal kills an otherwise good score.

### 3.2 Layer Specifications

**Layer 1 — Structural Score (weight: 0.35)**

The existing quality validator, preserved as the base layer. Evaluates:

For code outputs:
- Compilation check (`py_compile`, `node --check` where applicable)
- Code block presence (triple-backtick fenced blocks)
- Pattern presence: `import`, `def`, `class`, `function`, `const`, `let`
- Syntax closure: balanced brackets, parentheses, braces
- Score: weighted composite of these checks, normalized 0-1

For general/research outputs:
- Substance check: response length > 50 chars, vocabulary diversity > 20 unique words
- Not padding: ratio of unique words to total words > 0.3
- Sentence structure: at least 2 complete sentences (contains periods or other terminal punctuation)
- Score: composite, normalized 0-1

**Layer 2 — Novelty Ratio (weight: 0.25)**

Measures information gain — does the response introduce new concepts or merely rephrase the prompt?

```python
import re
from collections import Counter

STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
             "being", "have", "has", "had", "do", "does", "did", "will",
             "would", "could", "should", "may", "might", "shall", "can",
             "to", "of", "in", "for", "on", "with", "at", "by", "from",
             "as", "into", "through", "during", "before", "after", "and",
             "but", "or", "nor", "not", "so", "yet", "both", "either",
             "neither", "each", "every", "all", "any", "few", "more",
             "most", "other", "some", "such", "no", "only", "own", "same",
             "than", "too", "very", "just", "that", "this", "these", "those",
             "it", "its", "he", "she", "they", "them", "their", "we", "you",
             "i", "me", "my", "your", "his", "her", "our", "how", "what",
             "when", "where", "which", "who", "why"}

def novelty_ratio(prompt: str, response: str) -> float:
    def tokenize(text: str) -> set[str]:
        words = re.findall(r'[a-z_][a-z0-9_]*', text.lower())
        return {w for w in words if w not in STOPWORDS and len(w) > 2}
    
    prompt_tokens = tokenize(prompt)
    response_tokens = tokenize(response)
    
    if not response_tokens:
        return 0.0
    
    novel = response_tokens - prompt_tokens
    return len(novel) / len(response_tokens)
```

A plan outline like "1. Define MoE routing 2. Explain components" reuses ~80% of prompt vocabulary → novelty ~0.20. A real answer introducing "sparse gating, top-k selection, load balancing, auxiliary loss, capacity factor" → novelty ~0.65. Threshold behavior: <0.25 is strongly penalized (likely reformulation), >0.50 is rewarded (genuine information gain).

**Layer 3 — Plan Detection (weight: 0.20)**

Binary detector for the specific failure mode we observed: agents returning numbered step lists instead of answers.

```python
def plan_detection_score(response: str, bucket: str) -> float:
    if bucket == "plan":
        return 1.0  # Numbered lists ARE correct for planning tasks
    
    lines = [l.strip() for l in response.strip().split('\n') if l.strip()]
    if len(lines) < 3:
        return 1.0  # Too short to be a plan
    
    numbered_lines = sum(1 for l in lines if re.match(r'^\d+[\.\)]\s', l))
    numbered_ratio = numbered_lines / len(lines)
    
    short_lines = sum(1 for l in lines if len(l.split()) < 20)
    short_ratio = short_lines / len(lines)
    
    if numbered_ratio >= 0.60 and short_ratio >= 0.60:
        return 0.0  # This is a plan, not an answer
    elif numbered_ratio >= 0.40:
        return 0.5  # Partially plan-like
    else:
        return 1.0  # Not a plan
```

**Layer 4 — Banded Embedding Distance (weight: 0.10)**

Uses nomic-embed-text (768-dim, via Ollama `/api/embeddings`) to compute semantic distance between prompt and response. Unlike simple cosine similarity (higher = better), this uses a banded scoring where the sweet spot is 0.35-0.80.

```python
import numpy as np

async def embedding_distance_score(prompt: str, response: str) -> float:
    prompt_emb = await get_embedding(prompt)    # 768-dim via nomic-embed-text
    response_emb = await get_embedding(response)
    
    cosine = np.dot(prompt_emb, response_emb) / (
        np.linalg.norm(prompt_emb) * np.linalg.norm(response_emb)
    )
    
    # Banded scoring: too similar = paraphrase, too distant = off-topic
    if cosine > 0.85:
        return 0.3   # Likely a reformulation/paraphrase of the prompt
    elif cosine > 0.80:
        return 0.6   # Borderline — related but may lack novelty
    elif cosine >= 0.35:
        return 1.0   # Sweet spot — related but distinct content
    elif cosine >= 0.20:
        return 0.5   # Getting off-topic
    else:
        return 0.1   # Unrelated to prompt
```

**Why banded and not linear:** A plan outline has maximum cosine similarity to the prompt because it literally contains the same concepts reformulated as steps. A real answer is semantically related but introduces new dimensions — moderate similarity, not maximum. The band catches both failure modes: paraphrases (too high) and hallucinations (too low).

**Fallback:** If embedding endpoint is unavailable, redistribute weight to structural (0.35 → 0.45).

**Layer 5 — Length Ratio (weight: 0.10)**

Compares actual response length to expected length for the bucket type.

```python
EXPECTED_RATIO = {
    "research": 10.0,    # 15-word question → expect ~150-word answer
    "general":  8.0,
    "review":   6.0,
    "security": 8.0,
    "refactor": 4.0,
    "code":     3.0,     # Code can be concise
    "plan":     2.0,     # Plans are naturally shorter
    "test":     4.0,
}

def length_ratio_score(prompt: str, response: str, bucket: str) -> float:
    prompt_words = len(prompt.split())
    response_words = len(response.split())
    expected = EXPECTED_RATIO.get(bucket, 5.0)
    
    if prompt_words == 0:
        return 0.5
    
    actual_ratio = response_words / prompt_words
    score = min(actual_ratio / expected, 1.0)
    return max(score, 0.1)  # Floor at 0.1 — never fully zero
```

### 3.3 Validation Results

Offline backtest over 132 historical prompt/output pairs:

| Category | Count | Old Score (avg) | New Score (avg) | Delta |
|---|---|---|---|---|
| Plan-as-answer | 56 (42%) | ~0.90 | 0.56-0.68 | -0.25 avg |
| Real substantive answers | 64 | ~0.92 | 0.85-1.0 | ~-0.05 |
| Plans in plan bucket | 12 | ~0.88 | 0.93 | +0.05 |

Lowest 3 scores: all genuine plan-style restatements (manually verified). Highest 3 scores: all real code answers (manually verified). The scorer is calibrated correctly — it separates the two populations with minimal overlap.

---

## Part 4 — Testing Protocol

### 4.0 Prerequisites

Before any formal testing. Status reflects the repo as of commit `b757507` and an audit of `~/.mahoraga/routing_decisions.db` on 2026-04-24.

1. ~~**`agent_override` on `run_task`:**~~ **DONE** (commits `90f1b56`, `b757507`). Both MCP `run_task` and `POST /api/task` accept `agent_override: str`. Forced dispatches are logged with `mode: "override"` and excluded from bandit updates.

2. ~~**Integrate layered scorer into passthrough path:**~~ **NOT NEEDED.** Audit finding: the scorer already reaches the reward path independently of the verifier. [`app.py:1461`](backend/orchestrator/service/app.py#L1461) calls `score_quality(req.prompt, output, bucket)` and feeds the `[0,1]` result into `TaskOutcome.quality_score`, which `RewardCalculator.compute()` ([`routing/reward.py:78`](backend/orchestrator/routing/reward.py#L78)) uses to compute `w₁·success + w₂·quality_score + w₃·speed + w₄·cost`. The `VerificationResult.score` from `_PassthroughVerifier` is only consumed for `action` (pass/retry/escalate) routing — never as quality signal. No code change required.

3. **Aider model string fix (REAL):** Strip quantization suffix from the aider env default. Adapter and worker defaults at [`aider_adapter.py:28`](backend/orchestrator/adapters/aider_adapter.py#L28) and [`workers/aider.py:26`](backend/orchestrator/workers/aider.py#L26) are already correct (`ollama_chat/qwen3:4b`). The bug is the env default at [`app.py:248`](backend/orchestrator/service/app.py#L248): `AIDER_MODEL` defaults to `ollama_chat/qwen3:4b-q4_K_M`. Change the default to the unquantized form. **One-line fix.**

4. ~~**DB migration cleanup:**~~ **ALREADY CLEAN.** Audit of `~/.mahoraga/routing_decisions.db` confirms distinct `selected_agent` values are: `aider`, `claude`, `codex-cli`, `gemini-cli`, `goose`, `ollama:deepseek-r1`, `ollama:gemma4-e4b`, `ollama:lfm2`, `ollama:qwen3-4b`, `opencode`. No legacy `'ollama'` or `'codex'` rows exist. No migration needed.

5. **Reward-loop gap (REAL, but debug task):** "73 of 198 ollama tasks missing reward" is a `log_outcome()` not-firing issue. [`decision_log.py:19-40`](backend/orchestrator/routing/decision_log.py#L19-L40) shows `decisions` is a single-row schema written in two phases: `log_decision()` inserts, then `log_outcome()` back-fills `success/latency_s/cost_usd/quality_score/reward`. A row with null `reward` means execution completed but the outcome write was skipped. Investigation task, not an implementation task — find all code paths where a decision is logged but `log_outcome()` can be missed (early returns, exceptions, async cancellation). Fix the leaks before Phase 2, because every rewardless task is lost learning signal.

### 4.1 Prompt Bank

Standardized prompt set across all buckets. Each prompt is tagged with expected bucket, expected complexity tier, and expected output characteristics.

**Code (8 prompts):**
```
C01: "Write a Python function that finds all duplicate elements in a list and returns them as a set"
C02: "Write a bash script that watches a directory for new .csv files and moves them to an archive folder"
C03: "Create a Python decorator that retries a function up to 3 times with exponential backoff"
C04: "Write a Python class that implements a simple LRU cache with get and put methods"
C05: "Write a function that takes a nested JSON object and flattens it into dot-notation keys"
C06: "Write a FastAPI endpoint that accepts a JSON payload with a url field, fetches the URL, extracts all links from the HTML, and returns them as a JSON array"
C07: "Write a Python function that takes a list of integers and returns the two numbers that sum to a target value"
C08: "Write a Python generator that yields Fibonacci numbers up to a given limit"
```

**Research (4 prompts):**
```
R01: "Explain how mixture-of-experts routing works in modern LLMs and why it reduces compute cost"
R02: "Compare SQLite vs PostgreSQL for a single-user local application with less than 100K rows"
R03: "What is the difference between L1 and L2 regularization and when would you use each"
R04: "How does cosine similarity work for comparing text embeddings and what are its limitations"
```

**Plan (2 prompts):**
```
P01: "Plan the steps to set up a CI/CD pipeline for a Python FastAPI project on GitHub Actions"
P02: "Outline the architecture for a local-first note-taking app with sync"
```

**Review (3 prompts):**
```
V01: "Review this code for issues: def get_user(id): return db.query(f'SELECT * FROM users WHERE id = {id}')"
V02: "Review this function for issues: def avg(nums): return sum(nums)/len(nums)"
V03: "What's wrong with this: async def fetch_all(urls): results = [await fetch(u) for u in urls]"
```

**Security (2 prompts):**
```
S01: "What are the OWASP top 3 vulnerabilities for REST APIs and how do you prevent each one"
S02: "How would you prevent prompt injection in an LLM application that takes user input"
```

**Refactor (2 prompts):**
```
F01: "Refactor this into clean functions: data = open('f.csv').read(); rows = data.split('\\n'); result = []; for r in rows: cols = r.split(','); if len(cols) > 2 and int(cols[2]) > 100: result.append(cols[0])"
F02: "Improve this: if x == 1: return 'one' elif x == 2: return 'two' elif x == 3: return 'three' elif x == 4: return 'four' else: return 'other'"
```

**General (3 prompts):**
```
G01: "What are the tradeoffs between microservices and monoliths for a team of 3 developers"
G02: "Explain the CAP theorem in one paragraph"
G03: "What is the difference between concurrency and parallelism, with an example of each"
```

**Total: 24 prompts across 7 buckets.** Enough to run each prompt through every eligible agent per bucket and get meaningful comparative data.

### Phase 1 — Forced Round-Robin (Build the Compatibility Matrix)

**Goal:** Run every prompt through every eligible agent for its bucket. Produce the empirical compatibility matrix.

**Method:** Use `agent_override` to force each prompt to each agent. For each combination, record: quality score (5-layer), wall time, tokens generated, t/s, success (binary).

**Example for R01 ("Explain MoE routing..."):**
```
run_task(R01, agent_override="ollama:qwen3-4b")     → record metrics
run_task(R01, agent_override="ollama:gemma4-e4b")    → record metrics
run_task(R01, agent_override="ollama:deepseek-r1")   → record metrics
run_task(R01, agent_override="ollama:lfm2")          → record metrics
run_task(R01, agent_override="gemini-cli")           → record metrics
```

**Output:** Matrix of `agent × bucket → avg(quality_score)`, `agent × bucket → avg(wall_time)`, `agent × bucket → avg(t/s)`. This replaces the hand-tuned oracle in `oracle.py` with measured values.

**Task count:** ~24 prompts × ~5 eligible agents per bucket (average) = ~120 forced tasks per replication. Target **10 replications per (prompt × agent) cell** for statistical meaning — 5 is underpowered for a paper claim. That's ~1,200 measurement tasks total.

**Wall-time budget (realistic):** The "5s average" assumes Ollama. Mixed-agent reality:
- Ollama agents (4): 4-8s per task
- Codex CLI, Aider, Gemini CLI, Goose, Opencode (5): 20-60s per task, plus cold-start
- Weighted average: **~25-35s per task**. For 1,200 tasks: **8-12 hours of wall time.**

Run in batches via the `orch` CLI overnight, not in one sitting. Budget 2-3 sessions.

**Critical:** Do NOT let the bandit learn from these. `mode: "override"` in the routing log. These are measurement tasks, not training tasks.

### Phase 2 — Natural Bandit Routing (Measure Convergence)

**Goal:** With the corrected quality scorer active, let the bandit route freely and measure whether it converges to the empirical compatibility matrix from Phase 1.

**Method:** Feed the same 24 prompts through the bandit (no override), repeated 10-15 times each in random order. Total: **~250-400 tasks.** The 200-task simulation converged with *ground-truth* compatibility; real traffic has noisier quality and richer context, so budget 1.5-2× the simulation's task count. After every 25 tasks, snapshot:
- Per-agent average reward (trailing 50-task window)
- Per-agent task count (how is traffic distributing?)
- Exploration rate (fraction of decisions where the bandit chose a non-optimal arm — defined as any agent not top-1 by UCB score)
- Per-bucket top agent (does it match Phase 1's measured best?)

**Stop criterion (convergence declared when ALL hold):**
- Exploration rate <15% averaged over the trailing 50 tasks
- Top agent per bucket unchanged for 50 consecutive tasks
- Per-agent trailing reward variance <0.05 (bandit has confident posterior)

If all three are met before task 250, freeze at that point and proceed to Phase 3. If they're not met by task 400, do **not** keep running — diagnose. Non-convergence after 400 tasks is a finding, not a setback, and points to one of the failure modes below.

**Expected behavior:**
- Tasks 1-50: exploration rate 40-50%, agent distribution roughly uniform
- Tasks 50-150: exploration rate dropping, traffic shifting toward Phase 1 winners
- Tasks 150-300: exploration rate <15%, dominant agents per bucket stabilizing
- Final routing policy should approximately match Phase 1 compatibility matrix

**Failure modes to watch for:**
- Exploration stays >30% after 200 tasks → α too high or feature vector not discriminative enough
- Agent rewards not spreading (all agents scoring within ±0.05 of mean) → quality scorer not discriminative on this prompt set, or reward-weight blend is washing out quality
- One agent dominating ALL buckets → reward pipeline bug, or that agent is genuinely best everywhere (unlikely — verify by checking Phase 1 matrix)
- Convergence achieved but routing policy contradicts Phase 1 → reward weights (Part 5) are mis-specified for the active buckets

### Phase 3 — Head-to-Head Counterfactuals

**Goal:** For the most interesting routing decisions from Phase 2, measure what would have happened with a different agent.

**Method:** Identify 10-15 tasks where the bandit made a confident choice (low UCB uncertainty, high exploitation). For each, re-run the same prompt through the top 2-3 alternative agents using `agent_override`. Compare quality scores.

**What this measures:** Regret on real traffic. If the bandit chose agent X and scored 0.85, and agent Y would have scored 0.92, that's 0.07 regret for that decision. Average across all counterfactuals gives empirical average regret — directly comparable to the simulation's per-step regret.

**Publication value:** "Measured per-step regret of X on real traffic, compared to simulated regret of 0.0887." If they're close, the simulation is validated. If real regret is higher, the simulation was optimistic and the gap tells you where the model is wrong.

### Phase 4 — Mahoraga vs Raw Claude Code

**Goal:** Answer the product question. Is Mahoraga faster, cheaper, or better than just using Claude Code directly?

**Method:** Define 20 tasks: 5 easy code, 5 medium code, 5 hard code, 5 non-code. Run each through Mahoraga MCP (`run_task`). Run each by prompting Claude Code directly (no Mahoraga). Compare:
- Wall time per task
- Quality score (apply the same 5-layer scorer to both outputs)
- Cost ($0 for local routing, API cost for Claude Code)
- Total cost across all 20 tasks

**Expected outcome:** Mahoraga wins on easy tasks (free local inference, 2-5s). Claude Code wins on hard tasks (better model quality). The crossover point is the product thesis.

**Cost:** Medium — the Claude Code comparison arm uses API credits. Run once, carefully.

### Phase 5 — Algorithm Ablation

**Goal:** Test whether each algorithmic component (dLinUCB, episodic memory, reward learner, swap penalty) actually improves routing.

**Method:** With baseline routing established from Phase 2, disable one component at a time and re-run 100 tasks:
- dLinUCB → vanilla LinUCB (remove γ discount)
- Episodic memory → no similarity priors (remove HNSW lookup)
- Reward learner → flat weights (remove OLS per-bucket weights)
- Swap penalty → no swap cost (remove β_swap)

For each ablation, measure: average reward, convergence speed, regret vs. baseline.

**Publication value:** "Each of the four components contributes X to the final routing quality. The most impactful is Y." Standard ablation study format.

### Phase 6 — Prompt Variant Experiments (Phase 3 from the Build Roadmap)

**Goal:** Test whether system prompt variants per bucket improve quality, holding the model constant.

**Deferred.** This requires stable routing data from Phases 1-3 as a baseline. Design:
- Per bucket, create 2-3 system prompt variants (e.g., code: strict/concise/verbose; research: free-form/structured/cite-sources)
- Log `variant_id` per task alongside agent and bucket
- Run traffic, measure quality per variant
- Phase two promotes winner-picking to a real second-stage bandit once there's enough signal

**Warning:** The prompt bandit only works with sufficient traffic per variant per bucket per agent. At 10 tasks/day, it starves. Either use the batch harness for synthetic traffic or accept multi-week convergence.

---

## Part 5 — Reward Function Specification

### 5.1 Composite Formula

```
r = w₁ · success + w₂ · quality + w₃ · speed + w₄ · cost - swap_penalty
```

Where weights are per-bucket and learnable after sufficient data:

| Bucket | w₁ (success) | w₂ (quality) | w₃ (speed) | w₄ (cost) |
|---|---|---|---|---|
| code | 0.60 | 0.20 | 0.15 | 0.05 |
| research | 0.35 | 0.45 | 0.10 | 0.10 |
| plan | 0.40 | 0.40 | 0.10 | 0.10 |
| security | 0.55 | 0.35 | 0.05 | 0.05 |
| test | 0.60 | 0.25 | 0.10 | 0.05 |
| review | 0.35 | 0.50 | 0.10 | 0.05 |
| refactor | 0.45 | 0.35 | 0.15 | 0.05 |
| general | 0.45 | 0.25 | 0.20 | 0.10 |

All weights clamped to [0.05, 0.70]. After 100+ tasks per bucket, OLS regression recomputes weights automatically from implicit user signals, normalized to sum to 1.0.

### 5.2 Component Definitions

**Success (0 or 1):** Did the agent return a usable response without hard error? Binary. For code: non-empty response with at least one code block or function definition. For general: non-empty response with >50 characters of substance.

**Quality (0.0 to 1.0):** The 5-layer scorer output from Part 3. Continuous, discriminative, bucket-aware.

**Speed (0.0 to 1.0):** Exponential decay against rolling median:
```python
t_ref = rolling_median(wall_time_ms, window=50)
speed_score = math.exp(-1.0 * wall_time_ms / t_ref)
```
Normalizes against actual system performance. A 3s task is fast when median is 5s, slow when median is 1.5s.

**Cost (0.0 to 1.0):** For local agents: 1.0 (free). For API agents: `1.0 - min(cost_usd / budget_per_task, 1.0)` where budget_per_task defaults to $0.05.

**Swap penalty:** Applied when the bandit switches between Ollama models:
```python
swap_penalty = (agent_spawn_time_ms / t_ref_ms) * 0.1  # β_swap = 0.1
r_adjusted = r_raw - swap_penalty if model_changed else r_raw
```
Only applies to Ollama agents. CLI agents have fixed startup cost that's already captured in wall_time.

### 5.3 Learnable Weights (Phase 3 Feature)

After 100+ tasks per bucket with the corrected quality scorer, the OLS regression automatically recomputes weights:

```python
# Per bucket, fit: user_success ~ w1*success + w2*quality + w3*speed + w4*cost
# user_success is binary: 1 if user didn't retry within 5 min, 0 if they did
# Normalize coefficients to sum to 1.0
# Clamp each to [0.05, 0.70]
# Update REWARD_WEIGHTS[bucket] in-place
# Run every 100 new tasks per bucket
```

This closes the loop: the system learns not just which agent to route to, but what dimensions of quality actually matter for each task type, based on observed user behavior.

---

## Part 6 — Infrastructure Requirements

### 6.1 MCP `agent_override` Parameter

```python
# In run_task MCP tool:
async def run_task(
    prompt: str,
    bucket: str | None = None,     # Optional manual bucket classification
    agent_override: str | None = None,  # Bypass bandit, dispatch directly
) -> dict:
    if agent_override:
        # Dispatch directly, log as mode="override"
        # Bandit does NOT update from this decision
        ...
    else:
        # Normal bandit routing
        ...
```

### 6.2 Batch Runner CLI

The actual CLI entry point is [`orch bench`](backend/orchestrator/cli/commands/bench.py), not `orch test`. Two modes:

```bash
# Phase 2 — natural bandit routing (no override)
orch bench run --prompts experiments/prompts_v1.jsonl --mode bandit \
    --repeats 10 --output experiments/results_phase2.jsonl

# Phase 1 — forced round-robin across all agents
orch bench run --prompts experiments/prompts_v1.jsonl --mode force-explore \
    --repeats 10 --output experiments/results_phase1.jsonl

# Single prompt pinned to a specific agent (smoke test or targeted probe)
orch bench run --prompts one_prompt.jsonl --mode force-explore \
    --agents "ollama:gemma4-e4b" --repeats 1 --limit 1

# Head-to-head comparison across a subset of agents
orch bench run --prompts one_prompt.jsonl --mode force-explore \
    --agents "ollama:gemma4-e4b,ollama:deepseek-r1,gemini-cli" --repeats 3
```

Flags: `--agents` (comma list, default all 9), `--repeats` (reps per pair), `--limit` (cap total tasks, for smoke tests), `--timeout` (per-task timeout seconds, default 180), `--output` (raw results JSONL). The CLI iterates **agent-major** — all prompts through agent A before swapping to agent B — to amortize Ollama cold-start across a block of tasks. In `force-explore` mode the bandit still observes outcomes and updates; the `agent_override` just pins the arm per task.

### 6.3 Reporting CLI

The following subcommands are implemented under `orch bench report`:

| Command | Input | Purpose |
|---------|-------|---------|
| `compat-matrix` | `~/.mahoraga-v2/mahoraga.db` | Aggregate quality, reward, pass rate, latency, tokens, or throughput by bucket and agent |
| `reweight` | `~/.mahoraga-v2/routing_decisions.db` | Recompute existing decisions under an alternate success/quality/speed/cost weight vector |
| `quality-replay` | `orch bench run --output` JSONL | Re-score captured outputs under alternate heuristic-quality configurations |
| `verify` | Bench JSONL plus a gold test bank | Execute captured code against tests and compare pass@1 with heuristic quality |
| `runs` | `~/.mahoraga-v2/routing_decisions.db` | List live and offline experiments recorded in the `bench_runs` ledger |

Examples:

```bash
# Compatibility matrix from live or benchmark task metrics
orch bench report compat-matrix --since 2026-07-01 --metric quality

# Zero-inference reward and quality experiments over existing observations
orch bench report reweight --weights 0.20,0.55,0.20,0.05
orch bench report quality-replay --input experiments/results_phase1.jsonl

# Execution-based correctness over the committed gold bank
orch bench report verify --input experiments/results_verifiable.jsonl

# Audit what has already been run
orch bench report runs --limit 20
```

`quality-replay` and `verify` require JSONL emitted by `orch bench run --output`
with full `prompt_full` and `output_full` values. `verify` additionally needs
`actual_agent` (or one of its supported agent-name fallbacks) and exact prompt
text matching the gold bank. `compat-matrix` supports `--json` and `--csv`;
the other analysis commands support `--json`.

`reweight`, `quality-replay`, and `verify` perform no new model inference, but
they do append an experiment summary to `bench_runs`. The `runs` command reads
that shared ledger so live batches and offline analyses can be reproduced and
compared.

The planned `convergence`, `regret`, `baseline-comparison`, and `ablation`
report subcommands are not implemented.

### 6.4 Verifiable rewards

Verifiable rewards provide two deliberately different signals:

1. **Live execution gate.** After a successful primary response in the `code`,
   `test`, `refactor`, or `debug` bucket, Mahoraga extracts Python code, parses
   it, and runs it with `python3 -c`. Empty output, syntax/import/runtime errors,
   non-zero exit, or an 8-second timeout turns the outcome into a failure. Since
   failed outcomes short-circuit the reward calculator, the bandit receives
   reward `0`. The gate is on by default; set `MAHORAGA_EXEC_GATE=off` (also
   accepts `0`, `false`, or `no`) to disable it.
2. **Offline correctness replay.** The committed
   `experiments/prompts_verifiable.jsonl` bank contains Python assertions for
   `code` and `debug` prompts. `verify` joins captured outputs to the bank by
   exact prompt text, appends the assertions to extracted code, executes the
   script with a 30-second timeout, and reports pass@1 per `(bucket, agent)`.
   The same outputs are scored by the heuristic so the report can show rank
   inversions and Spearman correlation without another inference run.

The layers are not interchangeable. Organic traffic has no hidden tests, so the
live gate proves only "runs without crashing"; wrong-but-runnable code passes.
The offline harness measures correctness only for behavior covered by its gold
assertions. Unmatched prompts are counted as a join-health signal and excluded
from pass@1.

```bash
# 1. Start the service separately: orch serve
# 2. Capture full outputs; the runner sends only each row's `prompt` to the model.
orch bench run \
  --prompts experiments/prompts_verifiable.jsonl \
  --mode force-explore \
  --agents "ollama:qwen3.5,ollama:granite4.1-8b" \
  --repeats 1 \
  --output experiments/results_verifiable.jsonl

# 3. Re-score locally. --bank defaults to experiments/prompts_verifiable.jsonl.
orch bench report verify --input experiments/results_verifiable.jsonl
```

The committed bank is versioned, but newly generated `experiments/*.jsonl`
results are ignored by Git. Keep raw outputs local unless there is a deliberate
reason to publish them.

> **Security constraint:** both layers execute model-generated Python directly
> on the host. The subprocess timeout is the only isolation; filesystem,
> network, process, memory, and CPU access are not sandboxed. Use this workflow
> only with trusted local model outputs.

---

## Part 7 — Success Criteria

### For the Portfolio (Path B — Impress Engineers)

The following artifacts, once produced, constitute a strong technical portfolio piece:

1. **Empirical compatibility matrix** — measured, not hand-tuned. "On MacBook Pro M4 16GB, across N real tasks, the LinUCB bandit learned that [agent X] dominates [bucket Y] with reward Z."

2. **Convergence proof on real traffic** — "dLinUCB converges with regret β=X on non-simulated tasks across 9 heterogeneous agents on consumer hardware."

3. **Architecture comparison** — "Transformer vs. hybrid conv+MoE under online bandit routing: [finding]." Novel axis that no existing paper addresses.

4. **Quality scorer ablation** — "Without discriminative quality scoring, the bandit degrades to a speed+cost optimizer. With the 5-layer scorer, quality variance increases from σ=0.05 to σ=0.18, and the learned routing policy changes by [metric]."

5. **Mahoraga vs Claude Code baseline** — "On easy tasks, Mahoraga routes to free local inference with comparable quality and saves $X. On hard tasks, Claude Code is Y% better. The crossover is at complexity tier Z."

### For the Paper (arXiv Target)

**The competitive claim, made precise:**

> "Online bandit routing with non-stationary adaptation, hardware-aware context features, retrieval-augmented empirical Bayes priors, and learned quality scoring. Routes across 9 heterogeneous agents (4 local, 5 cloud/CLI) on consumer hardware. Measured sublinear regret on real traffic. Learns from your usage, online, forever. No retraining step."

**What no existing paper addresses:** Local hardware state as a routing context feature, HNSW episodic memory for prompt-level priors, OLS-learned reward weights from implicit user signals, and discriminative quality scoring that separates plan-style restatements from substantive answers.

---

## Part 8 — Timeline

| Phase | Dependency | Tasks | Data Produced |
|---|---|---|---|
| **Prerequisites** | None | agent_override, reward pipeline fix, aider model string, DB cleanup | Clean infrastructure |
| **Phase 1** | Prerequisites | ~120 forced round-robin tasks | Empirical compatibility matrix |
| **Phase 2** | Phase 1 | ~120 natural bandit tasks | Convergence curves, routing policy |
| **Phase 3** | Phase 2 | ~30-45 counterfactual tasks | Measured regret on real traffic |
| **Phase 4** | Phase 2 | ~40 tasks (20 Mahoraga + 20 Claude Code) | Head-to-head comparison |
| **Phase 5** | Phase 2 | ~400 tasks (100 per ablation) | Component contribution analysis |
| **Phase 6** | Phase 3 stable | Deferred | Prompt-architecture interaction data |

**Hard stop after Phase 5:** Shift from building to writing. The README becomes the paper. Dense, essay-like, with numbers, convergence charts, and head-to-head comparisons.
