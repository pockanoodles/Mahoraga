# Mahoraga

Mahoraga is a local-first LLM orchestrator that learns which agent to use for
each task. It classifies the task, selects an agent with a contextual bandit,
executes the work, scores the result, and uses the outcome to improve later
routing decisions.

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Last commit](https://img.shields.io/github/last-commit/pockanoodles/Mahoraga)

Mahoraga currently runs three local Ollama arms:

| Arm | Model | Role |
| --- | --- | --- |
| `ollama:qwen3.5` | `qwen3.5:latest` | Code, planning, and general work |
| `ollama:granite4.1-8b` | `granite4.1:8b` | Tests, reviews, and structured output |
| `ollama:qwen3-14b` | `qwen3:14b` | Diagnostic larger-model arm |

The roster is controlled by [`agents.yaml`](agents.yaml). Cloud and CLI
adapters remain available but are disabled in the committed configuration.

## How it works

1. A keyword classifier assigns the task to a capability bucket such as
   `code`, `debug`, `plan`, `research`, or `review`.
2. A per-bucket discounted LinUCB policy selects from the healthy agents.
3. The selected worker executes the task.
4. Mahoraga computes a reward from success, quality, speed, and cost.
5. The policy and episodic memory are updated for the next decision.

```mermaid
flowchart LR
    Client[Web UI / API / MCP] --> Classifier[Task classifier]
    Classifier --> Bandit[Per-bucket dLinUCB]
    Memory[Episodic memory] --> Bandit
    Bandit --> Pool[Agent pool]
    Pool --> Worker[Selected worker]
    Worker --> Gate[Validation and execution gate]
    Gate --> Reward[Composite reward]
    Reward --> Bandit
    Reward --> Log[(Decision log)]
    Reward --> Memory
```

The default strategy is `linucb_per_bucket`, with a nine-dimensional context
vector and discounted updates for non-stationary agent performance. Semantic
episodic memory uses `all-MiniLM-L6-v2` when its optional dependency is
installed, and falls back to handcrafted context retrieval otherwise.

For code-like buckets, the execution gate is enabled by default. It rejects
outputs that do not execute before they can receive a successful reward. This
is a conservative runnable-code check, not a proof that the answer is
functionally correct.

## Quick start

Requirements:

- Python 3.12+
- Ollama
- A C++ toolchain for `hnswlib`

```bash
git clone https://github.com/pockanoodles/Mahoraga.git
cd Mahoraga

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

ollama pull qwen3.5:latest
ollama pull granite4.1:8b
ollama pull qwen3:14b

orch serve
```

The API starts at `http://127.0.0.1:8000`. Check it with:

```bash
curl http://127.0.0.1:8000/api/health
```

The React UI is served at `/` after it has been built:

```bash
cd frontend
npm ci
npm run build
cd ..
```

Restart `orch serve`, then open `http://127.0.0.1:8000`.

For semantic episodic memory:

```bash
pip install -r requirements-semantic.txt
```

See the [getting-started guide](docs/getting-started.md) for development mode,
health checks, and troubleshooting.

## Interfaces

- **FastAPI and web UI:** `orch serve`
- **MCP stdio bridge:** `python -m backend.mcp.server`
- **CLI operations:** `orch --help`
- **macOS background service:** `orch service --help`

The MCP bridge provides nine tools for task execution, route previews, health,
agent status, routing statistics, and runtime policy changes. See the
[MCP guide](docs/mcp.md).

## Routing and rewards

The context vector captures task length, code density, question shape,
complexity, mentioned files, error/creation/research keywords, and live queue
depth. Each capability bucket keeps independent LinUCB matrices so an agent can
be preferred for one kind of task without dominating unrelated work.

The reward is:

```text
reward = w_success·success + w_quality·quality + w_speed·speed + w_cost·cost
```

Weights are bucket-specific and can be learned from observed outcomes. A budget
pacer, drift detector, and quarantine state protect the live policy. All
routing state is local under `~/.mahoraga-v2/`.

Mahoraga also supports execution-based offline evaluation:

```bash
orch bench report verify --input results.jsonl --bank prompts_verifiable.jsonl
```

This extracts Python from captured model outputs, runs hidden tests, and reports
pass@1 alongside heuristic quality. Only run trusted evaluation data: both the
live execution gate and offline verifier execute generated code locally.

## Monitoring

The metrics commands read the decision database directly; the server does not
need to be running.

```bash
orch metrics live
orch metrics live --watch 30
orch metrics snapshot
orch quarantine list
orch budget status
```

The default decision log is
`~/.mahoraga-v2/routing_decisions.db`.

## Documentation

- [Documentation index](docs/README.md)
- [Getting started](docs/getting-started.md)
- [Configuration](docs/configuration.md)
- [CLI reference](docs/cli-reference.md)
- [MCP integration](docs/mcp.md)
- [Experiments and evaluation](docs/experimentation.md)
- [Design specifications](docs/specs/)
- [Current research state](brain/state/current_state.md)

The files under `docs/specs/` describe designs and research plans. Their status
headers may refer to the point at which they were written; use the operator
guides and running CLI help for current behavior.

## Development

```bash
pytest -m "not slow"
```

The project uses `main` as its single trunk. Changes are made on short-lived
branches, validated in CI, and merged through pull requests.

## Limitations

- The committed roster expects local Ollama models and is tuned for a single
  16 GB development machine.
- State is global to one local user; there is no multi-tenant isolation.
- Quality scoring is useful for routing but remains heuristic outside the
  execution-verified code/debug paths.
- The CLI has a legacy port split: `orch serve`, MCP, and live bench commands
  default to port 8000, while mission/run/task and several older HTTP clients
  currently target port 8001. The [CLI reference](docs/cli-reference.md)
  identifies the affected commands.
- Running generated code is not sandboxed strongly enough for untrusted input.

## Motivation

The LLM tooling ecosystem has three established layers, each solving a different part of the problem:

**Gateways** (LiteLLM, Bifrost, OpenRouter) normalize provider APIs — unified interface, failover, load balancing across OpenAI/Anthropic/etc. Their routing is rule-based and doesn't adapt.

**Trained routers** (RouteLLM, LLMRouter) learn to route between models, typically a strong/weak pair, using classifiers trained offline on Chatbot Arena preference data. RouteLLM achieves up to 85% cost savings on cloud-to-cloud routing. But the policy is frozen at training time, routes between two model endpoints, and assumes cloud inference throughout.

**Orchestration frameworks** (CrewAI, LangGraph, Microsoft Agent Framework) coordinate multi-agent workflows — they define what agents *do*, not which agent to dispatch for a given task.

The gap: no existing system learns *online* which heterogeneous agent to route to, across a pool that spans local models at zero marginal cost alongside cloud APIs.

Mahoraga extends the trained-router category in three ways: online feedback rather than offline training, N heterogeneous agents (CLI tools, local models, cloud APIs) rather than two model endpoints, and local inference as the default cost tier rather than cloud escalation. The bandit accumulates experience from every routing decision and gets incrementally better — no retraining step, no deployment cycle.

## Related work

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

## License

MIT
