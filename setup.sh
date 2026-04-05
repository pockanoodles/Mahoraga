#!/usr/bin/env bash
set -euo pipefail

# ── Python version check ─────────────────────────────────────────────────────
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
  echo "Error: python3 not found" >&2; exit 1
fi
version=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
major=$(echo "$version" | cut -d. -f1)
minor=$(echo "$version" | cut -d. -f2)
if [ "$major" -lt 3 ] || ([ "$major" -eq 3 ] && [ "$minor" -lt 12 ]); then
  echo "Error: Python 3.12+ required, found $version" >&2; exit 1
fi
echo "Python $version"

# ── Virtualenv ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
  echo "Creating .venv..."
  "$PYTHON" -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
echo "venv active"

# ── Environment file ─────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example .env
    echo "Created .env from .env.example — fill in your ANTHROPIC_API_KEY before running."
  else
    echo "Warning: no .env or .env.example found — create .env with your API keys." >&2
  fi
fi

# ── Validate API key ──────────────────────────────────────────────────────────
# Load .env if it exists so we can check the key
if [ -f ".env" ]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' .env | grep -v '^$' | xargs) 2>/dev/null || true
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo ""
  echo "WARNING: ANTHROPIC_API_KEY is not set."
  echo "  Edit .env and add: ANTHROPIC_API_KEY=sk-ant-..."
  echo ""
fi

# ── Dependencies ─────────────────────────────────────────────────────────────
echo "Installing dependencies..."
pip install -r requirements.txt -q
echo "Dependencies installed"

# ── Data directory ────────────────────────────────────────────────────────────
mkdir -p "$HOME/.mahoraga"
echo "Data directory: ~/.mahoraga"

# ── Database init ─────────────────────────────────────────────────────────────
echo "Initializing database..."
.venv/bin/python -c "
import asyncio
from pathlib import Path
Path.home().joinpath('.mahoraga').mkdir(exist_ok=True)
from backend.orchestrator.store.base import Store
async def init():
    store = await Store.connect()
    await store.close()
asyncio.run(init())
"
echo "Database ready"

# ── Ready ─────────────────────────────────────────────────────────────────────
echo ""
echo "Setup complete. To start Mahoraga:"
echo ""
echo "  .venv/bin/python -m uvicorn backend.orchestrator.service.app:app --host 0.0.0.0 --port 8000"
echo ""
echo "Then open http://localhost:8000"
echo ""
