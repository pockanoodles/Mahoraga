# Configuration

Mahoraga reads its agent roster from `agents.yaml`, optional process settings
from `.env`, and persistent operator preferences from
`~/.mahoraga-v2/config.json`.

## Agent roster

The root [`agents.yaml`](../agents.yaml) file is the roster source of truth.
Restart `orch serve` after changing it.

### Ollama configuration

```yaml
ollama:
  base_url: "http://localhost:11434"
  roles: [planner, fast, coder, general]
  models:
    - id: qwen3.5
      model: qwen3.5:latest
      enabled: true
      max_ctx: 131072
      warm: true
      options: {}
      extra_payload: {think: false}
      capabilities:
        code: 0.88
        general: 0.82
```

| Field | Meaning |
| --- | --- |
| `base_url` | Ollama endpoint; `OLLAMA_BASE_URL` overrides it |
| `roles` | Worker roles created for every enabled model |
| `id` | Stable arm identifier, exposed as `ollama:<id>` |
| `model` | Exact Ollama model tag |
| `enabled` | Omit or set `true` to register the arm |
| `max_ctx` | Optional context limit |
| `warm` | Pre-warm the model's `general` worker at startup |
| `options` | Ollama generation options |
| `extra_payload` | Additional Ollama request fields |
| `capabilities` | Cold-start capability confidence values from 0 to 1 |

Capability values are priors, not permanent routing rules. The bandit learns
from real outcomes and persists that history even if an arm is later disabled.

The committed enabled roster is:

- `ollama:qwen3.5`
- `ollama:granite4.1-8b`
- `ollama:qwen3-14b`

### Cloud and CLI adapters

The `claude`, `codex`, `gemini`, `aider`, `opencode`, and `goose` sections use
the same `enabled` and `capabilities` pattern. They are disabled in the
committed configuration.

Enabling an adapter also requires its runtime:

| Adapter | Additional requirement |
| --- | --- |
| Claude | `ANTHROPIC_API_KEY` |
| Codex | Codex CLI and its authentication |
| Gemini | Gemini CLI and `GEMINI_API_KEY` |
| Aider | Aider CLI; model from `AIDER_MODEL` or `model_default` |
| OpenCode | OpenCode CLI and its authentication |
| Goose | Goose CLI and its authentication |

Setting an API key does not override `enabled: false`.

## Environment file

The service loads `.env` from its working directory. Start from the template:

```bash
cp .env.example .env
```

Do not commit credentials.

### Core settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `MAHORAGA_WORKDIR` | Current directory | Worker subprocess directory |
| `MAHORAGA_MAX_CONCURRENT` | `3` | Global execution limit, clamped to 1–32 |
| `MAHORAGA_TASK_TIMEOUT` | `120` | Task timeout in seconds |
| `MAHORAGA_BANDIT_SEED` | Unset | Seed Python and NumPy routing randomness |
| `MAHORAGA_PROMPT_SEED` | Unset | Seed `orch bench run` prompt shuffling |

### Memory

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAHORAGA_MEMORY_MODE` | `semantic` | `semantic`, `keyword`, or `off` |
| `MAHORAGA_MEMORY_ALPHA` | `0.20` | Memory bias blend in the range 0–1 |
| `MAHORAGA_MEMORY_CONFIDENCE_WEIGHTED` | `false` | Weight bias by neighbour confidence |
| `MAHORAGA_MEMORY_ALPHA_PER_BUCKET` | Unset | JSON object of bucket-specific alphas |

Example:

```bash
MAHORAGA_MEMORY_MODE=semantic
MAHORAGA_MEMORY_ALPHA_PER_BUCKET='{"research": 0.0, "code": 0.25}'
```

Semantic episodic memory uses the optional `sentence-transformers` package and
`all-MiniLM-L6-v2`. This is separate from the quality scorer's optional Ollama
embedding check, which uses `nomic-embed-text`.

### Validation and routing safeguards

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAHORAGA_EXEC_GATE` | `on` | Execute code-like outputs before rewarding success |
| `MAHORAGA_JUDGE_GATE` | `off` | Let a free local judge escalate answers it reads as incorrect |
| `MAHORAGA_JUDGE_MODEL` | `qwen3.5` | Which local arm judges when the gate is on |
| `MAHORAGA_DRIFT_ENABLED` | `true` | Enable reward drift detection |
| `MAHORAGA_DRIFT_WINDOW` | `50` | Rolling observations for drift checks |
| `MAHORAGA_DRIFT_SIGMA` | `2.0` | Drift threshold in standard deviations |
| `MAHORAGA_DRIFT_MIN_OBS` | `20` | Minimum observations before drift checks |
| `MAHORAGA_DRIFT_CHECK_INTERVAL` | `10` | Decisions between drift checks |
| `MAHORAGA_QUARANTINE_ENABLED` | `on` | Remove drifted arms from normal selection |
| `MAHORAGA_QUARANTINE_PROBE_INTERVAL` | `50` | Tasks between recovery probes |
| `MAHORAGA_QUARANTINE_AUTO_RELEASE` | `3` | Successful probes required for release |
| `MAHORAGA_QUARANTINE_PROBE_QUALITY_FLOOR` | `0.50` | Probe quality threshold |

Disable the execution gate only for trusted debugging or incompatible output
formats:

```bash
MAHORAGA_EXEC_GATE=off orch serve
```

The gate and offline verifier execute generated Python locally. Do not use
untrusted prompts or outputs without a stronger sandbox.

#### Judge gate

`MAHORAGA_JUDGE_GATE=on` turns on the local→judge→escalate cascade that Phase 5c
measured (1.000 pass@1 at 22% of always-cloud's cost on the verifiable bank).
After a worker's output clears the cheap validator, the arm named by
`MAHORAGA_JUDGE_MODEL` re-reads the prompt and the answer and votes correct or
incorrect; an "incorrect" vote routes the task to the next capable worker.

```bash
MAHORAGA_JUDGE_GATE=on MAHORAGA_JUDGE_MODEL=qwen3.5 orch serve
```

It is **off by default**, unlike the execution gate. The execution gate only
rewrites the bandit's reward, while this one changes which answer you get back
and adds a judge call to every task. A rejected answer is escalated, never
failed: if there is nowhere to escalate the judge is not consulted at all, and
if the escalation target dies the original answer is served rather than the task
blocked. So a judge mistake costs latency and (with a cloud arm enabled) money —
not the answer. See `routing/judge_escalation.py` for the full invariant.

Every consultation is logged to `judge_gate_events` in the decisions DB. Read the
gate's live behaviour back with:

```bash
orch bench report judge-live               # operating point vs the Era-14 bank
orch bench report judge-live --json        # same, machine-readable
```

That report gives escalation rate (overall, per bucket, per agent), judge latency
— the per-task tax, paid whether or not the gate escalates — and how often an
escalation went nowhere and fell back. It deliberately does **not** report
accuracy: organic traffic has no ground truth, which is exactly what the 5c/5d
banks exist to provide. Divergence from the bank's 20% escalation rate is the
finding; agreement is only weak confirmation.

### Budget and escalation

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAHORAGA_BUDGET_CEILING` | `0.05` | Rolling average USD target per task |
| `MAHORAGA_BUDGET_WINDOW` | `100` | Rolling budget window |
| `MAHORAGA_BUDGET_HARD_LIMIT` | `0.50` | Hard USD limit per task |
| `MAHORAGA_BUDGET_ETA` | `0.01` | Budget multiplier learning rate |
| `MAHORAGA_ESCALATION_ENABLED` | `off` | Enable escalation policy |
| `MAHORAGA_ESCALATION_POLICY` | `claude` | `none`, `claude`, `double_run`, or `verify` |
| `MAHORAGA_ALLOW_PAID_ESCALATION` | `off` | Permit paid escalation |

Additional tuning variables are available for escalation variance and gap
thresholds. Inspect their resolved values in code before changing them; the
default committed roster is local-only.

### MCP

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAHORAGA_BASE` | `http://localhost:8000` | FastAPI endpoint used by MCP |
| `MAHORAGA_MCP_RETRIES` | `2` | Retry count for MCP read timeouts |

See [MCP integration](mcp.md).

### Optional integrations

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude adapter and paid escalation |
| `ENABLE_OPUS=1` | Register the escalation-only Opus worker when Claude is enabled |
| `GEMINI_API_KEY` | Gemini CLI adapter |
| `AIDER_MODEL` | Aider model override |
| `TELEGRAM_BOT_TOKEN` | Telegram channel |
| `BRAVE_API_KEY` | Web search integration |

## Persistent preferences

`~/.mahoraga-v2/config.json` is optional. Defaults are:

```json
{
  "active_backend": "ollama",
  "ollama_base_url": "http://localhost:11434",
  "workdir": null,
  "routing_mode": "balanced"
}
```

`routing_mode` accepts `local_first`, `balanced`, or `quality_first`. Runtime
API or MCP changes are written to this file. Environment variables take
precedence where a subsystem supports both forms.

## State files

Mahoraga creates state under `~/.mahoraga-v2/`:

| File | Purpose |
| --- | --- |
| `mahoraga.db` | Missions, plans, runs, tasks, events, and metrics |
| `routing_decisions.db` | Decisions, outcomes, and experiment ledger |
| `bandit_state.json` | Per-bucket bandit matrices |
| `bandit_state.learner.json` | Learned reward weights |
| `episodic_memory.bin` | Handcrafted episodic index |
| `episodic_memory_v2.bin` | Semantic episodic index |
| `episodic_memory.meta.json` | Episode metadata |
| `embedding_cache.sqlite` | Semantic embedding cache |
| `compatibility_matrix.json` | Optional benchmark warm-start priors |
| `tuned_hyperparams.json` | Optional Pareto-sweep output |
| `quarantine.json` | Quarantined agent state |
| `budget_pacer.json` | Cost pacing state |
| `quality_predictor.json` | Optional trained quality model |
| `quality_predictor_meta.json` | Quality model training metadata |
| `server.log` | macOS launchd service log |

Back up this directory before resetting learned routing state. Use
`orch memory clear`, `orch budget reset`, and `orch quarantine clear` for
targeted maintenance instead of deleting the whole directory.
