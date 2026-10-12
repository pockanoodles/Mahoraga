# Where your data goes

Mahoraga works on your source code, so this page says, in plain language, what leaves
your machine, where it goes, and how to stop it. If something here stops being true, the
code is wrong, not this page.

## The audit (`mahoraga audit …`)

The audit measures local coding agents on your own repository, and by design it sends
nothing anywhere.

- **Your repository is only read.** Mining copies commits into a separate work area
  (`~/.mahoraga-v2/agentbench/`) and every attempt happens in its own copy, so your
  checkout is never written to.
- **Agents run sandboxed on macOS.** Each agent runs inside an operating-system sandbox
  (`sandbox-exec`) that can write only to its own copy and a temporary folder, and that
  can open network connections only to `localhost`, which is where Ollama serves the
  model. A request anywhere else is refused by the operating system.
- **On Linux there is no operating-system sandbox yet.** Agents get their own home
  directory and Mahoraga starts them with their update checks and analytics turned off
  (for aider, `--no-check-update --no-analytics`), but nothing stops an agent from opening
  a connection if it tries.
- **The model runs on your machine.** Ollama answers at `http://127.0.0.1:11434` unless
  you point `OLLAMA_HOST` somewhere else.
- **Results stay local.** Tasks, attempts, run manifests and verdicts are files under
  `~/.mahoraga-v2/`, and nothing uploads them.

## The router (`mahoraga serve`, the MCP bridge)

The router answers tasks with a local model first, and it is the one part that can send a
task to the cloud.

- **Escalation is on by default.** When the local judge rejects a local answer, or the
  answer doesn't run, the router re-runs that task on the escalation arm. The default arm
  is the `claude` command-line tool, so the task's prompt goes to Anthropic under your own
  Claude login. If the tool isn't installed, nothing is sent and the local answer is
  served.
- **It is capped.** At most 25 escalations a UTC day
  (`MAHORAGA_ESCALATE_MAX_PER_DAY`), so a bad judge day can't send everything.
- **It can be switched off.** `MAHORAGA_CASCADE=off` keeps every task on the machine.
  `MAHORAGA_ESCALATE_TO=claude` uses your `ANTHROPIC_API_KEY` instead of the CLI.
- **Other cloud arms are off.** The arms for Claude's API, Codex, Gemini and the other
  hosted agents are disabled in `agents.yaml`. Turning one on sends that arm's tasks to
  its vendor.
- **The Telegram channel is off unless configured.** It talks to Telegram only when
  `TELEGRAM_BOT_TOKEN` is set. The web-search and URL-reading tools in
  `backend/orchestrator/tools/` are not wired into any request path.

## Downloads

- **Models** come from Ollama's registry when you run `ollama pull`.
- **Semantic memory**, an optional extra (`requirements-semantic.txt`), downloads the
  `all-MiniLM-L6-v2` embedding model from Hugging Face on first use. It sends no task
  data, only the request for the model.

## The app

The app is served by the engine on your machine and talks only to that engine. Its fonts
are bundled, so opening it makes no request to Google or anyone else. Links to GitHub open
only when you click them.

## What is never collected

Mahoraga has no telemetry, no analytics and no crash reporting, and it makes no request to
any server run by this project.
