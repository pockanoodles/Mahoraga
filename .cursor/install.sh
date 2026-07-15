#!/usr/bin/env bash
# Idempotent dependency install for Cursor cloud agents / automations.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

.venv/bin/pip install -U pip -q
.venv/bin/pip install -r requirements.txt -q
.venv/bin/pip install -e . -q

# Make pytest / orch / python available without activating the venv.
mkdir -p "$HOME/.local/bin"
ln -sfn "$(pwd)/.venv/bin/python" "$HOME/.local/bin/python"
ln -sfn "$(pwd)/.venv/bin/python3" "$HOME/.local/bin/python3"
ln -sfn "$(pwd)/.venv/bin/pytest" "$HOME/.local/bin/pytest"
ln -sfn "$(pwd)/.venv/bin/orch" "$HOME/.local/bin/orch"

case ":${PATH}:" in
  *:"$HOME/.local/bin":*) ;;
  *) echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> "$HOME/.bashrc" ;;
esac
export PATH="$HOME/.local/bin:$PATH"

python --version
pytest --version
echo "Cloud install complete."
