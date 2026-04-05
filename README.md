# Mahoraga

**The Adapting AI That Actually Does Things.**

A self-hosted AI assistant that learns how you work. Talk to it from your browser or Telegram. It routes tasks intelligently through Claude — Haiku plans, Sonnet executes, Opus escalates — tracks every dollar spent, and adapts to you over time.

Open source. Bring your own API key. All data stays on your machine.

## Why Mahoraga?

- **Adapts to you** — learns your preferences, communication style, and corrections. Gets better every conversation.
- **Cost efficient** — Haiku handles routing and simple responses (~$0.001/msg). Sonnet only activates when needed. Most conversations cost under $0.01.
- **Transparent** — see exactly what every interaction costs. No hidden token usage.
- **Self-hosted** — your API key, your machine, your data. Nothing leaves your control.
- **Multi-channel** — web UI out of the box, Telegram with one env var.

## Quick Start

```bash
git clone https://github.com/pockanoodles/Mahoraga.git
cd Mahoraga
cp .env.example .env     # Add your ANTHROPIC_API_KEY
./setup.sh               # Install deps, init database
.venv/bin/python -m uvicorn backend.orchestrator.service.app:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** and start chatting.

### Telegram (optional)

1. Message [@BotFather](https://t.me/botfather) on Telegram, create a bot, get the token
2. Add `TELEGRAM_BOT_TOKEN=your-token-here` to `.env`
3. Restart Mahoraga — your bot is live

## How It Works

```
You (browser/Telegram)
    → Planner (Haiku) — classifies intent, decomposes tasks
    → Executor (Sonnet) — executes tasks, uses tools
    → Verifier (Haiku) — scores output, retries if needed
    → Adaptive Model — learns from the interaction
    → Response
```

**Simple messages** ("hey", "what's 2+2") → Haiku responds directly. ~$0.001.

**Complex tasks** ("research X, compare with Y, write a report") → Planner creates a task graph, Sonnet executes each step. ~$0.05.

**Failed tasks** → Retry with feedback, then escalate to Opus. You only pay for Opus when it's actually needed.

## Tools

| Tool | What it does | Required |
|---|---|---|
| Web search | Search + summarize results | `BRAVE_API_KEY` in .env |
| URL reader | Fetch and extract content from links | Built-in |
| Document reader | Read text from files you share | Built-in |
| Code execution | Run Python for calculations | Python 3.12+ |

## Cost Transparency

Every response shows what it cost:

```
$0.003 (Haiku: 1.2k tok | Sonnet: 3.4k tok)
```

Ask "how much have I spent this week?" and Mahoraga answers from its local ledger.

**Typical daily cost:** $0.05–0.20 for casual use.

## Configuration

All config via `.env`:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Optional
TELEGRAM_BOT_TOKEN=         # Enable Telegram channel
BRAVE_API_KEY=              # Enable web search tool
```

## Requirements

- Python 3.12+
- Anthropic API key ([get one here](https://console.anthropic.com/))
- Docker (optional, for sandboxed code execution)

## License

MIT
