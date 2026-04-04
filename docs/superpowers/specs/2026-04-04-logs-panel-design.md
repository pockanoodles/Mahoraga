# Logs Panel + Setup Script Design

**Date:** 2026-04-04
**Repos:** pockanoodles/Mahoraga, pockanoodles/Noctis (overhaul worktree)

---

## Overview

Three deliverables:

1. **`GET /logs` endpoint** — aggregate REST endpoint on Mahoraga's FastAPI backend returning recent runs with nested events
2. **Structured executor logging** — stdout logging at key executor lifecycle points
3. **Noctis Logs panel** — SwiftUI panel in the overhaul worktree polling the endpoint and rendering runs + events
4. **`setup.sh`** — single-command Mahoraga startup script

---

## 1. Mahoraga Backend

### `GET /logs` endpoint

Added to `backend/orchestrator/service/app.py`.

**Query params:**
- `limit: int = 5` (max 20) — number of most recent runs to return

**Response shape:**
```json
{
  "runs": [
    {
      "id": "abc123",
      "mission_id": "xyz",
      "status": "active",
      "created_at": 1712345678.0,
      "events": [
        {
          "id": "evt1",
          "type": "run.started",
          "task_id": null,
          "attempt_id": null,
          "payload": {},
          "ts": 1712345678.1
        }
      ]
    }
  ]
}
```

**Implementation:**
- Query `runs` ordered by `created_at DESC LIMIT {limit}`
- For each run, call `EventStore.list_for_run(run_id)` (already exists)
- Return as Pydantic response model `LogsResponse`

### Structured logging

In `app.py` lifespan startup:
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
```

Logger calls added in `executor.py` at:
- `task.assigned` → `INFO: Task {task_id} assigned to {worker_id}`
- `attempt.started` → `INFO: Attempt {attempt_id} started`
- Verifier result → `INFO: Verifier score={score} action={action}`
- Soft retry → `WARNING: Soft retry {n}/2 for task {task_id}: {feedback}`
- Escalation → `WARNING: Escalating task {task_id} to {new_worker}`
- `attempt.completed` → `INFO: Attempt {attempt_id} completed`
- `attempt.failed` / `task.failed` → `ERROR: ...`

Uvicorn captures stdout — no additional infra needed.

---

## 2. Noctis Logs Panel (overhaul worktree)

### New models (`Models.swift`)

```swift
struct MahoragaEvent: Codable, Identifiable {
    let id: String
    let type: String
    let taskId: String?
    let attemptId: String?
    let ts: Double

    enum CodingKeys: String, CodingKey {
        case id, type, ts
        case taskId = "task_id"
        case attemptId = "attempt_id"
    }
}

struct MahoragaRun: Codable, Identifiable {
    let id: String
    let missionId: String
    let status: String
    let createdAt: Double
    var events: [MahoragaEvent]

    enum CodingKeys: String, CodingKey {
        case id, status, events
        case missionId = "mission_id"
        case createdAt = "created_at"
    }
}
```

Payload is intentionally omitted from the Noctis model — the panel shows `event.type` + key fields extracted from type string, not raw JSON.

### DataStore additions (`DataStore.swift`)

```swift
@Published var mahoragaRuns: [MahoragaRun] = []
@Published var mahoragaOnline: Bool = false
```

New 2s timer calling `loadMahoragaLogs()`:
- `GET http://localhost:8000/logs?limit=5`
- On success: update `mahoragaRuns`, set `mahoragaOnline = true`
- On `URLError` / non-200: set `mahoragaOnline = false`, **keep existing `mahoragaRuns`** (last-known state stays visible, dimmed)

### `LogsPanel.swift`

```
┌─ MAHORAGA LOGS ──────────────────────────────┐
│  ● RUN abc123  active   Apr 4, 12:04          │
│    ● run.started                              │
│    ● task.created    "Refactor auth module"   │
│    ● task.assigned   → claude:sonnet          │
│    ◌ attempt.started                          │
│                                               │
│  ▸ RUN 9f3a21  completed  Apr 4, 11:58        │  (collapsed)
│  ▸ RUN 7b1c44  failed     Apr 4, 11:42        │  (collapsed)
└───────────────────────────────────────────────┘
```

**Behavior:**
- Latest run expanded by default; older runs collapsed
- Each run header: short ID (first 6 chars), status badge, formatted timestamp
- Each event row: colored dot + `event.type` (dots: green=completed/granted, red=failed/escalated, blue=started/assigned/created, amber=blocked/retry, gray=everything else)
- Auto-scrolls to bottom of latest run when new events arrive
- Offline state: panel header dims, body shows "Mahoraga offline — last seen {time}" with last-known runs still visible

**Compact view (pill):** Latest event type + total event count across all visible runs.

### `PanelID` addition

```swift
enum PanelID: String, CaseIterable, Codable {
    case liveAgents, ollama, briefing, homunculus, cost, logs  // +logs
    ...
}
```

---

## 3. Setup Script (`setup.sh`)

Location: repo root (`~/Projects/Mahoraga/setup.sh`)

```bash
#!/usr/bin/env bash
set -e

# 1. Python version check (requires 3.12+)
# 2. Create .venv if absent, activate
# 3. pip install -r requirements.txt
# 4. Warn if ANTHROPIC_API_KEY unset (non-blocking)
# 5. Warn if Ollama unreachable at OLLAMA_URL (non-blocking)
# 6. uvicorn backend.orchestrator.service.app:app --host 127.0.0.1 --port 8000 --reload
```

Idempotent — safe to run multiple times. No daemon management; Ctrl+C to stop.

---

## Implementation Order

1. `setup.sh` — unblocks live testing of everything else
2. Mahoraga: structured logging in `executor.py`
3. Mahoraga: `GET /logs` endpoint + `LogsResponse` Pydantic model
4. Noctis overhaul: models + DataStore
5. Noctis overhaul: `LogsPanel.swift` + wire into `PanelID` and `DashboardView`

---

## Out of Scope

- SSE / real-time streaming (polling is sufficient)
- Payload detail expansion (click-to-expand a single event) — future
- Run filtering by mission — future
- VS Code extension Logs panel — future
