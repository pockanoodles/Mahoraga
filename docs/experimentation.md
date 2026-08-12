# Experiments and evaluation

Mahoraga supports live model batches, synthetic router simulations, offline
policy replay, reward reweighting, and execution-verified scoring.

## Keep experiments reproducible

Before a run:

1. Record the goal and expected decision.
2. Fix the prompt bank and enabled roster.
3. Set seeds where the command supports them.
4. Capture raw output with `--output`.
5. Add `--notes` so the run is identifiable in the experiment ledger.

Useful environment variables:

```bash
export MAHORAGA_BANDIT_SEED=42   # set on the server process
export MAHORAGA_PROMPT_SEED=42   # set on the bench client
```

`orch bench report runs` lists live and offline experiment records stored in
`routing_decisions.db`.

## Prompt-bank format

`orch bench run` accepts JSON Lines with one object per prompt:

```jsonl
{"prompt":"Implement clamp(value, low, high).","bucket":"code"}
{"prompt":"Explain why this worker deadlocks.","bucket":"debug"}
```

Blank lines and lines beginning with `#` are ignored.

Validate a bank without inference:

```bash
orch bench validate experiments/prompts.jsonl
```

The repository ignores `experiments/` by default so local prompt banks and
model outputs do not get committed accidentally. Anything that backs a
published number is opted in explicitly by a negation in `.gitignore` and
declared in [`experiments/claims.json`](../experiments/claims.json), so
publishing evidence is a deliberate, reviewable act rather than a side effect
of a run. `orch bench verify` fails if a claimed artifact is missing — see
[Results](RESULTS.md).

## Live batch runs

Start the backend with the seed in its environment:

```bash
MAHORAGA_BANDIT_SEED=42 orch serve
```

Run a batch against the current roster:

```bash
MAHORAGA_PROMPT_SEED=42 orch bench run \
  --prompts experiments/prompts.jsonl \
  --mode bandit \
  --agents ollama:qwen3.5,ollama:granite4.1-8b,ollama:qwen3-14b \
  --base-url http://localhost:8000 \
  --output experiments/results.jsonl \
  --notes "baseline before memory change"
```

Modes:

- `bandit` lets the current policy choose an arm.
- `force-explore` pins every listed arm for every prompt.

Force-explore is not observational: outcomes still update the bandit. Do not
use it as a casual way to seed only part of the roster, because arms left cold
retain larger UCB bonuses.

The default `--agents` list is historical and does not match the current
`agents.yaml`; always pass it explicitly.

## Compatibility reports

Aggregate outcomes by bucket and agent:

```bash
orch bench report compat-matrix
orch bench report compat-matrix --bench-run-id 12 --metric quality
orch bench report compat-matrix --since 2026-07-01 --json
```

Supported metrics include quality, reward, pass rate, latency, tokens, and
tokens per second. Use `--min-samples` to avoid presenting tiny cells as stable
comparisons.

## Execution-verified scoring

The verifier joins a captured bench output to a gold bank by exact prompt text,
extracts Python, appends hidden tests, and runs the combined script with
`python3`.

A gold-bank row contains at least `prompt`, `bucket`, and `tests`:

```jsonl
{"prompt":"Implement clamp(value, low, high). Return only Python.","bucket":"code","tier":"easy","entrypoint":"clamp","tests":"assert clamp(5, 0, 10) == 5\nassert clamp(-1, 0, 10) == 0\nassert clamp(12, 0, 10) == 10"}
```

Generate fresh model outputs with the same prompt bank:

```bash
orch bench run \
  --prompts experiments/prompts_verifiable.jsonl \
  --mode force-explore \
  --agents ollama:qwen3.5,ollama:granite4.1-8b,ollama:qwen3-14b \
  --output experiments/verifiable_results.jsonl \
  --notes "execution scorer comparison"
```

Then score without new inference:

```bash
orch bench report verify \
  --input experiments/verifiable_results.jsonl \
  --bank experiments/prompts_verifiable.jsonl \
  --notes "pass@1 vs heuristic quality"
```

The results file must contain `prompt_full`, `output_full`, and an agent field;
current `orch bench run --output` writes these fields. The report shows pass@1
by bucket and arm, heuristic quality on the same outputs, and rank correlation.

> Security: the verifier runs model-generated code with a wall-clock timeout,
> not a hardened sandbox. Only evaluate trusted local outputs and curated test
> banks.

## Reward reweighting and quality replay

Reweight previously logged components:

```bash
orch bench report reweight \
  --weights 0.30,0.45,0.15,0.10 \
  --notes "increase quality sensitivity"
```

Every component weight must be at least 0.05.

Rescore captured full-text output under available heuristic configurations:

```bash
orch bench report quality-replay \
  --input experiments/results.jsonl \
  --notes "check scorer sensitivity"
```

Both are offline diagnostics. They do not prove that a policy will improve on
new traffic.

## Policy replay

Replay logged contexts under a hypothetical policy:

```bash
orch replay run \
  --strategy linucb_per_bucket \
  --alpha 1.0 \
  --decay 0.98 \
  --limit 500 \
  --notes "screen candidate decay"
```

Replay uses estimators for outcomes of agents that were not selected
historically. Treat it as a screening tool: a losing replay is a warning, while
a winning replay still needs a controlled live comparison.

## Synthetic and v2 benchmarks

Commands that do not require live model inference:

```bash
orch benchmark simulate --tasks 200 --seed 42
orch benchmark ablation --tasks 200 --seed 42
orch benchmark memory-mode --seeds 10 --repeats 5
orch benchmark paraphrase --seeds 10
orch benchmark v2 --gate-only
```

Commands that analyze local state:

```bash
orch benchmark live-report
orch benchmark bootstrap
orch analyze weekly
```

A full v2 run checks the configured Ollama roster and may execute model
workloads:

```bash
orch benchmark v2 --save-matrix
```

The saved `~/.mahoraga-v2/compatibility_matrix.json` is consumed as a
warm-start prior on future router startup.

## Interpreting results

- Report sample counts with every average.
- Compare per-bucket results; a global mean can hide arm specialization.
- Separate forced traffic from organic bandit traffic.
- Distinguish heuristic quality from executable correctness.
- Do not infer causality from an offline replay alone.
- Preserve the seed, git SHA, model tags, raw outputs, and notes for any result
  used to make a roster or routing decision.
