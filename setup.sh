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
echo "✓ Python $version"

# ── Virtualenv ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
  echo "Creating .venv..."
  "$PYTHON" -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
echo "✓ venv active"

# ── Dependencies ─────────────────────────────────────────────────────────────
echo "Installing dependencies..."
pip install -q -r requirements.txt
echo "✓ Dependencies installed"

# ── Environment checks (non-blocking) ────────────────────────────────────────
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "⚠  ANTHROPIC_API_KEY not set — Claude workers will be unavailable"
fi

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
if ! curl -sf "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
  echo "⚠  Ollama not reachable at $OLLAMA_URL — OllamaWorker will be unavailable"
fi

# ── Start ────────────────────────────────────────────────────────────────────
echo ""
echo "Starting Mahoraga on http://127.0.0.1:8000"
exec uvicorn backend.orchestrator.service.app:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
