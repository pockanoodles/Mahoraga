# Mahoraga

> **You are looking at the `v2` branch.** Active development of semantic-augmented routing. See [`docs/semantic-routing.md`](docs/semantic-routing.md) for the design spec. The stable v1 release is on the [`main` branch](https://github.com/pockanoodles/Mahoraga/tree/main).

An online bandit routing engine for heterogeneous AI agents. Local-first — local models are the default tier, not the fallback. Learns from every task.
*Mahoraga analyzes, adapts, and overcomes.*

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Last commit](https://img.shields.io/github/last-commit/pockanoodles/Mahoraga)

---

## What It Does

Mahoraga is an agent orchestrator. When you give it a task, it:

1. Classifies complexity via keyword gate into a capability bucket (code, debug, plan, research, general…)
2. Routes to the best agent using a LinUCB contextual bandit that learns from every task
3. Streams the response in real time with markdown rendering
4. Evaluates output quality across four layers — novelty ratio, structural checks, embedding similarity (nomic-embed-text), and length-to-bucket fit
5. Records metrics, updates the bandit, and stores the episode in episodic memory
6. Retries with feedback context or escalates to cloud on failure

Any agent that implements `AgentAdapter` is automatically registered and routed to — see [Adapter Interface](#adapter-interface).

---

## Research Engine

`research` is a dedicated capability bucket in Mahoraga's keyword classifier. Tasks that trigger it — explain, compare, summarise, survey — are routed by the bandit using a context vector that includes `has_research_keywords` (feature 8) and `is_question` (feature 3) as strong signals.

In the oracle compatibility matrix, Gemini CLI scores 0.88 on research tasks and 0.82 on complex reasoning — the highest of any registered agent on those buckets. The bandit learns these priors from real routing decisions and refines them episode by episode. Qwen handles shorter reasoning tasks at zero marginal cost. Escalation to Claude happens only on retry, when the verifier scores the output below threshold.

The result: most research queries route to free agents. The bandit gets better the more it runs. No rules to write, no routing config to maintain.

---

## How It Works

### Task Classification

Tasks are classified by keyword gate into capability buckets (code, debug, plan, research, general, security, test, review, refactor). Short direct tasks route immediately. Complex tasks decompose through the planner first.

### Adaptive Routing

Within each bucket, a **LinUCB contextual bandit** selects the agent.

**The 9-dimensional context vector** — each feature is normalised to [0, 1]:

| # | Feature | Captures |
|---|---------|---------|
| 1 | `word_count_norm` | Task length — longer tasks favour agents with larger context windows |
| 2 | `code_keyword_density` | Fraction of tokens that are code keywords — routes code-heavy tasks to coding agents |
| 3 | `is_question` | 1.0 if phrased as a question — research/explain agents tend to score higher |
| 4 | `complexity_tier` | 0.33 / 0.67 / 1.0 for simple / moderate / complex — complex tasks favour cloud agents |
| 5 | `file_count` | Number of file paths mentioned — multi-file tasks suit git-native agents like aider |
| 6 | `has_error_keywords` | Error/exception/traceback presence — debug-capable agents get an edge |
| 7 | `has_creation_keywords` | Create/build/scaffold language — generative agents favoured |
| 8 | `has_research_keywords` | Explain/compare/summarise language — Gemini and Goose favoured |
| 9 | `queue_depth_norm` | Live pool depth from ExecutionPool, normalized by `max_concurrent` — congested pools bias toward fast local agents |

Per agent, the bandit maintains **A** (9×9 covariance) and **b** (9×1 reward accumulator). At selection time:

```
UCB_a = x'θ_a + α√(x' A_a⁻¹ x)    where θ_a = A_a⁻¹ b_a
```

Four learning layers run in parallel:

- **dLinUCB (γ=0.98)** — discounted updates handle non-stationarity as agents improve or degrade over time
- **Reward Learner** — OLS fits per-bucket reward weights after 100 observations; well-calibrated priors before convergence; simplex projection prevents weight collapse
- **Episodic Memory** — HNSW index (hnswlib) over past context vectors; k=10 nearest-neighbour rewards bias selection at α=0.20; FIFO cap at 10k episodes
- **Double-Run** — when enabled, two candidate agents execute in parallel; the winner is selected by quality score and both outcomes feed the bandit as separate episodes, halving the exploration cost per task

The composite reward: `r = w₁·success + w₂·quality + w₃·speed + w₄·cost` where weights are per-bucket and learnable. A spawn penalty deducts from reward when `agent_spawn_time_ms > 500` — the bandit learns to favour already-warm agents on low-memory hardware.

### Quality Evaluation

After every execution, the validator checks:

- **Code outputs:** compilation check, code block presence, import/def/class patterns, syntax closure
- **General outputs:** substance check — length and content, not padding
- **Embedding similarity:** cosine between prompt and output embeddings via nomic-embed-text (catches off-topic or degenerate outputs)

Outcomes: pass → stream response; retry → same worker with feedback context; escalate → next-best adapter.

**Implicit quality signals** require no explicit feedback: a retry within 5 minutes signals failure (reward 0.0) and accepting an agent's output without change signals success (+0.6 bonus).

### Warm Start

On first startup, if `~/.mahoraga-v2/compatibility_matrix.json` exists (from `orch benchmark simulate --save-matrix`), the bandit injects pseudo-observations instead of cold-starting from zero. Based on PILOT (Panda et al., EMNLP 2025) — reduces early exploration waste. New agents added at runtime are average-initialised from existing arm matrices, ensuring moderate exploration without a regret spike.

### Execution Control

An `ExecutionPool` semaphore caps concurrent tasks at `max_concurrent` (default: 8). Each task slot decrement is reflected immediately in `queue_depth_norm` so the bandit sees live congestion before selecting an agent. Tasks that exceed their timeout (per-agent, per-bucket) are cancelled and the attempt is counted as a failure.

A **budget pacer** enforces a soft cost ceiling per task using a Lagrange multiplier λ. If `avg_cost > ceiling`, λ rises and high-cost agents (codex-cli, claude escalation) are penalised in the reward. A hard per-task limit exists as a backstop — tasks that would exceed it are rejected before dispatch.

**Drift detection and auto-quarantine** monitor each agent's reward distribution against a per-bucket baseline. When an agent's rolling mean drops more than 2σ below expectation, it is quarantined: the bandit stops routing to it and a probe sequence begins. On recovery the agent re-enters the pool with a reduced exploration bonus. This happens automatically, with no manual intervention.

---

## Architecture

```mermaid
graph LR
    User([User]) --> UI[Web UI / MCP]
    UI --> KC[Keyword Classifier]
    KC --> CB[Capability Bucket]
    CB --> LB[LinUCB Bandit]
    EM[Episodic Memory] -->|α=0.20 bias| LB
    RL[Reward Learner] -->|OLS weights| LB
    LB -->|select arm| Agents
    Agents --> ollama[ollama · Qwen3 4B · free]
    Agents --> codex[codex-cli · OpenAI CLI]
    Agents --> aider[aider · git-native]
    Agents --> gemini[gemini-cli · Google]
    Agents --> goose[goose · Block]
    Agents --> opencode[opencode · sst]
    Agents --> cloud[claude · escalation]
    Agents --> Metrics[task_metrics · SQLite]
    Metrics -->|composite reward| LB
    Metrics -->|episode| EM
```

---

## v2 Benchmark

Results from the committed 54-prompt bench set (`benchmarks/v2/32dd2e7/`). Run:
```bash
orch benchmark v2 --save-matrix   # gate → simulation → warm-start matrix
```

### v2 Compatibility Matrix (54 prompts × 2 arms, seed=42)

| Bucket | ollama:qwen3.5 | ollama:granite4.1-8b | Better arm |
|--------|---------------|----------------------|------------|
| code | **0.875** | 0.559 | qwen3.5 |
| test | **0.851** | 0.549 | qwen3.5 |
| plan | **0.811** | 0.524 | qwen3.5 |
| general | **0.776** | 0.498 | qwen3.5 |
| debug | 0.559 | **0.860** | granite4.1-8b |
| refactor | 0.535 | **0.822** | granite4.1-8b |
| security | 0.560 | **0.837** | granite4.1-8b |
| research | 0.533 | **0.814** | granite4.1-8b |
| review | 0.503 | **0.791** | granite4.1-8b |

The split follows a creation vs. analysis axis: qwen3.5 leads on generation-heavy tasks
(code, test, plan), granite4.1-8b leads on structured reasoning tasks (debug, security,
refactor, research). This matrix is written to `~/.mahoraga-v2/compatibility_matrix.json`
and consumed as the bandit's warm-start prior.

The v2 strategy is `linucb_per_bucket` — each bucket maintains its own A/b matrices,
so the bandit can learn different per-arm preferences per bucket rather than averaging
across task types. After 200 real routing episodes the spread criterion (§13 item 6)
will verify that ≥3 buckets show θᵀx spread > 0.1 between the two arms.

---

## Benchmark Results — Historical (v1)

> **Note:** These numbers are from the v1 benchmark and are technically suspect.
> In v1, a bucket-name mismatch in the reward calculator caused every task to be
> scored with `general`-bucket weights regardless of its actual bucket.
> Per-bucket columns in the matrix below are mostly noise — they reflect the generic
> prose scorer, not bucket-specific evaluation. Agent-level averages (the `Avg` column)
> are still broadly meaningful. The v2 bench above supersedes this data.

### Agent × Bucket Quality Matrix

Measured quality scores from 192 tasks (8 agents × 24 prompts, forced round-robin). Each agent ran every prompt — no routing bias. Scored by the 4-layer heuristic quality system.

| Agent | Code | Research | Plan | Review | Security | Refactor | General | Avg |
|-------|------|----------|------|--------|----------|----------|---------|-----|
| ollama:qwen3-4b | **0.906** | 0.739 | 0.851 | 0.635 | 0.650 | **0.900** | 0.775 | 0.779 |
| ollama:gemma4-e4b | 0.750 | 0.801 | **0.935** | 0.764 | 0.650 | 0.700 | 0.877 | 0.782 |
| ollama:lfm2 | 0.750 | 0.716 | 0.891 | 0.737 | 0.650 | 0.750 | 0.840 | 0.762 |
| ollama:deepseek-r1 | 0.638 | 0.853 | 0.888 | 0.504 | 0.650 | 0.325 | 0.893 | 0.679 |
| codex-cli | 0.650 | 0.890 | 0.911 | 0.747 | 0.650 | 0.650 | 0.884 | 0.769 |
| gemini-cli | 0.650 | 0.842 | 0.916 | **0.805** | 0.650 | 0.700 | 0.893 | 0.779 |
| goose | 0.650 | **0.911** | 0.923 | 0.742 | 0.650 | 0.700 | 0.889 | 0.781 |
| opencode | 0.650 | 0.899 | **0.924** | 0.753 | 0.650 | 0.650 | 0.892 | 0.774 |

**Best agent per bucket:**

```
code → ollama:qwen3-4b (0.906)     research → goose (0.911)
plan → ollama:gemma4-e4b (0.935)   review → gemini-cli (0.805)
refactor → ollama:qwen3-4b (0.900) general → ollama:deepseek-r1 (0.893)
security → flat at 0.650 across all agents
```

Qwen3 4B dominates code (0.906) and refactor (0.900), beating every cloud agent on those buckets at zero cost and 6.1s average latency — the local 4B model isn't just cheaper, it's measurably better for code generation in this benchmark. Cloud agents (codex-cli, gemini-cli, goose, opencode) cluster between 0.753–0.924 with no single dominant winner; the bandit's job is to learn these marginal per-bucket differences over time. DeepSeek-R1 scores highest on general (0.893) but averages 123.5s latency with an 88% pass rate — the reasoning chain overhead makes it impractical as a default on 16 GB hardware. Security scores are flat at 0.650 across all agents; the quality scorer lacks security-specific signal.

---

### Model Throughput

**Hardware:** MacBook Pro (Nov 2024), M-series, 16 GB unified memory

| Model | Throughput | Avg Latency | Avg Quality | Pass Rate |
|-------|-----------|-------------|-------------|-----------|
| LFM2 | 77.1 t/s | 5.1s | 0.757 | 100% |
| Qwen3 4B Q4_K_M | 33.8 t/s | 6.1s | 0.802 | 100% |
| Gemma4 E4B | 28.6 t/s | 16.8s | 0.779 | 100% |
| DeepSeek-R1 | 17.8 t/s | 123.5s | 0.685 | 88% |
| Qwen2.5 7B Q4 (baseline) | 12–14 t/s | — | — | — |

Qwen3 4B is the Pareto winner — best quality-to-speed ratio. LFM2 is 2× faster but trades ~5 quality points. DeepSeek-R1's reasoning overhead makes it unusable as a default at this memory tier.

Cloud agents (codex-cli, gemini-cli, goose, opencode) average 14–19s per task. Latency is network and API dependent — not a hardware metric.

---

### Routing Strategy Comparison

Strategy comparison over 200 simulated tasks with a ground-truth compatibility matrix:

| Strategy | Mean Reward | Total Regret | β | Sublinear? |
|----------|------------|-------------|---|------------|
| Static (baseline) | 0.8649 | 6.88 | 1.569 | No |
| UCB1 | 0.7524 | 28.69 | 0.950 | No |
| Thompson Sampling | 0.8070 | 17.73 | 1.175 | No |
| **LinUCB** | **0.8049** | **18.38** | **0.659** | **Yes** |

β < 1.0 means sublinear regret — the algorithm converges. LinUCB is the only strategy where per-step regret decreases over time. Early regret: 0.1431/task → Late regret: 0.0887/task.

The oracle compatibility matrix used in simulation aligns with the Phase 2 empirical results — Qwen3 4B's code dominance and Gemini CLI's research strength both appear in the measured data.

---

## Quick Start

```bash
git clone https://github.com/pockanoodles/Mahoraga.git && cd Mahoraga
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen3:4b
orch serve        # starts at localhost:8000
```

> **Note:** `hnswlib` requires a C++ compiler. On macOS: `xcode-select --install`. On Ubuntu: `apt install build-essential`. Episodic memory degrades gracefully if `hnswlib` is not installed — the rest of the system runs normally.

Open http://localhost:8000.

Optional cloud keys (set in `.env` or shell):

```bash
ANTHROPIC_API_KEY=sk-ant-...   # Claude escalation
OPENAI_API_KEY=sk-...          # Codex CLI
GEMINI_API_KEY=...             # Gemini CLI
```

---

## Monitoring

The routing health dashboard reads `routing_decisions.db` directly — no server required.

```bash
orch metrics live              # one-shot snapshot with colors and alert banner
orch metrics live --watch 30   # auto-refresh every 30 seconds
orch metrics live --tail 20    # show more recent decisions in the tail
orch metrics snapshot | jq .   # raw JSON — pipe to jq for specific fields
```

What it shows: rolling reward windows (100 / 500 / all-time), per-agent win rates, quarantine state, budget pacer status, escalation counts, composer shadow delta, and a chronological tail of recent decisions with per-row outcome and latency.

For raw counts without the server:

```bash
sqlite3 ~/.mahoraga-v2/routing_decisions.db \
  "SELECT COUNT(*) total, COUNT(reward) with_outcome FROM decisions;"

sqlite3 ~/.mahoraga-v2/routing_decisions.db \
  "SELECT selected_agent, COUNT(*) n, ROUND(AVG(reward),3) avg_reward \
   FROM decisions GROUP BY selected_agent ORDER BY n DESC;"
```

---

## MCP Server

Mahoraga exposes itself as an MCP server, allowing Claude Code (or any MCP client) to route subtasks through the bandit at runtime.

Key tools:
- `run_task` — dispatch a single task with optional `capability_hint` to skip keyword classification
- `run_batch` — dispatch multiple independent tasks; Mahoraga serializes those with file conflicts
- `routing_stats` — live bandit state: arm counts, rewards, quarantine status
- `agent_status` — health-check all registered agents
- `switch_strategy` — hot-swap the routing strategy (linucb / thompson / ucb1 / static)

The `/mahoraga` skill in Claude Code toggles routing on/off. When on, subtasks that can be expressed as self-contained prompts route to open-source agents (Qwen, Aider, Gemini CLI, Goose, OpenCode) instead of Claude Code subagents. The bandit picks the actual worker — `capability_hint` bypasses the keyword classifier and routes directly into the right UCB arm.

---

## Adapter Interface

Any agent that implements `AgentAdapter` is automatically registered and routed to:

```python
class MyAdapter(AgentAdapter):
    @property
    def name(self) -> str:
        return "my-agent"

    @property
    def worker_id(self) -> str:
        return "my-agent:default"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [AgentCapability(task_type="code", confidence=0.8, cost_usd=0.0)]

    async def health_check(self) -> AgentStatus:
        return AgentStatus(name=self.name, available=True)
```

The `AdapterRegistry` scores all registered adapters by `capability_confidence × (1 / (1 + cost_usd))` and routes to the highest scorer. See `backend/orchestrator/adapters/base.py` for the full interface.

---

## Agent Pool

The agent pool is defined in [`agents.yaml`](agents.yaml) at the project root. Edit the file and restart `orch serve` — no Python required. Setting `enabled: false` removes an agent from the pool without losing its bandit history in the DB.

Capability confidence values in the YAML are warm-start priors. The bandit overwrites them from real routing data as it accumulates.

**Five active arms (mid-2026):**

| Agent | What It Is | Primary buckets | Cost |
|-------|-----------|-----------------|------|
| `ollama:qwen3.5` | Qwen3.5 9.7B Q4_K_M via Ollama | code, refactor, general | Free |
| `ollama:gemma4-e4b` | Gemma 4 E4B via Ollama | plan, research, review | Free |
| `gemini-cli` | Google Gemini CLI | research, explain, long-context | Free tier (1K req/day, 1M ctx) |
| `codex-cli` | OpenAI Codex CLI | code, refactor, test | Free tier |
| `claude` | Anthropic API | all buckets — escalation only | Per-token |

The two local arms are competitive, not a compromise tier. Qwen3.5 9B Q4_K_M beats cloud agents on code and refactor in benchmark data. Gemma covers plan and research with different strengths, giving the bandit a real local choice before it considers cloud. Claude only enters when the budget pacer allows and the verifier has already failed on a cheaper agent.

Each Ollama model spawns four workers internally (planner / fast / coder / general role prompts). The bandit arm is per-model — role selection happens below the bandit in the gateway.

Disabled arms (`aider`, `opencode`, `goose`, `deepseek-r1`, `lfm2`) remain in `agents.yaml` with notes on why and what would re-enable them.

---

## CLI Reference

All commands are under `orch`:

| Command | What it does |
|---------|-------------|
| `orch serve` | Start the FastAPI server at localhost:8000 |
| `orch status [run_id]` | Show active runs or a specific run |
| `orch metrics live` | Routing health dashboard |
| `orch metrics snapshot` | One-shot JSON health dump |
| `orch run ...` | Submit a task or mission |
| `orch replay <episode_id>` | Re-run a stored episode against current agents |
| `orch analyze <run_id>` | Post-hoc analysis of a completed run |
| `orch brain journal` | Write a session journal entry |
| `orch quarantine list` | Show quarantined agents |
| `orch budget status` | Show budget pacer state |
| `orch eval ...` | Run quality evaluation on an output |
| `orch benchmark simulate` | Strategy comparison over synthetic tasks |

Run any command with `--help` for options.

---

## Run the Benchmark

**v2 bench (current):**
```bash
orch benchmark v2                         # gate → simulation → print matrix
orch benchmark v2 --save-matrix           # also write warm-start matrix to ~/.mahoraga-v2/
orch benchmark v2 --write-roster          # capture current Ollama model IDs into roster.json
orch benchmark v2 --gate-only             # verify all 54 prompts still classify correctly
```

**v1 bench (historical):**
```bash
orch benchmark simulate          # strategy comparison, 200 synthetic tasks
orch benchmark ablation          # full ablation study (5 experiments, 5 charts)
orch benchmark pareto-sweep      # sweep (α, γ, β) grid, write tuned_hyperparams.json
orch benchmark live-report       # analyse real routing decisions from SQLite
```

Run `orch benchmark` with no arguments to see all subcommands.

---

## Known Limitations

**No general fan-out.** General task execution is still sequential. Double-run executes two agents in parallel for a single task, but independent tasks in a plan don't fan out concurrently yet. The ExecutionPool semaphore and dependency graph are in place; the executor scheduler isn't.

**Single-user.** No session isolation. The bandit state, routing decisions, and episodic memory are global. Multi-user deployments would need per-session bandit instances.

**Security bucket is underserved.** The 4-layer quality scorer doesn't capture security-specific signal — security tasks score 0.650 across all agents in the Phase 2 benchmark. The keyword classifier catches security prompts, but the quality evaluation can't distinguish a good security answer from a mediocre one.

**DeepSeek-R1 latency on 16 GB.** The reasoning model averages 123.5s per task on this hardware tier. It's registered and routed to, but in practice the bandit learns to avoid it quickly due to the speed penalty in the reward function.

**Quality scoring is heuristic-only.** No LLM-as-judge. The 4-layer scorer (novelty ratio, structural checks, embedding similarity, length-to-bucket fit) works well enough for routing decisions but can't catch subtle correctness issues. This is a deliberate cost tradeoff — zero API cost for quality signal.

**Counterfactual estimation not yet active.** The composer shadow telemetry is recorded and the infrastructure exists, but k-NN counterfactual reward estimation (F3) requires ~500 decisions to be meaningful. It activates automatically once the DB reaches that threshold.

---

## Motivation

The LLM tooling ecosystem has three established layers, each solving a different part of the problem:

**Gateways** (LiteLLM, Bifrost, OpenRouter) normalize provider APIs — unified interface, failover, load balancing across OpenAI/Anthropic/etc. Their routing is rule-based and doesn't adapt.

**Trained routers** (RouteLLM, LLMRouter) learn to route between models, typically a strong/weak pair, using classifiers trained offline on Chatbot Arena preference data. RouteLLM achieves up to 85% cost savings on cloud-to-cloud routing. But the policy is frozen at training time, routes between two model endpoints, and assumes cloud inference throughout.

**Orchestration frameworks** (CrewAI, LangGraph, Microsoft Agent Framework) coordinate multi-agent workflows — they define what agents *do*, not which agent to dispatch for a given task.

The gap: no existing system learns *online* which heterogeneous agent to route to, across a pool that spans local models at zero marginal cost alongside cloud APIs.

Mahoraga extends the trained-router category in three ways: online feedback rather than offline training, N heterogeneous agents (CLI tools, local models, cloud APIs) rather than two model endpoints, and local inference as the default cost tier rather than cloud escalation. The bandit accumulates experience from every routing decision and gets incrementally better — no retraining step, no deployment cycle.

---

## Related Work

**Foundational:**
- **LinUCB** (Li et al., WWW 2010) — the contextual bandit algorithm Mahoraga uses for routing. Originally applied to news article recommendation; the per-arm A/b update rule and UCB exploration bonus are directly adopted here.
- **RouteLLM** (Ong et al., 2024) — the paper that established learned LLM routing as a problem worth solving. Trains 4 routers (matrix factorization, weighted Elo, BERT, causal LLM) on Chatbot Arena preference data. Binary strong/weak routing, offline, cloud-only. Mahoraga extends it to N heterogeneous agents with online learning.

**Online / bandit routing:**
- **BaRP** (Anonymous, ICLR 2026 submission) — multi-objective contextual bandit for LLM routing with REINFORCE policy gradient. Validates the bandit framing; paper only, no runtime. Mahoraga's two-stage bucketing (keyword classifier → per-bucket bandit) should accelerate convergence by narrowing the action space before the bandit runs — analogous to a hierarchical bandit.
- **LLM Bandit** (Li, arXiv 2025) — multi-armed bandit with preference conditioning. Partial online learning; offline pretraining requires full-information labels.
- **MAR** (Zhang et al., 2025) — multi-armed router that avoids full-information supervision.
- **C2MAB-V** (Dai et al., 2024) — combinatorial contextual volatile MAB; online feedback, no preference tuning.

**Cost-aware cascading:**
- **FrugalGPT** (Chen et al., 2023) — LLM cascade: try cheap first, escalate on failure. Sequential rather than learned. Mahoraga's escalation path (Ollama → cloud) implements the same intuition with a bandit replacing the fixed threshold.
- **AutoMix** (2024) — routes to larger LMs based on approximate correctness of smaller LM output. Complementary; Mahoraga could use AutoMix-style correctness detection as a reward signal.

**Warm start and non-stationarity:**
- **PILOT** (Panda et al., EMNLP 2025) — warm-starting LinUCB from preference priors reduces regret by Ω(‖θ*−θ_prior‖²). Mahoraga applies this via the benchmark compatibility matrix (`orch benchmark simulate --save-matrix`).
- **ParetoBandit** (Taberner-Miller et al., March 2026) — geometric forgetting for non-stationary LLM routing. Mahoraga's dLinUCB discount factor (γ=0.98) applies the same mechanism.

**Context:**
- **Bouneffouf & Feraud — "Bandits, LLMs, and Agentic AI"** (AAAI 2026 Tutorial, IBM Research) — survey of how bandit algorithms support adaptive decision-making in agentic systems. Validates the research direction.

What no existing paper addresses: local hardware state as a routing context feature, HNSW episodic memory for prompt-level priors, and OLS-learned reward weights from implicit user signals.

---

## References

```
Li, L., Chu, W., Langford, J., & Schapire, R. E. (2010).
  A Contextual-Bandit Approach to Personalized News Article Recommendation.
  WWW 2010. [The LinUCB paper]

Ong, I., Almahairi, A., Wu, V., et al. (2024).
  RouteLLM: Learning to Route LLMs with Preference Data.
  arXiv:2406.18665. https://github.com/lm-sys/RouteLLM

Anonymous (2025/2026).
  Learning to Route LLMs from Bandit Feedback: One Policy, Many Trade-offs.
  ICLR 2026 submission. [BaRP]

Li, Z. (2025).
  LLM Bandit: Cost-Efficient LLM Generation via Preference-Conditioned Dynamic Routing.
  arXiv:2502.02743.

Chen, L., Zaharia, M., & Zou, J. (2023).
  FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance.
  arXiv:2305.05176.

Panda, A., et al. (2025).
  PILOT: Preference-Informed LinUCB with Transfer for Online Routing.
  EMNLP 2025.

Taberner-Miller, E., et al. (2026).
  ParetoBandit: Multi-Objective Non-Stationary Routing for Language Models.
  March 2026.

Bouneffouf, D., & Feraud, R. (2026).
  Bandits, LLMs, and Agentic AI.
  AAAI 2026 Tutorial. IBM Research.
```

---

## License

MIT
