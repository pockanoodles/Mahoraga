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
├── metrics {live, snapshot}
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

### Benchmark reproduction

`orch bench repro` reproduces the headline HumanEval+ cascade benchmark with
the published configuration pinned (local=granite4.1-8b, judge=qwen3.5:latest,
cloud=claude-cli). It preflights the environment first; `--preflight-only`
checks without inference, `--smoke` runs the first 5 tasks, `--local-only`
skips the always-cloud baseline. See the README's
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
