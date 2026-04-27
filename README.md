# Mahoraga

An online bandit routing engine for heterogeneous AI agents. Local-first, research-capable, learns from every task.
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
| 9 | `queue_depth_norm` | Reserved — always 0.0 in current implementation; placeholder for queue-depth routing |

Per agent, the bandit maintains **A** (9×9 covariance) and **b** (9×1 reward accumulator). At selection time:

```
UCB_a = x'θ_a + α√(x' A_a⁻¹ x)    where θ_a = A_a⁻¹ b_a
```

Three learning layers run in parallel:

- **dLinUCB (γ=0.98)** — discounted updates handle non-stationarity as agents improve or degrade over time
- **Reward Learner** — OLS fits per-bucket reward weights after 100 observations; well-calibrated priors before convergence; simplex projection prevents weight collapse
- **Episodic Memory** — HNSW index (hnswlib) over past context vectors; k=10 nearest-neighbour rewards bias selection at α=0.20; FIFO cap at 10k episodes

The composite reward: `r = w₁·success + w₂·quality + w₃·speed + w₄·cost` where weights are per-bucket and learnable. A spawn penalty deducts from reward when `agent_spawn_time_ms > 500` — the bandit learns to favour already-warm agents on low-memory hardware.

### Quality Evaluation

After every execution, the validator checks:

- **Code outputs:** compilation check, code block presence, import/def/class patterns, syntax closure
- **General outputs:** substance check — length and content, not padding
- **Embedding similarity:** cosine between prompt and output embeddings via nomic-embed-text (catches off-topic or degenerate outputs)

Outcomes: pass → stream response; retry → same worker with feedback context; escalate → next-best adapter.

**Implicit quality signals** require no explicit feedback: a retry within 5 minutes signals failure (reward 0.0) and accepting an agent's output without change signals success (+0.6 bonus).

### Warm Start

On first startup, if `~/.mahoraga/compatibility_matrix.json` exists (from `orch benchmark simulate --save-matrix`), the bandit injects pseudo-observations instead of cold-starting from zero. Based on PILOT (Panda et al., EMNLP 2025) — reduces early exploration waste. New agents added at runtime are average-initialised from existing arm matrices, ensuring moderate exploration without a regret spike.

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

## Benchmark Results

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

## Agent Roster

| Agent | What It Is | Capability Buckets | Cost |
|-------|-----------|-------------------|------|
| ollama | Local Qwen3 4B via Ollama | general, plan, explain | Free |
| codex-cli | OpenAI Codex CLI subprocess | code, refactor, test, explain | API cost |
| aider | git-native multi-file editor | refactor, code, test | Free (Ollama default) / API cost |
| gemini-cli | Google Gemini CLI | code, explain, research, general | Free tier (Flash) |
| goose | Block's open-source agent | research, general, explain | Free/API (provider-dependent) |
| opencode | sst/opencode, multi-provider | code, refactor, test, explain, general | Free/API |
| claude | Anthropic API (escalation) | code, general, plan, explain, refactor, test | Per-token |

---

## Run the Benchmark

```bash
orch benchmark simulate          # strategy comparison, 200 synthetic tasks
orch benchmark simulate --warm-start --save-matrix  # with warm-start + export matrix
orch benchmark ablation          # full ablation study (5 experiments, 5 charts)
orch benchmark pareto-sweep      # sweep (α, γ, β) grid, write tuned_hyperparams.json
orch benchmark live-report       # analyse real routing decisions from SQLite
orch benchmark report --json     # machine-readable last-run summary
```

Run `orch benchmark` with no arguments to see all subcommands.

---

## Known Limitations

**Sequential execution.** Tasks run one at a time. The dependency resolution machinery exists (tasks have `ready`/`pending` status) but the executor doesn't fan out concurrently yet. `asyncio.gather` is the planned fix.

**Single-user.** No session isolation. The bandit state, routing decisions, and episodic memory are global. Multi-user deployments would need per-session bandit instances.

**Security bucket is underserved.** The 4-layer quality scorer doesn't capture security-specific signal — security tasks score 0.650 across all agents in the Phase 2 benchmark. The keyword classifier catches security prompts, but the quality evaluation can't distinguish a good security answer from a mediocre one.

**DeepSeek-R1 latency on 16 GB.** The reasoning model averages 123.5s per task on this hardware tier. It's registered and routed to, but in practice the bandit learns to avoid it quickly due to the speed penalty in the reward function.

**Quality scoring is heuristic-only.** No LLM-as-judge. The 4-layer scorer (novelty ratio, structural checks, embedding similarity, length-to-bucket fit) works well enough for routing decisions but can't catch subtle correctness issues. This is a deliberate cost tradeoff — zero API cost for quality signal.

**Congestion-aware routing is wired but inactive.** `queue_depth_norm` (context feature 9) exists in the feature vector but is always 0.0 since tasks run sequentially. It becomes meaningful once parallel execution is implemented.

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
