# Mahoraga

> Agent-agnostic LLM orchestration framework with online bandit routing. Unifies any AI coding agent — local or cloud — into an intelligent workflow with learned routing, quality evaluation, and real-time visual feedback.

*Named after the adaptive deity from Buddhist mythology — Mahoraga analyzes, adapts, and overcomes.*

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Last commit](https://img.shields.io/github/last-commit/pockanoodles/Mahoraga)

<!-- TODO: record demo GIF after collecting 50+ real routing decisions -->
![demo](docs/demo.gif)

---

## What It Does

Mahoraga is not an agent. It orchestrates agents. When you give it a task, it:

1. Classifies complexity via keyword gate into a capability bucket (code, debug, plan, research, general…)
2. Routes to the best agent using a LinUCB contextual bandit that learns from every task
3. Streams the response in real time with markdown rendering
4. Evaluates output quality via heuristic scoring + embedding similarity
5. Records metrics, updates the bandit, and stores the episode in episodic memory
6. Retries with feedback context or escalates to cloud on failure

Any agent plugs in through the `AgentAdapter` interface.

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

## Why This Exists

Cloud coding agents burn credits on tasks a 4B local model handles fine. Mahoraga routes each task to the right agent — local for the easy stuff, cloud when it matters. The bandit learns your patterns and gets smarter over time.

---

## Benchmark Results

### Model Throughput

**Hardware:** MacBook Pro (Nov 2024), M-series, 16 GB unified memory

| Model | Throughput | Easy | Medium | Hard |
|-------|-----------|------|--------|------|
| Qwen2.5 7B Q4 (baseline) | 12–14 t/s | 23s | 39s | 40s |
| **Qwen3 4B Q4_K_M** | **21–23 t/s** | **12s** | **36s** | **48s** |
| Qwen3 8B Q4 | 12–13 t/s | 27s | 58s | — |

Qwen3 4B in nothink mode is the default — 80% faster than the 7B baseline with comparable quality on short-to-medium tasks.

### Routing Strategy Comparison

Strategy comparison over 200 simulated tasks with a ground-truth compatibility matrix:

| Strategy | Mean Reward | Total Regret | β | Sublinear? |
|----------|------------|-------------|---|------------|
| Static (baseline) | 0.8649 | 6.88 | 1.569 | No |
| UCB1 | 0.7524 | 28.69 | 0.950 | No |
| Thompson Sampling | 0.8070 | 17.73 | 1.175 | No |
| **LinUCB** | **0.8049** | **18.38** | **0.659** | **Yes** |

β < 1.0 means sublinear regret — the algorithm converges. LinUCB is the only strategy where per-step regret decreases over time. Early regret: 0.1431/task → Late regret: 0.0887/task.

Naive model alternation between Ollama models costs ~0.10 reward points per task. Hardware-aware routing (swap penalty + warm/cold detection) eliminates this.

<!-- TODO: embed regret_curve.png once ablation runs clean -->

### Oracle Compatibility Matrix (Ground Truth)

```
simple_chat        → ollama       (0.92)
code_generation    → opencode     (0.85)
code_refactoring   → aider        (0.92)
debugging          → aider        (0.88)
file_operations    → codex-cli    (0.93)
research           → gemini-cli   (0.88)
planning           → gemini-cli   (0.80)
complex_reasoning  → gemini-cli   (0.82)
```

---

## Quick Start

```bash
git clone https://github.com/pockanoodles/Mahoraga.git && cd Mahoraga
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen3:4b
orch serve        # starts at localhost:8000
```

Open http://localhost:8000.

Optional cloud keys (set in `.env` or shell):

```bash
ANTHROPIC_API_KEY=sk-ant-...   # Claude escalation
OPENAI_API_KEY=sk-...          # Codex CLI
GEMINI_API_KEY=...             # Gemini CLI
```

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
| 9 | `queue_depth_norm` | Agent queue fraction — congestion-aware routing avoids overloaded agents |

Per agent, the bandit maintains **A** (9×9 covariance) and **b** (9×1 reward accumulator). At selection time:

```
UCB_a = x'θ_a + α√(x' A_a⁻¹ x)    where θ_a = A_a⁻¹ b_a
```

Three learning layers run in parallel:

- **dLinUCB (γ=0.97)** — discounted updates handle non-stationarity as agents improve or degrade over time
- **Reward Learner** — OLS fits per-bucket reward weights after 100 observations; well-calibrated priors before convergence; simplex projection prevents weight collapse
- **Episodic Memory** — HNSW index (hnswlib) over past context vectors; k=10 nearest-neighbour rewards bias selection at α=0.20; FIFO cap at 10k episodes

The composite reward: `r = w₁·success + w₂·quality + w₃·speed + w₄·cost` where weights are per-bucket and learnable. Swap cost penalty adjusts reward when the bandit switches between Ollama models (3–8s latency hit on 16 GB unified memory).

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
| aider | git-native multi-file editor | refactor, code, test, explain | API cost |
| gemini-cli | Google Gemini CLI | code, explain, research, general | Free tier (Flash) |
| goose | Block's open-source agent | research, general, explain | Free/API (provider-dependent) |
| opencode | sst/opencode, multi-provider | code, refactor, test, explain, general | Free/API |
| claude | Anthropic API (escalation) | all buckets | Per-token |

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

## Related Work

Mahoraga builds on **RouteLLM** (Ong et al., ICLR 2025) — the first learned router for LLM selection — but extends it from offline binary classification to online multi-agent bandit routing with 6+ heterogeneous local/cloud agents. **PILOT** (Panda et al., EMNLP 2025) demonstrated that warm-starting LinUCB from preference priors reduces regret by Ω(‖θ*−θ_prior‖²); Mahoraga applies this via the benchmark compatibility matrix. **BaRP** showed that reward shaping with swap-cost awareness stabilizes routing under hardware constraints; the β_swap term in Mahoraga's reward function is a direct application. **ParetoBandit** (Taberner-Miller et al., March 2026) introduced geometric forgetting for non-stationary LLM routing; Mahoraga's dLinUCB (γ=0.97) is the same mechanism.

What no existing paper addresses: local hardware state as a routing context feature, HNSW episodic memory for prompt-level priors, and OLS-learned reward weights from implicit user signals.

---

## Roadmap

- [x] Ollama local inference with quality scoring
- [x] Claude API (Haiku → Sonnet → Opus escalation chain)
- [x] `AgentAdapter` interface with capability-based routing
- [x] Codex CLI, Aider, OpenCode, Gemini CLI, Goose adapters
- [x] Real-time web UI with vine chart task visualization
- [x] Per-agent, per-session cost tracking
- [x] LinUCB bandit routing with dLinUCB, episodic memory, reward learner
- [x] Benchmark suite (strategy comparison, ablation, pareto sweep, live report)
- [x] Warm-start from compatibility matrix
- [x] Implicit quality signals (retry detection → reward signal)
- [x] MCP server — expose orchestration as MCP tools (`run_task`, `run_batch`, `routing_stats`, `recent_decisions`)
- [ ] Native macOS dashboard ([Noctis](https://github.com/pockanoodles/noctis))
- [ ] Multi-user session isolation
- [ ] Skill marketplace

---

## License

MIT
