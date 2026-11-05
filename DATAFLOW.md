# Where your data goes

Mahoraga works on your source code, so this page says, in plain language, what leaves
your machine, where it goes, and how to stop it. If something here stops being true, the
code is wrong, not this page.

The privacy policy on top of this page, written for visitors, is at
[kaitosoeno.com/mahoraga/privacy](https://kaitosoeno.com/mahoraga/privacy/). A change here
belongs there too (`site/content/privacy.ts`).

## At a glance

| Where | What | Why |
| --- | --- | --- |
| Nowhere (audit) | Nothing. Mining, runs, grading and the verdict all happen on your machine. | The audit exists to measure local agents on your code without sending it out. |
| Anthropic (the audit's cloud arm, off unless you pass `--allow-cloud`) | What Claude Code reads of the task's repository, the task prompt, and its tool output | A `claude:<model>` arm measures the cloud agent the local ones would replace, on the same tasks. |
| Ollama on `localhost:11434` | Task prompts and the repository context the agent reads | The local model answers the task. This stays on your machine unless you point `OLLAMA_BASE_URL` elsewhere. |
| Anthropic (router escalation, on by default) | The prompt of a task the local answer failed, through the `claude` CLI under your login or through `ANTHROPIC_API_KEY` | A failed local answer is re-run on a cloud model. `MAHORAGA_CASCADE=off` stops it. |
| Anthropic (planner and verifier) | Mission text and task output | Only when the `claude` backend is enabled, which it is not in the shipped configuration. |
| Ollama's registry | A request for a model name | `ollama pull` fetches the model. |
| Hugging Face | A request for the `all-MiniLM-L6-v2` model, no task data | The optional semantic memory extra downloads it once. |
| GitHub (EvalPlus release) | A request for the HumanEval+ file | Only when you run `experiments/build_humaneval_bank.py fetch` to rebuild the benchmark bank. |
| Telegram, Brave Search | The messages or queries you route through them | Only when you set `TELEGRAM_BOT_TOKEN` or `BRAVE_API_KEY`. Nothing in a request path calls the Brave tools today. |
| This project's servers | Nothing | There are none. |

## The audit (`mahoraga audit …`)

The audit measures local coding agents on your own repository. With local arms, which
are the default, it sends nothing anywhere. Only the cloud arm, which you turn on for a
run, sends anything out (below).

- **Your repository is only read.** Mining copies commits into a separate work area
  (`~/.mahoraga-v2/agentbench/`) and every attempt happens in its own copy, so your
  checkout is never written to.
- **Agents run in an operating-system sandbox.** On macOS it is `sandbox-exec`, and on
  Linux it is bubblewrap (`apt install bubblewrap`). The agent can write only to its own
  copy, its own home folder and a temporary folder. It can open network connections only
  to the local ports Mahoraga gives it, which are Ollama's for a local arm and the
  cloud arm's proxy for the cloud arm. A request anywhere else is refused by the
  operating system. On Linux the agent gets its own private network with no route out,
  its own `/tmp` and its own `/run`, so it can't reach other programs' sockets either.
- **Agents can't read your credentials.** The folders under your home that hold keys and
  logins (`.ssh`, `.config`, `.claude`, `.codex`, `.aws` and the like) are hidden from
  every agent.
- **Without a sandbox, only local arms run.** On a Linux machine without bubblewrap, local
  agents still get their own home folder, and Mahoraga starts them with their update
  checks and analytics turned off (for aider, `--no-check-update --no-analytics`). But
  nothing stops them opening a connection, and the run's preflight says so. The cloud arm
  refuses to start.
- **The model runs on your machine.** Ollama answers at `http://127.0.0.1:11434` unless
  you point `OLLAMA_BASE_URL` somewhere else.
- **Results stay local.** Tasks, attempts, run manifests and verdicts are files under
  `~/.mahoraga-v2/`, and nothing uploads them.

### The cloud arm (`claude:<model>`)

The study compares local agents with the cloud agent they would replace, on the same
tasks, so the audit can run Claude Code as an arm. This is the one part of the audit that
sends your code out.

- **It only runs when you ask, each time.** `mahoraga audit run --arm
  claude:claude-sonnet-5-5 --allow-cloud` starts it. Without `--allow-cloud` the run
  stops before anything is sent, and the queue refuses cloud arms altogether, so nothing
  unattended ever sends code.
- **What leaves.** Claude Code reads files in the task's copy of your repository and runs
  commands there, the same way it would on your own checkout. Everything it reads, the
  task prompt (the commit's message and the names of its test files), and the output of
  the commands it runs go to Anthropic's API. It can't read the original repository or
  the bench's other files, because the sandbox hides them.
- **It sees less than a local arm.** Your whole home folder is hidden from it, apart from
  its own copy of the task, its own empty home, and the programs it runs (the `claude`
  command and the Python that runs the task's tests). It gets only the environment
  variables it needs (`PATH`, locale, `TERM`, `TZ`, your user name, `SHELL`, `TMPDIR`), so a
  token exported in your shell doesn't reach it. On macOS it can connect to the proxy's
  port and no other local port.
- **Where it goes, and the record of it.** The agent's only way out is a proxy Mahoraga
  runs for the attempt (`agentbench/egress.py`). The proxy opens connections to
  `api.anthropic.com` on port 443 and refuses everything else. Every connection it is
  asked for, allowed or refused, is written at the end of the attempt's log with the
  number of bytes each way. The traffic is encrypted, so the log says where and how much,
  not what. Web fetch and web search are turned off, and Claude Code's telemetry, error
  reporting and update checks are switched off.
- **Whose account.** It runs on the login whose token you save with `claude setup-token`
  at `~/.config/mahoraga/claude-token`, or wherever `MAHORAGA_CLAUDE_TOKEN_FILE` points.
  Each attempt starts with an empty home folder, so none of your own Claude Code settings,
  instructions, skills, hooks or memory go with it. An `ANTHROPIC_API_KEY` in your
  environment is not passed on, so the run can't bill your API account by accident.
- **What it costs.** Each attempt records what Claude Code reports it would cost at API
  prices, whether your login is a subscription or not.
- Once the code reaches Anthropic, the terms in "When the cloud arm is Anthropic" below
  apply.

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
  its vendor. The planner and the output verifier also call Anthropic's API, but only when
  the `claude` backend is enabled, and `ENABLED_BACKENDS` ships as `["ollama"]`.
- **The Telegram channel is off unless configured.** It talks to Telegram only when
  `TELEGRAM_BOT_TOKEN` is set.

## Downloads

- **Models** come from Ollama's registry when you run `ollama pull`.
- **The benchmark bank** is built from the public EvalPlus release on GitHub when you run
  `experiments/build_humaneval_bank.py fetch`. The committed bank needs no download.
- **Semantic memory**, an optional extra (`requirements-semantic.txt`), downloads the
  `all-MiniLM-L6-v2` embedding model from Hugging Face on first use. It sends no task
  data, only the request for the model.

## Local files

The router's decisions, memory and funnel log live under `~/.mahoraga-v2/`. The funnel
hook records the path, extension and line counts of files Claude Code writes, never their
contents. All of it stays on disk until you delete it.

## The app

The app is served by the engine on your machine and talks only to that engine. Its fonts
are bundled, so opening it makes no request to Google or anyone else. Links to GitHub open
only when you click them.

## When the cloud arm is Anthropic

Escalation, `orch bench repro` and the `claude` worker send the task's prompt to Anthropic,
whether through the `claude` command-line tool on a Claude subscription (`claude-cli`) or
through an API key (`claude`). The audit's cloud arm sends what Claude Code reads of the
task's repository. In the router a prompt can contain your code, because the task is
whatever you asked it to do. Once it reaches Anthropic, how long it is kept and
whether it is used for training are set by Anthropic's terms and your account settings,
not by Mahoraga, and this project can't promise anything about them. Read
[Anthropic's privacy policy](https://www.anthropic.com/legal/privacy) before you send
anything you wouldn't send to Anthropic directly. The audit uses Anthropic only through
its cloud arm, and only when you pass `--allow-cloud`.

## What is never collected

Mahoraga has no telemetry, no analytics and no crash reporting, and it makes no request to
any server run by this project.
