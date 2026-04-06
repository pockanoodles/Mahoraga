# Mahoraga Web UI — Design Spec

**Date:** 2026-04-06
**Status:** Approved
**Scope:** Rich web chat interface — replaces the current minimal static UI

---

## Overview

A single-surface, dark VS Code-style web interface for Mahoraga. Two resizable columns: a chat column (primary) and a sidebar (secondary). No page routing, no top navbar. Designed to feel familiar to developers and presentable in workplace contexts. Matches Noctis design tokens — dark background, blue accent (`#007AFF`).

The web UI is the canonical interface — it works in browser, and the future Noctis macOS shell wraps it via WKWebView.

---

## 1. Layout

**Structure:** Two columns separated by a continuously draggable divider. No snap points except fully hidden. The chat column takes all remaining width.

**Sidebar states:**
- Hidden — divider handle visible on hover only
- Default — ~33% width
- Expanded — up to 50% width, user-draggable
- Any width in between is valid

**Collapse control:** Chevron button in the sidebar header snaps it to hidden. Clicking it again restores to last width.

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

**System messages** (errors, model escalations): Centered, muted text, no bubble. e.g. `↑ Escalated to Opus`.

### Input area

Pinned to bottom. Padding `16px 20px`.

- **Textarea:** Single-line that grows to multiline on overflow. Max height `160px` then scrolls. Background `#252535`, blue border on focus. `Enter` sends, `Shift+Enter` for newline.
- **Send button:** Right of input. Blue, arrow icon. Disabled while streaming.

### Model switcher bar

Sits below the textarea. Compact strip of chips: `haiku` · `sonnet` · `opus` · *(ollama models — future)*. Active model highlighted with blue background and white text. Inactive chips are muted text, hover shows border. Clicking switches model immediately. No confirmation.

---

## 4. Sidebar

Background `#16161e`. Three stacked sections from top to bottom. Each section has a header with title and a collapse chevron for that section individually (independent of the whole sidebar).

### 4a. Vine Chart

Takes 55% of sidebar height by default. Vertically scrollable within its container when the task tree grows.

**Rendering:** Animated SVG. Tasks are circular nodes. Dependencies are smooth cubic bezier curves connecting parent → child nodes.

**Node states:**

| State | Color |
|---|---|
| Pending | `#3a3a5a` (muted) |
| Active | `#007AFF` with pulsing glow |
| Complete | `#3fb950` dimmed to 60% |
| Failed | `#f85149` |

**Active node:** Glowing blue dot that pulses (CSS `box-shadow` animation, 1.5s loop). The path from root to active node is highlighted brighter than completed paths.

**Hover:** Tooltip on node hover — task name, status badge, elapsed time. Tooltip appears above the node, doesn't clip the container edge.

**Empty state:** When no mission is active, shows a minimal placeholder — faint vine outline, text "No active mission".

**Future extension:** Node click will show the agent/model that ran it and its output. Planned for when subagent teams are integrated.

### 4b. Recent Logs

Takes remaining height between vine chart and cost bar. Scrollable.

**Not a terminal dump.** Each entry is a structured row:

```
[timestamp]  [model chip]  One-line summary of what happened
```

Clicking a row expands it inline to show: full user message, full assistant response, tool calls used (if any), cost for that exchange.

Model chip uses color coding: Haiku = muted blue, Sonnet = blue, Opus = bright blue.

Entries are grouped by session (today, yesterday, older). Within a group, newest first.

**Empty state:** "No recent activity."

### 4c. Cost Bar

Pinned to the bottom of the sidebar. Always visible, does not scroll away.

Layout: `Session: $0.003` on the left · thin progress bar center · `Total: $1.24` on the right.

Progress bar fills left to right. Color: blue at low spend, transitions to yellow at $1/session, red at $5/session. Thresholds configurable via env var later.

Hover on the bar shows a tooltip: breakdown by model (`Haiku: $0.001 · Sonnet: $0.002`).

---

## 5. Files

```
static/
├── index.html          ← restructured layout (replaces current 34-line file)
├── style.css           ← full design token system (replaces current file)
├── app.js              ← chat + SSE streaming logic (refactored)
├── sidebar.js          ← vine chart, logs, cost bar
└── resize.js           ← drag-to-resize divider logic
```

No build step. Vanilla HTML/CSS/JS. SVG vine rendered programmatically via JS — no canvas, no D3, no dependencies.

---

## 6. Backend Requirements

The web UI consumes existing endpoints. No new backend work required for the initial build. Cost bar and logs read from:
- `GET /chat` — SSE streaming (already exists)
- `GET /cost/summary` — session + total cost (already exists via `CostLedger`)
- `GET /logs/recent` — structured log feed (needs a new endpoint — simple query on the store)

The `/logs/recent` endpoint is the only new backend work. It returns the last N interactions with: timestamp, user message, assistant response, model used, cost.

---

## 7. What This Is Not

- No page routing
- No top navbar (deferred)
- No authentication (deferred — self-hosted, single user)
- No file upload UI (deferred — backend tool exists, UI later)
- No mobile layout (desktop-first, Noctis wraps this on macOS)
