# Mahoraga Web UI — Design Spec

**Date:** 2026-04-06
**Status:** Approved
**Scope:** Rich web chat interface — replaces the current minimal static UI

---

## Overview

A single-surface, dark VS Code-style web interface for Mahoraga. Two resizable columns: a chat column (primary) and a sidebar ops panel (secondary). No page routing, no top navbar. Designed to feel familiar to developers and presentable in workplace contexts. Matches Noctis design tokens — dark background, blue accent (`#007AFF`).

**Mental model:**
- **Chat column** — you give instructions. That's it.
- **Sidebar** — you watch the system work. Read-only ops panel: task graph, agent activity, logs, cost.
- **Settings drawer** — you configure the system (executor model, API keys, Telegram, future agent teams).

The web UI is the canonical interface — it works in-browser for all platforms, and the future Noctis macOS shell wraps it via WKWebView.

**Product direction:** Mahoraga is heading toward user-created agent teams — each agent has its own model (Claude Sonnet, Opus, Ollama/llama3, etc.). The orchestrator routes work to the right agent automatically. The vine chart is the primary visual surface for this: it shows the task graph *and* which agent/model handled each node. This is the Noctis LiveAgents vision, landing inside the web UI.

---

## 1. Layout

**Structure:** Two columns separated by a continuously draggable divider. No snap points except fully hidden. The chat column takes all remaining width.

**Sidebar states:**
- Hidden — divider handle visible on hover only
- Default — ~33% width
- Any width up to ~60%, user-draggable continuously

**Collapse control:** Chevron button in the sidebar header snaps it to hidden. Clicking it again restores to last width. Last width is saved to `localStorage`.

**No top navbar.** Single surface. Navbar deferred to a future task.

**Viewport:** Full height, no scrollbar on the outer layout. Each column handles its own internal scroll.

---

## 2. Design Tokens

| Token | Value |
|---|---|
| Background (chat) | `#1e1e2e` |
| Background (sidebar) | `#16161e` |
| Surface (assistant card) | `#2a2a3e` |
| Surface (input) | `#252535` |
| Accent (blue) | `#007AFF` |
| Accent dim | `#0055CC` |
| Text primary | `#e8e8f0` |
| Text muted | `#6b6b8a` |
| Success | `#3fb950` |
| Error | `#f85149` |
| Warning | `#d29922` |
| Font (UI) | `-apple-system, "Inter", system-ui, sans-serif` |
| Font (code/mono) | `"JetBrains Mono", "Fira Code", monospace` |
| Border radius (cards) | `12px` |
| Border radius (input) | `10px` |
| Divider color | `#2a2a40` |

---

## 3. Chat Column

Full height, background `#1e1e2e`. Flex column: messages area fills available space, input area pinned to bottom.

### Messages area

Scrollable. Messages stack top to bottom with `16px` gap.

**User messages:** Right-aligned. Dark pill (`#2a2a3e` background, blue left border). Max width 70%.

**Assistant messages:** Left-aligned. Card surface (`#2a2a3e`), no border, `12px` radius. Max width 85%. Supports markdown rendering — bold, italic, inline code. Code blocks get a distinct monospace inset (`#16161e` background) with a copy button top-right.

**Streaming:** Assistant card appears immediately with a pulsing cursor. Text streams in as SSE chunks arrive. No layout shift when streaming completes.

**System messages** (errors, escalations): Centered, muted text, no bubble. e.g. `↑ Escalated to Opus — task complexity exceeded Sonnet threshold`.

### Input area

Pinned to bottom. Padding `16px 20px`.

- **Textarea:** Single-line that grows to multiline on overflow. Max height `160px` then scrolls. Background `#252535`, blue border on focus. `Enter` sends, `Shift+Enter` for newline.
- **Send button:** Right of input. Blue, arrow icon. Disabled while streaming.
- **Settings gear:** Small icon left of the textarea. Opens the settings drawer. Does not interrupt chat state.

No model switcher in the input area. The orchestrator selects models automatically based on task routing. Executor configuration lives in settings.

---

## 4. Settings Drawer

Slides in from the right (or bottom sheet on narrow viewports). Does not replace the chat column — overlays it at 320px wide.

**Contents (v1):**
- Executor model — dropdown: `claude-sonnet-4-6` (default), `claude-opus-4-6`, *(Ollama models — future)*
- API key display (masked, with a "change" button)
- Telegram token (optional, masked)
- Brave API key (optional, masked)

**Future (agent teams):** This drawer will eventually have an Agents tab — create agents, assign models, define capabilities. For now, it's config only.

Drawer is dismissed by clicking outside or pressing `Esc`.

---

## 5. Sidebar — Ops Panel

Background `#16161e`. Three stacked sections from top to bottom. Each section has its own collapse chevron — independently collapsible within the sidebar.

The sidebar is **read-only**. It observes the system. Users do not interact with agents through the sidebar — they use chat for that.

---

### 5a. Vine Chart

Takes 55% of sidebar height by default. Vertically scrollable within its container when the task tree grows.

**This is the centrepiece.** It shows the mission's task graph as an animated SVG vine — organic, beautiful, and information-dense. As agent teams grow, this becomes the live map of your team working.

**Rendering:** Animated SVG. Tasks are circular nodes. Dependencies are smooth cubic bezier curves (not straight lines) connecting parent → child. The curves feel organic — slightly different curvature per branch so it doesn't look mechanical.

**Node anatomy:**

Each node is a circle with:
- Outer ring: status color (see states below)
- Inner fill: agent/model color (see agent colors)
- Node label below: short task name (truncated to 18 chars)
- Sub-label: agent name or model chip (e.g. `sonnet`, `llama3`)

**Node states:**

| State | Outer ring | Behavior |
|---|---|---|
| Pending | `#3a3a5a` (muted) | Static |
| Active | `#007AFF` | Pulsing glow, 1.5s loop |
| Complete | `#3fb950` at 60% | Static, path dims |
| Failed | `#f85149` | Static, path turns red |

**Agent/model colors (inner fill):** Each agent or model gets a consistent color derived from its name (hashed). This means the same agent always appears the same color across sessions — the vine chart becomes recognizable over time. Color palette draws from a curated set of muted, dark-background-friendly hues (not random).

**Path rendering:** The bezier path from root to each active node is rendered brighter than completed paths. Completed paths dim to ~40% opacity. The active path glows faintly blue.

**Hover tooltip:** Shows above the node, never clips the container edge.
```
[Task name]
Status: Running · 4.2s
Agent: researcher
Model: claude-sonnet-4-6
```

**Click (future):** Opens a detail panel showing the agent's full output for that task. Deferred — no click behavior in v1.

**Empty state:** When no mission is active — faint vine outline as decoration, centered text "No active mission". The outline is purely aesthetic, not interactive.

**Scaling:** A single task = one node (barely a vine). A 10-task mission = a small tree. A multi-agent team mission = a branching canopy. The SVG viewport scales to fit content, with a minimum height so single-node missions don't collapse.

---

### 5b. Recent Logs

Takes remaining height between vine chart and cost bar. Scrollable.

**Not a terminal dump.** Each entry is a structured row:

```
10:42 AM   [sonnet]   Summarized 3 search results into draft outline
```

Clicking a row expands it inline to show:
- Full user message
- Full assistant response (scrollable if long)
- Tool calls used (if any) — each as a collapsible chip: `web_search("mahoraga AI")` → result preview
- Cost for that exchange: `$0.004 (Haiku 800tok + Sonnet 2.1k tok)`

Model/agent chip color coding matches vine chart colors for consistency.

Entries grouped by session: **Today**, **Yesterday**, **Older**. Within a group, newest first.

**Empty state:** "No recent activity."

---

### 5c. Cost Bar

Pinned to the bottom of the sidebar. Always visible, does not scroll away.

```
Session: $0.003   [━━━━━━░░░░░░░░░]   Total: $1.24
```

Progress bar fills left to right. Color transitions:
- Blue → under $1 session spend
- Yellow → $1–5 session spend
- Red → over $5 session spend

Thresholds configurable via env var (`COST_WARN_USD`, `COST_ALERT_USD`).

Hover tooltip: breakdown by model or agent.
```
Haiku (planner/verifier):  $0.001
Sonnet (executor):         $0.002
```

---

## 6. Files

```
static/
├── index.html       ← restructured layout (replaces current 34-line file)
├── style.css        ← full design token system (replaces current file)
├── app.js           ← chat + SSE streaming + markdown rendering
├── sidebar.js       ← vine chart, logs, cost bar, polling
├── settings.js      ← settings drawer
└── resize.js        ← drag-to-resize divider logic
```

No build step. Vanilla HTML/CSS/JS. SVG vine rendered programmatically — no canvas, no D3, no external dependencies.

---

## 7. Backend Requirements

Most endpoints already exist. New work is minimal:

| Endpoint | Status | Notes |
|---|---|---|
| `POST /chat` | Exists | SSE streaming |
| `GET /cost/summary` | Exists | Via `CostLedger` |
| `GET /logs/recent` | **New** | Last N interactions: timestamp, user msg, assistant response, model, cost |
| `GET /missions/active` | **New** | Current mission's task graph: tasks, dependencies, statuses, assigned worker |

`/logs/recent` and `/missions/active` are both simple store reads — no new domain logic required.

---

## 8. What This Is Not

- No model switcher in the chat input (routing is automatic)
- No agent creation UI (deferred to settings drawer v2)
- No page routing (deferred)
- No top navbar (deferred)
- No authentication (deferred — self-hosted, single user for now)
- No file upload UI (deferred — backend tool exists, UI later)
- No mobile layout (desktop-first, Noctis wraps this on macOS)
- No D3 or external charting libraries (vanilla SVG only)
