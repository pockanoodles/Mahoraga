# CLI reference

The editable Python install exposes the `orch` command:

```bash
python -m pip install -e .
orch --help
```

This page maps the current command tree and its runtime prerequisites. Use
`orch <group> <command> --help` for the complete option list and defaults.

## Command tree

```text
orch
├── serve
├── mission {new, show, list}
├── plan {create, show, list, generate, approve}
├── run {start, show, list, cancel}
├── task {list, show, retry, cancel}
├── status
├── events
├── approve
├── reject
├── benchmark
│   ├── simulate
│   ├── report
│   ├── ablation
│   ├── live-report
│   ├── pareto-sweep
│   ├── memory-mode
│   ├── paraphrase
│   ├── bootstrap
│   ├── v2
│   ├── v2-review
│   └── refresh
├── bench
│   ├── run
│   ├── validate
│   └── report {compat-matrix, reweight, quality-replay, verify, runs}
├── eval {ab}
├── rankings
├── agent {add}
├── memory {inspect, clear, backfill}
├── quality {train, eval, predict, inspect, retrain}
├── brain {status, query}
├── metrics {live, snapshot, usage, funnel, resource}
├── budget {status, reset, tune}
├── quarantine {list, clear, add, events}
├── replay {run}
├── analyze
│   ├── composer-counterfactual
│   ├── escalation-roi
│   ├── a3-calibration
│   ├── drift-history
│   ├── override-roi
│   └── weekly
├── agentbench {mine, preflight, run, report, verdict}
└── service {install, uninstall, start, stop, status}
```

There is no `orch benchmark lab`, `orch brain journal`, or
`orch replay <episode_id>` command. Use live batch, brain query, and offline
replay commands described below.

## Server

```bash
orch serve [--host 127.0.0.1] [--port 8000] [--reload]
```

Starts `backend.orchestrator.service.app:app` with Uvicorn. The default server,
web UI, MCP bridge, and live bench client use port 8000.

## Missions and runs

These commands call the FastAPI service:

| Command | Purpose |
| --- | --- |
| `orch mission new` | Create a mission interactively or from options |
| `orch mission show <id>` | Show one mission |
| `orch mission list` | List missions |
| `orch plan create --mission <id>` | Create an empty plan |
| `orch plan generate --mission <id>` | Generate a plan with local Ollama |
| `orch plan approve <id>` | Approve a plan |
| `orch plan show <id>` / `list` | Inspect plans |
| `orch run start <plan_id>` | Start an approved plan |
| `orch run show <id>` / `list` | Inspect runs |
| `orch run cancel <id>` | Cancel a run |
| `orch task list` / `show <id>` | Inspect run tasks |
| `orch task retry <id>` | Retry a task |
| `orch task cancel <id>` | Cancel a task |
| `orch status [run_id]` | Show active runs or one run |
| `orch events <run_id>` | Show run events |
| `orch approve <task_id> --run <run_id>` | Satisfy an approval dependency |
| `orch reject <task_id> --run <run_id>` | Reject an approval dependency |

These legacy clients currently target port 8001; see
[Port behavior](#port-behavior).

## Live batches

`orch bench` drives a running FastAPI service.

```bash
orch bench validate prompts.jsonl

orch bench run \
  --prompts prompts.jsonl \
  --mode bandit \
  --agents ollama:qwen3.5,ollama:granite4.1-8b,ollama:qwen3-14b \
  --base-url http://localhost:8000 \
  --notes "reason for this run"
```

`--mode` is `bandit` or `force-explore`. Do not use force-explore to seed the
live bandit: it can train some arms while leaving others cold and distort UCB
exploration. `--limit`, `--repeats`, `--timeout`, and `--output` control the
batch.

The default agent list in this command predates the current `agents.yaml`.
Pass `--agents` explicitly for reproducible runs.

### Claim verification

`orch bench verify` recomputes every published benchmark figure from the
committed per-case artifact it was derived from, and requires it to round to
exactly the value declared in `experiments/claims.json`. It needs no models,
network, API key, or GPU, exits nonzero on any mismatch, and runs in CI — so a
headline number in the README cannot drift from its data. `--json` emits the
per-metric results. See [Results](RESULTS.md).

### Benchmark reproduction

`orch bench repro` reproduces the headline HumanEval+ cascade benchmark with
the published configuration pinned (local=granite4.1-8b, judge=qwen3.5:latest).
It preflights the environment first; `--preflight-only` checks without
inference, `--smoke` runs the first 5 tasks, `--local-only` skips the
always-cloud baseline.

`--cloud-arm` picks how the cloud arm authenticates: `claude-cli` (default —
the `claude` binary on a Claude subscription, what the published run used) or
`claude` (the Anthropic API with `ANTHROPIC_API_KEY`, no subscription needed).
Same model and prompt framing either way, so routed pass@1 is comparable; the
always-cloud dollar column is not, because the two bill differently. The same
choice applies to the live serving cascade through `MAHORAGA_ESCALATE_TO`.
See the README's
[Reproduce the benchmark](../README.md#reproduce-the-benchmark) section.
Unlike `bench run`, it does not need the FastAPI service — it drives the
workers directly through `bench live-route`.

### Batch reports

| Command | Purpose |
| --- | --- |
| `orch bench report runs` | List live and offline experiment records |
| `orch bench report compat-matrix` | Aggregate agent × bucket outcomes |
| `orch bench report reweight --weights ...` | Recompute logged rewards |
| `orch bench report quality-replay --input ...` | Rescore captured outputs |
| `orch bench report verify --input ... --bank ...` | Run hidden Python tests |
| `orch bench report route-sim --input ... --bench-run-id ...` | Counterfactual routing-vs-baseline Pareto |
| `orch bench report judge-gate --input ... --bench-run-id ...` | Score an LLM-judge escalation gate |
| `orch bench report judge-bank` | Judge discrimination on non-verifiable tasks |
| `orch bench report code-judge --input ...` | Replay the generated-test code judge |
| `orch bench report reward-judge` | Reward-fidelity replay: legacy vs oracle vs synthetic-judge rewards |

See [Experiments and evaluation](experimentation.md) for file formats and
safety notes.

## Offline benchmarks

`orch benchmark` contains synthetic studies and benchmark harnesses:

| Command | Server/Ollama needed | Purpose |
| --- | --- | --- |
| `simulate` | No | Compare static, UCB1, Thompson, and LinUCB |
| `ablation` | No | Run routing ablation studies and charts |
| `live-report` | No server; decision DB needed | Analyze real decisions |
| `pareto-sweep` | No | Tune routing hyperparameters |
| `memory-mode` | No | Compare semantic, keyword, and off modes |
| `paraphrase` | No | Test paraphrase retrieval behavior |
| `bootstrap` | No server | Exercise router and decision logger |
| `v2` | Ollama for a full run | Validate prompts and build a compatibility matrix |
| `v2-review` | FastAPI | Check live routing spread |
| `refresh` | FastAPI | Refresh rankings |

`orch benchmark v2 --gate-only` validates prompt classification without
running model inference.

`orch benchmark report` reads a historical
`backend/orchestrator/routing/benchmark/results/strategy_results.json` file.
`simulate` prints results but does not create that file; generate it with the
benchmark harness module before using this report command.

## Offline routing analysis

These commands read local state and do not require the server:

| Command | Purpose |
| --- | --- |
| `orch replay run` | Replay logged decisions under another strategy |
| `orch analyze weekly` | Run the full analysis bundle |
| `orch analyze composer-counterfactual` | Analyze composer shadow choices |
| `orch analyze escalation-roi` | Analyze escalation return |
| `orch analyze a3-calibration` | Inspect predictor calibration |
| `orch analyze drift-history` | Inspect drift events |
| `orch analyze override-roi` | Analyze routing overrides |

`orch replay run` supports strategy, alpha, decay, pooling, estimator, filter,
limit, database, JSON, and notes options.

## State and observability

| Command | Purpose |
| --- | --- |
| `orch metrics live` | Human-readable health snapshot or watch loop |
| `orch metrics snapshot` | Machine-readable snapshot |
| `orch metrics usage` | What the cascade did for real work: local share, escalations, spend avoided |
| `orch metrics funnel` | How much delegable work reached Mahoraga (`--install-hint` for the hook config) |
| `orch metrics resource` | Is Mahoraga straining the machine — CPU/thermal, log-only (`MAHORAGA_RESOURCE_LOG=1` to enable) |
| `orch memory inspect` | Inspect episodic memory files |
| `orch memory clear` | Clear memory with confirmation |
| `orch memory backfill` | Rebuild memory from the decision log |
| `orch quarantine list` | List quarantined bucket/agent pairs |
| `orch quarantine add` / `clear` | Maintain quarantine state |
| `orch quarantine events` | Read quarantine events |
| `orch budget status` | Show budget-pacer state |
| `orch budget tune` | Show resolved budget configuration |
| `orch budget reset` | Delete budget-pacer state |

The default state directory is `~/.mahoraga-v2/`.

## Quality predictor

`orch quality` trains and inspects the optional logistic quality predictor:

```bash
orch quality train
orch quality eval
orch quality predict --task "..." --agent ollama:qwen3.5
orch quality inspect
orch quality retrain
```

Training and evaluation read `routing_decisions.db` by default.

## Brain

```bash
orch brain status
orch brain query "execution gate" --k 5
```

These commands inspect the repo-local `brain/` notes. They do not append
journals or decisions.

## Agent bench

`orch agentbench` measures which coding agent + local model can fix bugs in a
given repo, using that repo's own history. The repo must be a git repo with a
pytest suite; it is only read, and all state lives under
`~/.mahoraga-v2/agentbench/`.

```bash
orch agentbench mine [REPO]                 # commits -> gated tasks
    [--limit 25] [--max-lines 250] [--rev HEAD] [--commit SHA ...]
    [--python PATH] [--pytest-arg ARG ...]
orch agentbench preflight [REPO] --arm aider:qwen3.5:latest [--arm ...]
orch agentbench run [REPO] --arm aider:qwen3.5:latest [--arm ...]
    [--cond blind|feedback|both] [--repeats K] [--task SHA ...] [--timeout SECS]
    [--until HH:MM] [--wait-idle MIN] [--min-speed 0.5] [--no-guard] [--force]
orch agentbench report [REPO] [--json]
orch agentbench verdict REPO [REPO ...] --arm aider:qwen3.5:latest --tasks-per-month N
    (--api-spend USD | --plan claude-pro|claude-max-5x|... [--plan-price USD] [--usage-of-cap 1.0])
    [--cond feedback] [--deferrable 0.25] [--hourly 50] [--triage-min 3]
    [--watts 40] [--kwh 0.30] [--json]
```

- **mine** takes commits that changed both source and a test module. The task
  is the parent plus the commit's test changes. It is kept only if the tests
  pass at the commit and at least one fails at the base. The full suite at the
  base becomes the pass-to-pass baseline.
- **run** resets each task, lets the agent edit, restores the test files, and
  grades. An attempt is resolved when the commit's tests pass and no
  pass-to-pass test regressed. `feedback` also passes the task's test command
  to the agent. Resumable; `--repeats` above 1 records separate attempts per
  cell.
- **run** also writes a manifest per run (`runs/<run_id>.json`) recording
  model digests, Ollama and agent versions, the harness commit, hashes of the
  tasks and prompts, the machine and its power state. Every attempt records
  the `run_id` it came from and the grading tier that decided it.
- **report** prints resolve rates, multi-file resolve rates, regressions,
  failure modes (`no-edit`, `wrong-file`, `broke-other`, `partial+broke`,
  `partial`, `wrong-fix`), stability across repeats, and a per-task matrix.
  Rates are weighted by task, with 95% Wilson intervals over *tasks*:
  repeats of one task aren't independent, so only more tasks narrow an
  interval. A precision line says how many tasks and nights ±10% would take.
- **verdict** pools one arm's evidence across repos and answers *stay*,
  *split* or *switch* for your billing. On API billing it weighs cloud cost
  per task against electricity and your time to triage a failed local
  attempt. On a subscription the only gains are a lower tier or headroom
  under a cap you hit. The call is made at both ends of the interval, so it
  is marked not yet decided when the interval spans the threshold. Every
  unmeasured input is printed with it. Prices come from the dated,
  sourced `backend/orchestrator/audit/pricing.json`. A plan whose price
  wasn't read from the vendor's page is refused unless you pass `--plan-price`.

**The guard** (on by default for `run`) keeps results from measuring the
machine instead of the agent. A throttled local model is slow enough to time
out the agent's calls, and those failures look exactly like model failures.

- **preflight** checks the charger (AC, at least 60W), the agent binary,
  Ollama, that each model is pulled, and each model's current generation speed.
  `run` refuses to start if a check fails, unless `--force` is given.
- **Before each attempt**, the run waits while the machine is on battery, while
  it generates under `--min-speed` (default half) of its reference speed, or,
  with `--wait-idle`, while someone is using it.
- **After each attempt**, it is marked *degraded* if AC was lost or speed fell
  under the bar. Degraded attempts go to `attempts/degraded/`: they're kept
  for audit, excluded from every rate, counted in `report`, and the cell is
  retried up to twice.
- **Speed** comes from a fixed 128-token generation, timed by Ollama's eval
  counters. The reference is the best reading for that model digest taken
  while the machine had been idle 5+ minutes on AC. Until one exists, speed is
  recorded but not judged.

`run` holds off sleep with `caffeinate` for its lifetime; closing the lid
still sleeps the machine. `--until 07:30` starts no attempt after 07:30.

Arms are `<agent>:<model>`, e.g. `aider:qwen3.5:latest` or
`opencode:qwen3.5:latest`. Both agents get the same prompt and the same
generation budget (32k context, 8,192 tokens per call), so a difference
between two arms on the same model is the agent's.

- **aider** edits with search/replace blocks over a repo map. `feedback` uses
  its `--auto-test`.
- **opencode** is a tool loop (read, grep, edit, bash). Ollama's
  OpenAI-compatible endpoint ignores per-request options, so the model is
  served as a derived tag (`agentbench/<model>:<tag>-num_ctx…-num_predict…`)
  that shares the base weights. Only the ollama provider is enabled and web
  fetch is denied. `feedback` names the test command in the prompt, and the
  agent runs it itself.

**Containment** is enforced by the OS, not requested of the agent. Each agent
runs with its own HOME under the bench root, so it never sees your config or
provider credentials. On macOS it also runs under `sandbox-exec`, which lets it
and its children write only inside the task's checkout, that HOME, and the
per-user temp area, and open network connections only to localhost (Ollama).
Your repo is read-only to it. On other platforms only the HOME isolation
applies.

Models are local Ollama models only; Ollama `-cloud` models are refused, since
they would send code off the machine outside the audited egress client.

## Agent and rankings commands

`orch rankings` reads the live rankings endpoint. `orch agent add <model>`
checks server health, benchmarks the model unless `--skip-benchmark` is used,
and refreshes rankings. Both currently use the legacy port 8001 client.

## macOS service

```bash
orch service install
orch service start
orch service status
orch service stop
orch service uninstall
```

This group manages a launchd job, runs `orch serve` on port 8000, and writes
logs to `~/.mahoraga-v2/server.log`. It is macOS-only.

## Port behavior

The CLI currently has two hard-coded HTTP defaults:

| Port 8000 | Port 8001 |
| --- | --- |
| `orch serve` | mission, plan, run, and task groups |
| MCP bridge | `status`, `events`, `approve`, and `reject` |
| `orch bench run` | `orch rankings` |
| `orch benchmark v2-review` | `orch agent add` |
| macOS launchd service | `orch benchmark refresh` and `orch eval ab` |

For MCP, the UI, and live batches, run the normal `orch serve` on port 8000.
For a workflow that uses the legacy mission/run clients, start a separate
process on port 8001:

```bash
orch serve --port 8001
```

Do not point two service processes at the same live state for concurrent task
execution. Until the clients share a configurable base URL, choose the port
matching the command family you are using.
