# Getting started

Mahoraga has two ways in. The audit measures local coding agents on your own repository
and gives a stay, split or switch verdict, and it needs Ollama, an agent and an environment
that runs the repository's tests. The
router serves tasks through local models and needs the backend, its models and,
optionally, the web UI. Most readers want the audit first, and the router is the stage
that acts on its verdict.

## Run the audit on your repository

You need Python 3.12 or newer, Git, [Ollama](https://ollama.com/download), and a
repository with a pytest suite. The repository's tests run under its own
`.venv/bin/python`, so that environment needs pytest and the project's dependencies, or
pass `--python` to name another interpreter. Mahoraga's own interpreter has no pytest,
and without one of these, mining finds no task it can run. Install aider in an
environment of its own, at the version the published log measured, because its pins
would clash with Mahoraga's. On Linux, install bubblewrap too, since it is the sandbox
every agent runs in there, and the audit won't run without it.

```bash
uv tool install --python 3.12 aider-chat==0.86.2
uv tool install git+https://github.com/pockanoodles/Mahoraga   # until the first release
sudo apt install bubblewrap                                     # Linux only
ollama pull qwen3.5:latest
```

Then queue a night of runs:

```bash
mahoraga audit tonight ~/code/app --tasks-per-month 200 --plan claude-max-5x
```

`tonight` mines up to 25 tasks from commits that changed both code and a test, checks the
agent, Ollama, the model and the sandbox, queues the run for 21:00 to 08:00, and starts
the background worker (launchd on macOS, systemd on Linux). It asks once for how many tasks you give cloud agents a month and how you pay,
since the verdict is priced for your bill. The next morning, read the result:

```bash
mahoraga audit report ~/code/app
mahoraga audit verdict ~/code/app --arm aider:qwen3.5:latest@ctx=131072 --cond blind
```

On the published 16 GB laptop an attempt at a 131k context took 28 minutes at the median,
so its 47 tasks took four nights. Once tasks are mined, `mahoraga audit doctor ~/code/app`
checks the bench and estimates the nights left on your own machine. The audit's commands
and their main flags are in the [CLI reference](cli-reference.md#the-audit), and
[`DATAFLOW.md`](../DATAFLOW.md) says what each part sends where. The rest of this guide
sets up the router.

## Set up the router

This part sets up the local Ollama roster, the FastAPI service, the web UI, and optional
semantic memory.

### Prerequisites

- Python 3.12 or newer
- [Ollama](https://ollama.com/)
- Git
- A C++ build toolchain for `hnswlib`, the router's memory, which `requirements.txt`
  installs (`pip install -e .` on its own needs none)
- Node.js and npm if you want the React UI

Install the compiler toolchain if needed:

```bash
# Ubuntu/Debian
sudo apt-get install build-essential

# macOS
xcode-select --install
```

### Install

```bash
git clone https://github.com/pockanoodles/Mahoraga.git mahoraga
cd mahoraga

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

The editable install creates the `mahoraga` command and its older name, `orch`.

Copy the environment template if you want to customize local settings:

```bash
cp .env.example .env
```

The default local-only setup does not require an API key.

### Pull the configured models

The committed [`agents.yaml`](../agents.yaml) enables two Ollama models, and qwen3.5 is
also the judge:

```bash
ollama pull qwen3.5:latest
ollama pull granite4.1:8b
```

The other models in `agents.yaml` (qwen3:14b, granite4.2, ornith and others) are
disabled, and each one's comment there says why. Set a model's `enabled` field to `true`
before starting Mahoraga to add it to the pool.

Confirm that Ollama is available:

```bash
curl http://127.0.0.1:11434/api/tags
```

### Start the backend

```bash
orch serve
```

The default bind address is `127.0.0.1:8000`. In another terminal:

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/agents/status
curl http://127.0.0.1:8000/api/health/routing
```

`/api/health` reports registered and online agent counts. A registered agent
can still be offline when Ollama is stopped or its model has not been pulled.

For development reloads:

```bash
orch serve --reload
```

### Build or develop the web UI

FastAPI serves `frontend/dist/` at `/` when a production build exists:

```bash
cd frontend
npm ci
npm run build
cd ..
```

Start or restart `orch serve`, then open `http://127.0.0.1:8000`.

For frontend development, keep the backend on port 8000 and run Vite:

```bash
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` requests to the backend.

### Enable semantic episodic memory

Semantic mode is the runtime default, but its encoder and the `hnswlib` index
it searches are optional installs:

```bash
python -m pip install -r requirements-semantic.txt
```

Mahoraga then uses `all-MiniLM-L6-v2` embeddings and caches them in
`~/.mahoraga-v2/embedding_cache.sqlite`. If the package or model is
unavailable, routing falls back to handcrafted context retrieval.

To rebuild semantic memory from the decision log:

```bash
orch memory backfill --dry-run
orch memory backfill
```

### Run tests

```bash
pytest -m "not slow"
```

Cloud development environments do not have Ollama, so live local-model routing
is expected to be unavailable there. Unit tests mock adapters and workers.

### Next steps

- Configure the roster and runtime in [Configuration](configuration.md).
- Connect an editor or agent client with [MCP integration](mcp.md).
- Inspect command families in the [CLI reference](cli-reference.md).
- Measure routing with [Experiments and evaluation](experimentation.md).

### Troubleshooting

#### `orch` is not found

Activate the virtual environment and install the package:

```bash
source .venv/bin/activate
python -m pip install -e .
```

#### An Ollama agent is offline

Check the Ollama server and verify the exact configured tag:

```bash
curl http://127.0.0.1:11434/api/tags
ollama pull qwen3.5:latest
```

If you changed `agents.yaml`, restart `orch serve`.

#### A mission or run command cannot connect

Some legacy HTTP CLI clients still target port 8001, while `orch serve`
defaults to 8000. See [Port behavior](cli-reference.md#port-behavior) for the
affected commands and current workaround.

#### Semantic memory falls back to keyword retrieval

Verify that the optional encoder and its index can be imported:

```bash
python -c "import hnswlib; from sentence_transformers import SentenceTransformer; print('ok')"
```

The first encoder use may need to download `all-MiniLM-L6-v2`.
