# Mahoraga macOS App — Design Spec

**Date:** 2026-04-05
**Status:** Approved
**Repo:** github.com/pockanoodles/Mahoraga
**Scope:** v2 — SwiftUI macOS desktop client

---

## Overview

A native macOS app that makes Mahoraga a first-class desktop experience. Users double-click, paste their API key once, and chat — no terminal, no uvicorn command, no setup friction.

The app is a thin SwiftUI client bundled with the existing Python backend. It manages the backend process lifecycle, surfaces a dark terminal-style chat UI, and stores credentials securely in the macOS Keychain.

Windows users continue using the web UI at `localhost:8000` (upgraded separately as a future task).

---

## Architecture

```
Mahoraga.app/
├── Contents/
│   ├── MacOS/
│   │   └── Mahoraga          ← Swift binary
│   ├── Resources/
│   │   ├── backend/          ← Python backend source (copied from repo)
│   │   └── .venv/            ← Bundled virtualenv (built at package time)
│   └── Info.plist
```

### Runtime flow

```
App launch
    → ProcessManager starts uvicorn (bundled Python + backend)
    → HealthPoller hits localhost:8000/health every 500ms
    → Loading state shown until 200 OK received (~2-3s)
    → Chat UI becomes active
App quit
    → ProcessManager terminates uvicorn subprocess
```

The Swift binary talks to the backend exclusively over `localhost:8000` (HTTP for messages, SSE for streaming). The backend is identical to v1 — no changes to the Python layer.

---

## Components

### ProcessManager

Responsible for the full backend subprocess lifecycle.

- Locates the bundled Python interpreter at `Bundle.main.resourcePath/.venv/bin/python`
- Constructs the uvicorn launch command with correct `PYTHONPATH` pointing to bundled `backend/`
- Reads `ANTHROPIC_API_KEY` and optionally `TELEGRAM_BOT_TOKEN` from Keychain, injects as env vars into the subprocess environment
- Spawns the process using `Foundation.Process`
- Monitors for unexpected exit — restarts once automatically, shows error state on second failure
- On app termination (`applicationWillTerminate`), sends SIGTERM to subprocess and waits for clean exit

### HealthPoller

Lightweight polling loop that gates the UI.

- Polls `GET localhost:8000/health` every 500ms after process launch
- On first 200 OK: publishes `.ready` state, enables chat input
- Timeout after 15s: publishes `.failed` state, shows error with "Restart backend" button
- Stops polling once ready

### SetupSheet

First-launch only. Shown modally before the main window if no API key exists in Keychain.

Fields:
- Anthropic API Key (required) — stored in Keychain under `com.mahoraga.anthropic-api-key`
- Telegram Bot Token (optional) — stored in Keychain under `com.mahoraga.telegram-token`

Validation: key must start with `sk-ant-`. No network call — just format check before saving.

Dismissed permanently once saved. Re-accessible via Settings.

### ChatView (main UI)

**Layout:** Two-column. Left sidebar (240pt fixed) + right message panel.

**Sidebar:**
- App logo + name at top
- "New Chat" button
- Scrollable conversation list (title = first user message, truncated to 40 chars)
- Settings button at bottom

**Message panel:**
- Scrollable thread — user messages right-aligned, assistant messages left-aligned
- Assistant output renders in JetBrains Mono; code blocks syntax-highlighted
- Streaming: tokens append in real-time via SSE (`/chat/stream` endpoint)
- Cost footer per response (toggleable in Settings): `$0.003 · Haiku 1.2k tok`
- Input bar at bottom: multiline `TextEditor`, Cmd+Return to send

**Color palette:** matches Noctis — near-black background (`#0D0D0D`), muted borders, white primary text, terracotta accent (`#C96442`) for interactive elements.

**Typography:**
- UI chrome: Inter
- Assistant output: JetBrains Mono

### SettingsView

Accessible from sidebar footer button. Three sections:

1. **API Keys** — view/update Anthropic key and Telegram token (Keychain backed)
2. **Display** — toggle cost footer per message
3. **Backend** — show backend status dot, "Restart backend" button, log tail (last 50 lines of uvicorn stdout)

---

## Backend Packaging

At build time (or a `package.sh` script):

1. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
2. Copy `backend/` and `.venv/` into `Mahoraga.app/Contents/Resources/`
3. Strip `.pyc` cache and test files to keep bundle size down

The bundled `.venv` is self-contained — no system Python dependency for the user.

**Bundle size estimate:** ~80-120MB (Python stdlib + anthropic SDK + FastAPI deps). Acceptable for a desktop app.

---

## Backend Changes (minimal)

One addition to the existing Python backend:

- `GET /health` endpoint — returns `{"status": "ok"}`. Used by HealthPoller.
- No other backend changes. The v1 API surface is unchanged.

---

## Distribution

Unsigned `.app` bundled as a `.zip` or `.dmg`.

Users install the same way as Noctis: right-click → Open to bypass Gatekeeper on first launch. Document this clearly in the README.

No App Store, no code signing certificate required for v2. Ad-hoc signing (`codesign --sign -`) applied so macOS doesn't reject the binary outright.

---

## Out of Scope (v2)

- Windows desktop app (web UI at localhost:8000 remains the Windows path)
- Notarization / App Store distribution (v3)
- Auto-update mechanism (v3)
- Menu bar / tray icon mode
- Multiple backend instances

---

## Success Criteria

- User double-clicks `Mahoraga.app`, completes setup in under 60 seconds, and sends their first message
- No terminal window ever appears
- Backend crash is recovered automatically (one restart attempt)
- App quit cleanly terminates the Python process (no orphan uvicorn processes)
- Cost footer matches ledger data from the backend
