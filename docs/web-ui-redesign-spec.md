# Mahoraga Web UI — Full Redesign Specification

**Purpose:** This document is a complete implementation spec for rebuilding the Mahoraga web UI. It is written to be handed directly to Claude Code. Read the entire thing before starting work. Where this doc says "figure it out," that means investigate the codebase, make a decision, and proceed — don't ask.

**Branch:** `personal`
**Repo:** `~/Projects/Mahoraga`
**Backend:** FastAPI at `localhost:8000`

---

## 0. Philosophy

Mahoraga's web UI is not a chatbot. It is an **observatory for a learning routing engine** that also happens to have a chat interface.

The system routes tasks to AI agents using a contextual multi-armed bandit (LinUCB). The bandit extracts a 9-dimensional context vector from each task, scores candidate agents, picks the best one, observes the outcome, and updates its model. The web UI exists so a human can **watch this process happen in real time** — see which agent was chosen, why, whether it was exploration or exploitation, what the reward was, and how the bandit's beliefs are shifting over time.

The chat panel is still functional — users type tasks, output streams back. But the chat is one component of the dashboard, not the center of the universe. The routing observatory is the main attraction. The layout is two resizable columns with a draggable divider. Either column can be expanded to dominate the screen or collapsed to a sliver. The left column (the observatory/dashboard) is the default-wider column. The right column (the chat) is secondary.

**Design language:** Keep the existing dark theme and color palette. The current look is fine — dark navy/slate background, teal/cyan accents, clean sans-serif type. Don't redesign the aesthetic. Improve the **information architecture** and **interactivity**.

---

## 1. Phase 1 — Fix the Response Assembler (Pre-Requisite)

**This phase is a debugging task, not a UI task.** The response assembler bug is the reason task output doesn't appear in the chat panel. Tasks route correctly (the sidebar shows agent assignment, workflow steps), but the chat panel stays empty — no streamed output, no final response.

### Known Symptoms
- User sends a task via the web UI.
- The sidebar shows the workflow: planner labels steps, agents get assigned.
- The chat panel shows the user's message bubble but **no assistant response** — empty space where the output should be.
- The "RECENT" section at the bottom of the sidebar shows task descriptions and `$0.0000` cost, confirming the task was created but output wasn't captured or displayed.
- In the Apr 13 screenshot: "create a file called test.py that prints hello world" → agent assigned (`aider:default`), workflow shows "Message from web-user", but no response rendered.
- In the Apr 6 screenshot: "Research rock types" → planner creates three workflow steps ("Research rock types", "Draft paragraph", "Review and finalize"), but chat panel is blank.

### Where to Investigate
The response assembly pipeline is:

```
FastAPI endpoint receives task
  → Planner decomposes into steps
  → BanditRouter picks agent per step
  → WorkerRegistry dispatches to adapter
  → Adapter.execute() streams output
  → Response assembler collects chunks
  → SSE/NDJSON stream pushes to frontend
  → Frontend JS parses and renders bubbles
```

The break could be at any point from adapter execution onward. Likely candidates:

1. **Adapter stubs**: Some worker adapters may not fully implement `execute()` or may silently return empty results. Check each adapter in `backend/orchestrator/workers/` — verify they actually call their respective backend (Ollama API, subprocess for CLI agents, Anthropic SDK for Claude tiers).

2. **Stream assembly**: The backend streams NDJSON chunks. The chunk types are:
   - `{"type":"meta", ...}` — route info, plan
   - `{"type":"delta", "text":"..."}` — live LLM tokens
   - `{"type":"brief", "text":"..."}` — status updates
   - `{"type":"done", ...}` — completion
   - `{"type":"error", ...}` — errors

   The frontend may be receiving `meta` and `brief` chunks (which populate the sidebar) but not `delta` chunks (which populate chat). Verify by adding `console.log` to the NDJSON parser in the frontend JS and checking what chunk types actually arrive.

3. **Response assembler bug (explicitly noted in pending work)**: The documented bug is "task descriptions showing instead of output." This suggests the assembler is surfacing the task's description string rather than the agent's generated output. Look for where the response text is constructed after execution — there's likely a field mismatch (reading `task.description` instead of `result.output` or similar).

### What "Fixed" Looks Like
- User sends "write a hello world in Python" via web UI.
- Chat panel shows user bubble, then an assistant bubble with the actual agent output streaming in.
- Sidebar shows which agent handled it, status progression.
- The response is the agent's generated content, not the task description.

### Instructions for Claude Code
**Debug this first before touching anything else.** The entire UI redesign depends on output actually appearing. Approach:
1. Start the Mahoraga backend (`python -m backend.orchestrator.service.app` or however it launches — check existing scripts).
2. Open `localhost:8000` in browser with DevTools console open.
3. Send a simple task: "explain quicksort in one paragraph."
4. Watch the Network tab for the SSE/fetch stream. Log every NDJSON chunk that arrives.
5. Trace backward from whatever's broken: no chunks at all → backend issue. Chunks arrive but wrong type → assembler. Chunks arrive correctly but don't render → frontend parser.
6. Fix and verify with 3 different task types: a simple question (general bucket), a code task (code bucket), and a planning task (plan bucket).

**Do not proceed to Phase 2 until at least one task type produces visible streamed output in the chat panel.**

---

## 2. Phase 2 — Layout Restructure

### 2.1 Tech Stack Migration

**Migrate from vanilla HTML/CSS/JS to React + Vite + Tailwind.**

Rationale: The new UI has 10+ independently updating components fed by real-time SSE streams (UCB score bars, routing cards, regret chart, agent status indicators, chat bubbles, session metrics, routing timeline). Managing this in vanilla JS means manual DOM manipulation, no component isolation, and painful state synchronization. React's component model and hooks (`useState`, `useEffect`, `useRef`) map directly to these requirements.

Setup:
- **Vite** for build/dev server (fast HMR, zero-config React support).
- **Tailwind CSS** for utility styling. Keep the existing color palette as Tailwind config custom colors.
- **Recharts** for the charts (regret curve, agent distribution, throughput over time). It's React-native, declarative, and handles real-time updates well.
- **No other UI libraries.** No shadcn, no Material UI, no component library. Custom components only. This is a portfolio piece — it should look like you built it, not assembled it from a kit.

The React app will be served by FastAPI as static files from a `frontend/dist/` directory after build, same as the current setup but with a build step. During development, Vite's dev server proxies API calls to FastAPI.

```
frontend/
├── index.html
├── vite.config.ts
├── tailwind.config.ts
├── package.json
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── hooks/
│   │   ├── useSSE.ts              # SSE stream subscription
│   │   ├── useMetrics.ts          # polls /api/metrics
│   │   └── useResizablePanel.ts   # drag-to-resize logic
│   ├── components/
│   │   ├── Layout.tsx             # two-column resizable shell
│   │   ├── DragDivider.tsx        # the resize handle
│   │   ├── observatory/
│   │   │   ├── Observatory.tsx        # left panel container
│   │   │   ├── SessionBar.tsx         # top-level session stats
│   │   │   ├── AgentScorePanel.tsx    # UCB scores per agent
│   │   │   ├── RoutingTimeline.tsx    # chronological decision feed
│   │   │   ├── RoutingCard.tsx        # individual routing decision
│   │   │   ├── RegretChart.tsx        # cumulative regret curve
│   │   │   ├── AgentDistribution.tsx  # pie/bar of agent usage
│   │   │   ├── BucketBreakdown.tsx    # per-bucket stats
│   │   │   ├── RoutingHealth.tsx      # health status + alerts
│   │   │   └── StrategySelector.tsx   # live strategy switch control
│   │   ├── chat/
│   │   │   ├── ChatPanel.tsx          # right panel container
│   │   │   ├── MessageBubble.tsx      # user/assistant message
│   │   │   ├── ChatInput.tsx          # input bar + send button
│   │   │   └── TaskMeta.tsx           # inline routing info per message
│   │   └── shared/
│   │       ├── StatusDot.tsx          # green/yellow/red indicator
│   │       └── Tooltip.tsx            # hover info
│   ├── lib/
│   │   ├── api.ts                 # fetch wrappers for all endpoints
│   │   └── types.ts               # TypeScript interfaces for API responses
│   └── styles/
│       └── globals.css            # Tailwind directives + custom CSS vars
```

### 2.2 Two-Column Resizable Layout

The UI is a single full-viewport page split into two columns by a vertical draggable divider.

```
┌──────────────────────────┬───┬─────────────────────┐
│                          │ ║ │                     │
│    OBSERVATORY           │ ║ │    CHAT             │
│    (routing dashboard)   │ ║ │    (task I/O)       │
│                          │ ║ │                     │
│                          │ ║ │                     │
│                          │ ║ │                     │
└──────────────────────────┴───┴─────────────────────┘
                           ↕
                     drag divider
```

**Behavior:**
- Default split: 60% observatory / 40% chat.
- Dragging the divider resizes both columns in real time. No snapping, no jumps, smooth `mousemove` tracking.
- Minimum width for either column: 280px. Below that, it collapses fully (0px) and the other column takes 100%.
- Double-clicking the divider resets to the 60/40 default.
- The divider itself is a 6px-wide vertical strip. On hover, it shows a subtle highlight (e.g., the teal accent color) and the cursor changes to `col-resize`.
- Persist the split ratio to `localStorage` so it survives page reloads.

**Implementation — `useResizablePanel` hook:**
- Track divider position as a percentage of viewport width.
- On `mousedown` on the divider, attach `mousemove` and `mouseup` listeners to `document`.
- On `mousemove`, compute `(clientX / window.innerWidth) * 100`, clamp to [min, max], set as the split point.
- On `mouseup`, detach listeners.
- Use `requestAnimationFrame` to debounce if needed, but likely unnecessary — CSS `width` transitions are fast.
- The left column gets `width: ${splitPercent}%`, the right gets `width: ${100 - splitPercent}%`, both with `overflow-y: auto`.

**This replaces the current broken resize behavior.** The existing divider doesn't move smoothly — likely because it's implemented with `resize` CSS or a janky JS approach. Replace entirely.

### 2.3 Color Palette

Preserve the existing dark theme. Extract these from the current CSS and define as Tailwind custom colors and CSS variables for Recharts:

```
--bg-primary:      #0d1117    (main background — very dark navy)
--bg-secondary:    #161b22    (card/panel backgrounds)
--bg-elevated:     #1c2128    (hover states, active cards)
--border:          #30363d    (subtle borders between sections)
--text-primary:    #e6edf3    (main text — off-white)
--text-secondary:  #8b949e    (muted labels, timestamps)
--accent-teal:     #00bcd4    (primary accent — the teal from "● Ollama")
--accent-green:    #3fb950    (success, PASS, healthy)
--accent-yellow:   #d29922    (warning, degraded, exploration)
--accent-red:      #f85149    (error, critical, ESCALATE)
--accent-blue:     #58a6ff    (info, links, exploration flag)
--accent-purple:   #bc8cff    (episodic memory, special)
```

Match these to the current screenshots. Adjust if the actual values differ — the screenshots are the source of truth for the current palette. The point is: **don't change the vibe, just systematize it.**

---

## 3. Phase 3 — The Observatory (Left Column)

This is the main event. The observatory is a vertically scrollable column containing stacked sections. Each section is a self-contained component that subscribes to its own data source.

### 3.1 Session Bar (top of observatory, always visible)

A compact horizontal stats bar pinned to the top of the left column. Replaces the old `Session: $0.000 / Total: $0.000` footer.

```
┌─────────────────────────────────────────────────────────┐
│  12 tasks · 47.2s · 1,834 tok · 21.3 t/s · 91.7% ✓    │
│  ▸ explore: 25%  · cost: $0.00  · health: ● ok         │
└─────────────────────────────────────────────────────────┘
```

**Data source:** `GET /api/metrics` → `session` object, polled every 5 seconds. Also updated immediately on every SSE task-completion event.

Fields:
- `task_count` — total tasks this session
- `wall_time_s` — cumulative wall time
- `tokens` — total tokens generated
- `avg_throughput_tps` — session average t/s
- `success_rate` — percentage, with a green/yellow/red color
- `exploration_rate` — what percentage of decisions were exploration vs exploitation
- `cost_usd` — total API spend
- `routing_health.status` — "ok" / "degraded" / "critical" as a colored dot

This bar is dense, single-line or two-line max. It's a glanceable status strip, not a detailed view.

### 3.2 Agent Score Panel

A ranked list of all registered agents with their current UCB scores. This is the heartbeat of the bandit — it shows what the system currently believes about each agent's quality.

```
┌─────────────────────────────────────────────────────────┐
│  AGENT SCORES (LinUCB)                    [↻ strategy ▾]│
│                                                         │
│  claude:sonnet  ████████████████████░░░░  0.87  ● warm  │
│  codex-cli      ████████████████░░░░░░░░  0.74  ● ready │
│  aider          ███████████████░░░░░░░░░  0.71  ● ready │
│  ollama:general ██████████████░░░░░░░░░░  0.65  ● warm  │
│  claude:haiku   █████████████░░░░░░░░░░░  0.62  ● warm  │
│  gemini-cli     ████████████░░░░░░░░░░░░  0.58  ● ready │
│  opencode       ██████████░░░░░░░░░░░░░░  0.48  ● ready │
│  goose          █████████░░░░░░░░░░░░░░░  0.43  ● ready │
│  claude:opus    ████████░░░░░░░░░░░░░░░░  0.39  ● idle  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Data source:** `GET /api/routing/stats` — needs to return per-agent UCB scores, or `GET /api/metrics` → `agents` object. Check what's currently available; if UCB scores aren't exposed, they need to be added to the stats endpoint.

Each row:
- Agent ID (e.g., `claude:sonnet`, `ollama:general`)
- Horizontal bar showing UCB score [0, 1] — filled portion is the score, color coded (green for high, yellow for mid, red for low)
- Numeric score label
- Status dot: `warm` (model loaded/process running), `ready` (available but not loaded), `idle` (not recently used), `error` (health check failed)

**The `[↻ strategy ▾]` dropdown** in the top-right lets you switch routing strategy live: LinUCB / UCB1 / Thompson / Static. Calls `POST /api/routing/strategy`. The panel title updates to reflect the active strategy.

**Animation:** When scores update (after a task completes), the bars should animate smoothly to their new width. Use CSS transitions, not JS animation.

### 3.3 Routing Timeline (the core visual)

A reverse-chronological feed of routing decisions. Every task that flows through the system gets a card in this timeline. This is the component that makes the bandit's decisions legible.

```
┌─────────────────────────────────────────────────────────┐
│  ROUTING DECISIONS                                      │
│                                                         │
│  ┌─ 14:23:07 ─────────────────────────────────────────┐ │
│  │ "create test.py that prints hello world"            │ │
│  │                                                     │ │
│  │ bucket: code  →  agent: codex-cli  [EXPLOIT]        │ │
│  │                                                     │ │
│  │ candidates:                                         │ │
│  │   codex-cli   0.82 ████████░░  ← selected          │ │
│  │   aider       0.74 ███████░░░                       │ │
│  │   ollama      0.61 ██████░░░░                       │ │
│  │                                                     │ │
│  │ verdict: PASS · quality: 0.88 · 3.2s · reward: 0.81│ │
│  └─────────────────────────────────────────────────────┘ │
│                                                         │
│  ┌─ 14:22:41 ─────────────────────────────────────────┐ │
│  │ "explain the water cycle"                           │ │
│  │                                                     │ │
│  │ bucket: general  →  agent: ollama  [EXPLORE]        │ │
│  │                                                     │ │
│  │ candidates:                                         │ │
│  │   claude:haiku 0.71 ███████░░░                      │ │
│  │   ollama      0.65 ██████░░░░  ← selected (explore)│ │
│  │   gemini-cli  0.52 █████░░░░░                       │ │
│  │                                                     │ │
│  │ verdict: PASS · quality: 0.72 · 5.1s · reward: 0.68│ │
│  └─────────────────────────────────────────────────────┘ │
│                                                         │
│  ┌─ 14:21:15 ─────────────────────────────────────────┐ │
│  │ "refactor auth module to use JWT"                   │ │
│  │                                                     │ │
│  │ bucket: code  →  agent: aider  [EXPLOIT]            │ │
│  │ ⚠ RETRY → codex-cli  [ESCALATE]                    │ │
│  │                                                     │ │
│  │ verdict: PASS (after escalation) · quality: 0.79    │ │
│  │ 12.4s · reward: 0.52 (penalty: escalation)         │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

**Data source:** `GET /api/routing/decisions?last=50` for initial load. Then SSE stream for real-time updates — every new routing decision pushes a card to the top of the timeline.

Each `RoutingCard` contains:
- **Timestamp** — when the decision was made.
- **Task excerpt** — first ~80 chars of the prompt. Truncated with ellipsis.
- **Bucket** — which capability bucket the classifier assigned (code, plan, general, etc.).
- **Selected agent** — which agent the bandit picked.
- **Exploration flag** — `[EXPLOIT]` if the selected agent had the highest UCB score, `[EXPLORE]` if the bandit chose a suboptimal agent to gather information. Color-coded: exploit is green/neutral, explore is yellow/blue.
- **Candidate scores** — the top 3 agents that were considered, with their UCB scores and mini score bars. The selected agent is highlighted. If it was an exploration pick, the highest-scoring agent is shown greyed out (the bandit chose not to pick it).
- **Verification verdict** — PASS / RETRY / ESCALATE. If RETRY or ESCALATE happened, show the chain (first agent → retry → escalation to second agent).
- **Outcome metrics** — quality score, wall time, reward score. These populate *after* the task completes — the card first appears with a "running..." state, then fills in once the verifier scores the output.

**Card states:**
1. **Pending** — task just routed, execution in progress. Card shows the routing decision but outcome section shows a spinner/pulse.
2. **Complete** — execution finished, verifier scored. All fields populated.
3. **Failed** — adapter error, timeout, or verifier ESCALATE chain exhausted. Red accent border.

**Interaction:** Clicking a routing card in the timeline scrolls the chat panel (right column) to the corresponding message exchange. This links the two columns — you see the routing decision on the left, the actual conversation output on the right.

### 3.4 Charts Section (collapsible)

A collapsible section below the routing timeline containing two charts. Default collapsed — click to expand. These are secondary to the timeline but available for deeper analysis.

**Chart A — Cumulative Regret Curve**

A line chart showing cumulative regret over time (task count on x-axis, cumulative regret on y-axis). If oracle scores aren't available (real usage, not benchmark), show cumulative (1 - reward) as a proxy.

This chart is the single most important number for proving the system works. Sublinear growth = the bandit is learning. Linear or superlinear = it's not.

**Chart B — Agent Distribution Over Time**

A stacked area chart or stacked bar chart. X-axis is time (or task number). Y-axis is percentage. Each band is an agent. Shows how the bandit's preferences shift — early on, lots of exploration across many agents. Over time, it converges on the best agents for each bucket.

**Data source:** `GET /api/metrics/history?last=200` — returns per-task metrics with timestamps, agent assignments, and reward scores. The frontend computes cumulative regret and agent distribution from this data.

**Use Recharts.** `<LineChart>` for regret, `<AreaChart>` with `stackOffset="expand"` for distribution. Minimal styling — dark background, teal/green lines, no gridlines, subtle axis labels.

### 3.5 Agent Detail Panel (on hover/click in Agent Score Panel)

When you click an agent row in the Agent Score Panel (3.2), a detail panel expands inline or as a slide-over showing:
- Total tasks handled
- Success rate
- Average reward
- Average wall time
- Average throughput (t/s, for Ollama agents)
- Which buckets it's been assigned to and its performance per bucket
- Warm/cold status (for Ollama: is the model loaded?)
- Last 5 routing decisions involving this agent

**Data source:** `GET /api/metrics` → `agents` object for aggregates. `GET /api/routing/decisions?agent=<name>&last=5` for recent history (this query param may need to be added to the endpoint).

### 3.6 Routing Health Alerts

If `routing_health.status` is `"degraded"` or `"critical"`, a banner appears at the top of the observatory (below the session bar) with the alert messages:

```
┌─────────────────────────────────────────────────────────┐
│ ⚠ DEGRADED — Success rate 62% (threshold: 70%) over    │
│   last 20 tasks. Bandit exploring at 43% after 312      │
│   decisions — not converging.                            │
└─────────────────────────────────────────────────────────┘
```

Yellow border for degraded, red for critical. Dismissible but re-appears if the condition persists on next poll.

### 3.7 Strategy Controls

Beyond the dropdown in the Agent Score Panel header, add a **routing mode** toggle somewhere in the observatory (session bar or its own section):

- `local_first` — prefer Ollama, escalate only when needed
- `balanced` — let the bandit decide freely
- `quality_first` — prefer Claude tiers, cost be damned

Calls `POST /api/routing/mode`. Shows the current active mode clearly.

---

## 4. Phase 4 — The Chat Panel (Right Column)

### 4.1 Chat Basics

The chat panel is a standard conversation interface. It already mostly exists — the redesign is about integrating it with the observatory and fixing the output rendering.

Components:
- **Message list** — scrollable, newest at bottom. User messages right-aligned, assistant messages left-aligned.
- **Chat input bar** — pinned to bottom of the right column. Text input + send button. Support for `Shift+Enter` for newlines, `Enter` to send.
- **Cancel button** — appears during streaming, aborts the in-flight request (use `AbortController`).
- **Streaming** — assistant messages appear character-by-character as NDJSON `delta` chunks arrive.

### 4.2 Task Metadata Inline

Each assistant message in the chat gets a small, collapsed metadata bar above it:

```
┌─────────────────────────────────────────────────────────┐
│ ▸ codex-cli · code · 3.2s · reward: 0.81 · EXPLOIT     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Here's the Python file:                                 │
│                                                         │
│ ```python                                               │
│ print("hello world")                                    │
│ ```                                                     │
│                                                         │
│ I've created test.py with a simple hello world...       │
└─────────────────────────────────────────────────────────┘
```

The `▸` is a disclosure triangle. Click to expand and see the full routing decision (same data as the RoutingCard in the observatory). This connects the chat experience to the routing data without cluttering the conversation view.

When a message is being composed (streaming), the metadata bar shows:
```
│ ◌ ollama:general · general · routing...                 │
```

Then fills in once complete:
```
│ ▸ ollama:general · general · 5.1s · reward: 0.68 · EXPLORE │
```

### 4.3 Chat History

Show a collapsible "RECENT" section at the bottom of the chat panel (or accessible via a button) that lists recent task sessions. Clicking one loads that conversation. This exists in the current UI but in the sidebar — move it to the chat panel where it belongs.

---

## 5. SSE / Real-Time Data Architecture

### 5.1 The SSE Stream

The backend already has an SSE/NDJSON streaming endpoint for task execution (`/api/chat/stream` or equivalent — check the actual endpoint). The frontend needs to subscribe to this for chat streaming AND extract routing metadata from it.

**Current chunk types** (from the architecture):
```
{"type":"meta", ...}          — route info, plan, initial trace
{"type":"delta", "text":"..."}  — live LLM tokens
{"type":"brief", "text":"..."}  — one-liner status updates
{"type":"done", ...}          — completion, timings, scores
{"type":"error", ...}         — error detail
```

The `meta` chunk should contain routing decision data (which agent, bucket, UCB scores, exploration flag). If it doesn't currently include all of this, add it to the backend response.

The `done` chunk should contain the verification verdict, quality score, reward, and wall time. If it doesn't, add it.

### 5.2 The `useSSE` Hook

A custom React hook that:
1. Opens an `EventSource` or `fetch` stream to the task endpoint when a task is submitted.
2. Parses each NDJSON line.
3. Dispatches to appropriate state updaters:
   - `meta` → update the routing card in the observatory (new card, pending state).
   - `delta` → append text to the current chat message.
   - `brief` → update a status indicator (optional, could be a subtle text below the streaming message).
   - `done` → finalize the chat message, update the routing card to complete state, trigger a metrics refresh.
   - `error` → show error state in both chat and routing card.

### 5.3 Polling for Metrics

Separate from the SSE stream, poll `GET /api/metrics` every 5 seconds for session aggregates and agent scores. Also trigger an immediate poll after every task completion (`done` event).

Don't use SSE for metrics — polling is simpler and metrics don't need sub-second updates.

### 5.4 Routing Decisions Feed

For the initial load of the routing timeline, fetch `GET /api/routing/decisions?last=50`. After that, new decisions arrive via the SSE stream's `meta` chunks. If multiple browser tabs or MCP sessions are sending tasks simultaneously, the polling fallback ensures the timeline stays in sync.

---

## 6. Backend API Surface

Here's what the frontend needs. Check which endpoints already exist, which need modification, and which need creation.

### Existing (verify and use)
| Endpoint | Purpose |
|---|---|
| `POST /api/task` | Submit a task (returns SSE stream) |
| `GET /api/health` | Server health |
| `GET /api/agents/status` | Agent availability + health |
| `GET /api/routing/stats` | Routing performance stats |
| `GET /api/routing/decisions` | Recent routing decisions |
| `POST /api/routing/strategy` | Switch bandit strategy |
| `POST /api/routing/mode` | Switch routing mode |

### Needed (build if missing)
| Endpoint | Purpose | Response Shape |
|---|---|---|
| `GET /api/metrics` | Session aggregates + routing health | See MAHORAGA_METRICS_AND_RESEARCH.md §1.3 |
| `GET /api/metrics/history?last=N` | Per-task metric history for charts | Array of task metric objects |
| `GET /api/routing/decisions?last=N&agent=X` | Filtered decisions | Array of decision objects with UCB scores, candidates, explore/exploit flag |

The **routing decisions** endpoint is the most critical. Each decision object needs to include:
```json
{
  "id": "...",
  "timestamp": "...",
  "task_excerpt": "first 80 chars of prompt",
  "capability_bucket": "code",
  "selected_agent": "codex-cli",
  "exploration": false,
  "candidates": [
    {"agent": "codex-cli", "ucb_score": 0.82, "selected": true},
    {"agent": "aider", "ucb_score": 0.74, "selected": false},
    {"agent": "ollama:general", "ucb_score": 0.61, "selected": false}
  ],
  "verdict": "PASS",
  "quality_score": 0.88,
  "reward_score": 0.81,
  "wall_time_ms": 3200,
  "cost_usd": 0.003
}
```

If the existing `/api/routing/decisions` doesn't return this shape, extend it. The bandit already computes UCB scores for all candidates during routing — it just needs to log and expose them.

---

## 7. Implementation Order

This is the order to build things. Each step produces a testable increment.

```
Phase 1: Debug response assembler
  ↓ (output appears in current vanilla UI)
Phase 2: Scaffold React + Vite + Tailwind
  ↓ (serves blank page from FastAPI)
Phase 2: Build Layout.tsx + DragDivider.tsx
  ↓ (two resizable columns, empty)
Phase 2: Port chat panel (ChatPanel, MessageBubble, ChatInput)
  ↓ (chat works in new React UI, equivalent to current)
Phase 3: Build SessionBar
  ↓ (top stats strip, polls /api/metrics)
Phase 3: Build AgentScorePanel
  ↓ (live UCB score bars per agent)
Phase 3: Build RoutingTimeline + RoutingCard
  ↓ (routing decisions appear as cards, linked to SSE)
Phase 3: Build charts (RegretChart, AgentDistribution)
  ↓ (collapsible section with Recharts)
Phase 4: Add TaskMeta to chat messages
  ↓ (routing info inline in chat)
Phase 3: Build RoutingHealth alerts
  ↓ (degraded/critical banners)
Phase 3: Build StrategySelector + mode toggle
  ↓ (live controls for strategy and routing mode)
Polish: Animations, transitions, edge cases
  ↓
Done
```

---

## 8. What NOT to Build

- **No authentication.** This runs on localhost.
- **No mobile responsive.** This is a desktop dev tool / demo surface.
- **No dark/light theme toggle.** Dark only.
- **No Telegram integration.** Dead.
- **No benchmark replay mode.** Benchmark data is for internal analysis only.
- **No settings page.** All config is env vars or API calls.
- **No file upload.** Tasks are text prompts.

---

## 9. Definition of Done

The web UI is "done" when:

1. **Chat works end-to-end.** Type a task → output streams back → assistant bubble renders with the agent's actual response.
2. **Routing is visible.** Every routing decision produces a card in the observatory timeline with agent selection, UCB scores, explore/exploit flag, and post-execution reward.
3. **Agent scores are live.** The Agent Score Panel shows current UCB scores that update after each task.
4. **Session metrics are live.** The session bar shows task count, throughput, success rate, cost, and routing health.
5. **The layout resizes smoothly.** Dragging the divider is buttery. No jank, no jumps, no layout thrashing.
6. **Charts render.** Regret curve and agent distribution chart populate from metrics history.
7. **Strategy and mode are switchable.** The controls work and the UI reflects the change immediately.

---

## 10. Reference: Existing Codebase Pointers

- **Backend entry point:** `backend/orchestrator/service/app.py` (FastAPI)
- **Routing engine:** `backend/orchestrator/routing/bandit_router.py`
- **Worker adapters:** `backend/orchestrator/workers/` (one file per agent)
- **MCP server:** `backend/mcp/server.py`
- **Current frontend:** likely `frontend/` or `static/` directory (check repo structure)
- **Bandit state:** `~/.mahoraga/bandit_state.json`
- **Routing decisions DB:** `~/.mahoraga/routing_decisions.db` (SQLite)
- **Metrics research doc:** `MAHORAGA_METRICS_AND_RESEARCH.md` — has the full schema for the `task_metrics` table and `/api/metrics` response shape
- **Test suite:** 577 tests via pytest — run before and after changes to verify nothing breaks

---

## 11. Notes for Claude Code

- **You and Nicole will debug the response assembler together (Phase 1).** Don't try to solve it from the spec alone — the investigation requires running the server, sending tasks, and tracing the stream.
- **The existing frontend files can be deleted once the React app is serving.** Don't try to incrementally migrate — scaffold fresh, port the chat logic, and replace.
- **The color palette in the screenshots is the source of truth.** The CSS variable values in section 2.3 are approximations — eyedrop the actual values from the running UI if they differ.
- **Check what API endpoints actually exist before building new ones.** The context doc says 9 MCP tools map to REST endpoints. Some of the metrics endpoints from MAHORAGA_METRICS_AND_RESEARCH.md may or may not be implemented yet.
- **The bandit already computes candidate UCB scores during routing.** The data exists in memory at decision time. It may just not be logged or exposed via API yet. Find where the routing decision is made in `bandit_router.py`, identify the UCB scores for all candidates, and make sure they get included in the decision log and the API response.
- **Don't break the MCP server or CLI.** The web UI is one surface. Tasks also come in via MCP (Claude Code) and CLI. All three should produce routing decisions that show up in the observatory. The routing timeline should show MCP-originated tasks alongside web UI tasks.
- **Run the test suite (`pytest`) after any backend changes.** 577 tests. They should all still pass.
