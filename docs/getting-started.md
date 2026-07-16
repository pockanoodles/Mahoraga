# Getting started

This guide sets up the local Ollama roster, FastAPI service, web UI, and
optional semantic memory.

## Prerequisites

- Python 3.12 or newer
- [Ollama](https://ollama.com/)
- Git
- A C++ build toolchain for `hnswlib`
- Node.js and npm if you want the React UI

Install the compiler toolchain if needed:

```bash
# Ubuntu/Debian
sudo apt-get install build-essential

# macOS
xcode-select --install
```

## Install

```bash
git clone https://github.com/pockanoodles/Mahoraga.git
cd Mahoraga

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

The editable install creates the `orch` command.

Copy the environment template if you want to customize local settings:

```bash
cp .env.example .env
```

The default local-only setup does not require an API key.

## Pull the configured models

The committed [`agents.yaml`](../agents.yaml) enables three Ollama models:

```bash
ollama pull qwen3.5:latest
ollama pull granite4.1:8b
ollama pull qwen3:14b
```

The 14B model is a diagnostic arm and requires roughly 9.3 GB on disk. On a
smaller machine, set its `enabled` field to `false` in `agents.yaml` before
starting Mahoraga.

Confirm that Ollama is available:

```bash
curl http://127.0.0.1:11434/api/tags
```

## Start the backend

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

## Build or develop the web UI

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

## Enable semantic episodic memory

Semantic mode is the runtime default, but its encoder is an optional install:

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

## Run tests

```bash
pytest -m "not slow"
```

Cloud development environments do not have Ollama, so live local-model routing
is expected to be unavailable there. Unit tests mock adapters and workers.

## Next steps

- Configure the roster and runtime in [Configuration](configuration.md).
- Connect an editor or agent client with [MCP integration](mcp.md).
- Inspect command families in the [CLI reference](cli-reference.md).
- Measure routing with [Experiments and evaluation](experimentation.md).

## Troubleshooting

### `orch` is not found

Activate the virtual environment and install the package:

```bash
source .venv/bin/activate
python -m pip install -e .
```

### An Ollama agent is offline

Check the Ollama server and verify the exact configured tag:

```bash
curl http://127.0.0.1:11434/api/tags
ollama pull qwen3.5:latest
```

If you changed `agents.yaml`, restart `orch serve`.

### A mission or run command cannot connect

Some legacy HTTP CLI clients still target port 8001, while `orch serve`
defaults to 8000. See [Port behavior](cli-reference.md#port-behavior) for the
affected commands and current workaround.

### Semantic memory falls back to keyword retrieval

Verify that the optional encoder can be imported:

```bash
python -c "from sentence_transformers import SentenceTransformer; print('ok')"
```

The first encoder use may need to download `all-MiniLM-L6-v2`.
