# Agent notes — Mahoraga

## Cursor Cloud specific instructions

Cloud agents and Slack automations use `.cursor/environment.json` (Dockerfile + install script). After boot:

- Prefer `.venv/bin/pytest` or `pytest` (symlinked onto `PATH` by install).
- Prefer `.venv/bin/python` / `.venv/bin/orch` the same way.
- Do **not** expect Ollama or the Mahoraga daemon on the cloud VM. Unit tests that mock adapters/workers are fine; live routing against local models will not work here.
- Skip full `./setup.sh` unless you need a real `~/.mahoraga` DB — the cloud install already does venv + `pip install -r requirements.txt` + editable package install.
