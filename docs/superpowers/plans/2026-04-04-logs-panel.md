# Logs Panel + Setup Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /logs` endpoint to Mahoraga, structured executor logging, a Noctis Logs panel in the overhaul worktree, and a one-command `setup.sh`.

**Architecture:** Mahoraga exposes a single aggregate `/logs?limit=5` endpoint returning recent runs with nested events. Noctis polls it every 2s via a new DataStore timer and renders runs as collapsible sections with colour-coded event rows. `setup.sh` wraps venv + deps + uvicorn into one idempotent command.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest/httpx (backend); SwiftUI, Combine, URLSession (Noctis overhaul worktree); bash (setup script).

---

## File Map

**Mahoraga** (`~/Projects/Mahoraga/`)
- Create: `setup.sh`
- Modify: `backend/orchestrator/service/app.py` — add logging config + `/logs` route + Pydantic models
- Modify: `backend/orchestrator/service/executor.py` — add structured logger calls
- Create: `tests/orchestrator_v2/test_logs.py` — tests for `/logs`

**Noctis overhaul** (`~/Projects/noctis/.worktrees/noctis-overhaul/`)
- Modify: `Noctis/Models.swift` — add `MahoragaEvent`, `MahoragaRun`, `MahoragaLogsResponse`; add `PanelID.logs`
- Modify: `Noctis/DataStore.swift` — add `mahoragaRuns`, `mahoragaOnline`, timer, `loadMahoragaLogs()`
- Create: `Noctis/Views/LogsPanel.swift`
- Modify: `Noctis/Views/DashboardView.swift` — wire `LogsPanel` into left column; update `fractions` switch

---

## Task 1: `setup.sh`

**Files:**
- Create: `setup.sh`

- [ ] **Step 1: Write `setup.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

# ── Python version check ─────────────────────────────────────────────────────
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
  echo "Error: python3 not found" >&2; exit 1
fi
version=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
major=$(echo "$version" | cut -d. -f1)
minor=$(echo "$version" | cut -d. -f2)
if [ "$major" -lt 3 ] || ([ "$major" -eq 3 ] && [ "$minor" -lt 12 ]); then
  echo "Error: Python 3.12+ required, found $version" >&2; exit 1
fi
echo "✓ Python $version"

# ── Virtualenv ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
  echo "Creating .venv..."
  "$PYTHON" -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
echo "✓ venv active"

# ── Dependencies ─────────────────────────────────────────────────────────────
echo "Installing dependencies..."
pip install -q -r requirements.txt
echo "✓ Dependencies installed"

# ── Environment checks (non-blocking) ────────────────────────────────────────
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "⚠  ANTHROPIC_API_KEY not set — Claude workers will be unavailable"
fi

OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
if ! curl -sf "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
  echo "⚠  Ollama not reachable at $OLLAMA_URL — OllamaWorker will be unavailable"
fi

# ── Start ────────────────────────────────────────────────────────────────────
echo ""
echo "Starting Mahoraga on http://127.0.0.1:8000"
exec uvicorn backend.orchestrator.service.app:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

- [ ] **Step 2: Make executable**

```bash
chmod +x setup.sh
```

- [ ] **Step 3: Smoke test**

Run: `bash -n setup.sh`
Expected: no output (syntax valid). Don't actually run it yet — Python deps may not be installed outside the venv.

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/Mahoraga
git add setup.sh
git commit -m "feat: add setup.sh for one-command startup"
```

---

## Task 2: Executor logging

**Files:**
- Modify: `backend/orchestrator/service/app.py`
- Modify: `backend/orchestrator/service/executor.py`

- [ ] **Step 1: Add `logging.basicConfig` to `app.py` lifespan**

In `app.py`, add `import logging` at the top (after `import os`), then add the following as the **first two lines** inside the `async with lifespan(app: FastAPI):` body (before `global _store`):

```python
import logging
```

In the lifespan function, add before `global _store, _registry, _verifier`:

```python
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
```

- [ ] **Step 2: Add logger + calls to `executor.py`**

Add at the top of `executor.py` (after the existing imports):

```python
import logging
logger = logging.getLogger(__name__)
```

Then add the following log calls at the indicated locations in `run_task()`:

**After the `ATTEMPT_ASSIGNED` event is appended** (just after the `await store.events.append(ev_types.make_event(... ATTEMPT_ASSIGNED ...))` block):
```python
        logger.info("task %s assigned to worker %s", task.id, worker_id)
```

**After the `ATTEMPT_STARTED` event is appended** (just after `await store.events.append(ev_types.make_event(... ATTEMPT_STARTED ...))`):
```python
        logger.info("attempt %s started", attempt.id)
```

**After `result = await verifier.verify(task, summary)` and the try/except block** (immediately after `result_feedback = result.feedback` line inside `if outcome.type == "attempt.completed":`):
```python
            logger.info(
                "verifier score=%d action=%s task=%s attempt=%s",
                result.score, result_action, task.id, attempt.id,
            )
```

**Inside `if result_action == "retry" ...` block, before `continue`:**
```python
                logger.warning(
                    "soft retry %d/%d for task %s: %s",
                    soft_retry_count[worker_id], MAX_SOFT_RETRIES,
                    task.id, result_feedback[:100],
                )
```

**After `await store.events.append(ev_types.make_event(... TASK_COMPLETED ...))` in the pass branch:**
```python
            logger.info("task %s completed", task.id)
```

**Inside `if escalating:` block, before `continue`:**
```python
            logger.warning("escalating task %s from %s to next worker", task.id, worker_id)
```

**In the final `task.failed/blocked` branch, before `return`** (after `await approvals.request_approval`):
```python
        logger.error("task %s blocked/failed: %s", task.id, blocking_reason[:100])
```

- [ ] **Step 3: Run existing executor tests to confirm nothing broke**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_executor.py -v
```
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add backend/orchestrator/service/app.py backend/orchestrator/service/executor.py
git commit -m "feat: add structured logging to executor and app lifespan"
```

---

## Task 3: `GET /logs` endpoint (TDD)

**Files:**
- Create: `tests/orchestrator_v2/test_logs.py`
- Modify: `backend/orchestrator/service/app.py`

- [ ] **Step 1: Write failing tests**

Create `tests/orchestrator_v2/test_logs.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from backend.orchestrator.store.base import Store
from backend.orchestrator.domain.models import Mission, Plan, Run, RunMode
from backend.orchestrator.domain import events as ev_types
from backend.orchestrator.workers.registry import WorkerRegistry
from backend.orchestrator.workers.base import WorkerAdapter, WorkerEvent, WorkerHealth
from backend.orchestrator.service.app import app, get_store, get_registry, get_verifier
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult
from unittest.mock import AsyncMock, MagicMock
from typing import AsyncIterator


def _make_pass_verifier() -> Verifier:
    v = MagicMock(spec=Verifier)
    v.verify = AsyncMock(return_value=VerificationResult(score=9, passed=True, feedback="", action="pass"))
    return v


class _StubWorker(WorkerAdapter):
    @property
    def id(self) -> str: return "extension"
    @property
    def capabilities(self) -> list[str]: return ["file_editing"]
    async def execute(self, attempt, task, feedback=None) -> AsyncIterator[WorkerEvent]:
        yield WorkerEvent("attempt.completed", {"summary": "done"})
    async def cancel(self, attempt_id: str) -> None: pass
    async def health(self) -> WorkerHealth:
        return WorkerHealth(worker_id="extension", healthy=True)


@pytest.fixture
async def store():
    s = await Store.connect(":memory:")
    yield s
    await s.close()


@pytest.fixture
def registry():
    reg = WorkerRegistry()
    reg.register(_StubWorker())
    return reg


@pytest.fixture
def client(store, registry):
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_verifier] = lambda: _make_pass_verifier()
    yield
    app.dependency_overrides.clear()


async def _seed_run(store: Store) -> tuple[Mission, Plan, Run]:
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    await store.missions.save_run(r)
    return m, p, r


@pytest.mark.asyncio
async def test_logs_empty(client, store):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/logs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


@pytest.mark.asyncio
async def test_logs_returns_run_with_events(client, store):
    _, _, run = await _seed_run(store)
    event = ev_types.make_event(run.id, ev_types.RUN_STARTED)
    await store.events.append(event)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["runs"]) == 1
    r = data["runs"][0]
    assert r["id"] == run.id
    assert r["status"] == "paused"  # Run.new() starts in paused state
    assert len(r["events"]) == 1
    assert r["events"][0]["type"] == "run.started"
    assert r["events"][0]["task_id"] is None


@pytest.mark.asyncio
async def test_logs_limit(client, store):
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    for _ in range(6):
        r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
        await store.missions.save_run(r)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/logs?limit=5")
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 5


@pytest.mark.asyncio
async def test_logs_max_limit_capped_at_20(client, store):
    m = Mission.new(title="M", goal="G")
    p = Plan.new(mission_id=m.id)
    await store.missions.save(m)
    await store.missions.save_plan(p)
    for _ in range(25):
        r = Run.new(mission_id=m.id, plan_id=p.id, mode=RunMode.direct)
        await store.missions.save_run(r)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/logs?limit=25")
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 20
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd ~/Projects/Mahoraga
python -m pytest tests/orchestrator_v2/test_logs.py -v
```
Expected: `FAILED` with `404 Not Found` on `/logs`.

- [ ] **Step 3: Add Pydantic models and `/logs` route to `app.py`**

Add after the existing `ApprovalRequest`/`CreateMissionRequest`/`CreatePlanRequest` models:

```python
class LogEventItem(BaseModel):
    id: str
    type: str
    task_id: str | None
    attempt_id: str | None
    ts: float


class LogRunItem(BaseModel):
    id: str
    mission_id: str
    status: str
    created_at: float
    events: list[LogEventItem]


class LogsResponse(BaseModel):
    runs: list[LogRunItem]
```

Add this route anywhere in the routes section (e.g., after the `GET /runs` route):

```python
@app.get("/logs", response_model=LogsResponse)
async def get_logs(store: StoreDep, limit: int = 5) -> LogsResponse:
    limit = min(limit, 20)
    all_runs = await store.missions.list_all_runs()  # already DESC by created_at
    runs = all_runs[:limit]
    run_items = []
    for run in runs:
        events = await store.events.list_by_run(run.id)
        run_items.append(LogRunItem(
            id=run.id,
            mission_id=run.mission_id,
            status=run.status.value,
            created_at=run.created_at,
            events=[
                LogEventItem(
                    id=e.id,
                    type=e.type,
                    task_id=e.task_id,
                    attempt_id=e.attempt_id,
                    ts=e.ts,
                )
                for e in events
            ],
        ))
    return LogsResponse(runs=run_items)
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
python -m pytest tests/orchestrator_v2/test_logs.py -v
```
Expected: all 4 green.

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
python -m pytest tests/orchestrator_v2/ -v --tb=short
```
Expected: all green (should be 314+ passing).

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/service/app.py tests/orchestrator_v2/test_logs.py
git commit -m "feat: add GET /logs aggregate endpoint with tests"
```

---

## Task 4: Noctis models + PanelID

**Files:**
- Modify: `Noctis/Models.swift` in `~/Projects/noctis/.worktrees/noctis-overhaul/`

- [ ] **Step 1: Add `MahoragaEvent`, `MahoragaRun`, `MahoragaLogsResponse` to `Models.swift`**

Append to the end of `Noctis/Models.swift` (after the last struct):

```swift
// MARK: - MahoragaLogs

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

struct MahoragaLogsResponse: Codable {
    let runs: [MahoragaRun]
}
```

- [ ] **Step 2: Add `.logs` to `PanelID`**

Find the existing `enum PanelID` (line 141 of `Models.swift`):
```swift
enum PanelID: String, CaseIterable, Codable {
    case liveAgents, cost, homunculus, ollama, briefing, tasks, activity
}
```

Replace with:
```swift
enum PanelID: String, CaseIterable, Codable {
    case liveAgents, cost, homunculus, ollama, briefing, tasks, activity, logs
}
```

- [ ] **Step 3: Build the Noctis target to confirm no compile errors**

```bash
cd ~/Projects/noctis/.worktrees/noctis-overhaul
xcodebuild -scheme Noctis -destination 'platform=macOS' build 2>&1 | tail -5
```
Expected: `** BUILD SUCCEEDED **`

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/noctis/.worktrees/noctis-overhaul
git add Noctis/Models.swift
git commit -m "feat: add MahoragaRun/Event models and PanelID.logs"
```

---

## Task 5: DataStore — `loadMahoragaLogs()`

**Files:**
- Modify: `Noctis/DataStore.swift` in `~/Projects/noctis/.worktrees/noctis-overhaul/`

- [ ] **Step 1: Add published state + timer property**

In `DataStore.swift`, in the `// MARK: Published state` block, add after the last `@Published var`:

```swift
@Published var mahoragaRuns: [MahoragaRun] = []
@Published var mahoragaOnline: Bool = false
```

In the `// MARK: Source paths` block (or just below it), add a constant:

```swift
private let mahoragaLogsURL = URL(string: "http://localhost:8000/logs?limit=5")!
```

In the `// MARK: Timers` block, add after the last `private var` timer:

```swift
private var mahoragaTimer: AnyCancellable?
```

- [ ] **Step 2: Wire the timer in `startTimers()`**

In `startTimers()`, add after the last existing timer assignment:

```swift
        mahoragaTimer = Timer.publish(every: 2, on: .main, in: .common).autoconnect()
            .sink { [weak self] _ in self?.loadMahoragaLogs() }
```

- [ ] **Step 3: Call `loadMahoragaLogs()` in `refreshAll()`**

In `refreshAll()`, add after the last existing `load` call:

```swift
        loadMahoragaLogs()
```

- [ ] **Step 4: Add `loadMahoragaLogs()` method**

Add a new `// MARK: - Mahoraga Logs` section after the existing `loadAgents()` section:

```swift
// MARK: - Mahoraga Logs

func loadMahoragaLogs() {
    URLSession.shared.dataTask(with: mahoragaLogsURL) { [weak self] data, response, error in
        guard let self else { return }
        guard error == nil,
              let http = response as? HTTPURLResponse, http.statusCode == 200,
              let data else {
            DispatchQueue.main.async { self.mahoragaOnline = false }
            return
        }
        guard let result = try? JSONDecoder().decode(MahoragaLogsResponse.self, from: data) else {
            DispatchQueue.main.async { self.mahoragaOnline = false }
            return
        }
        DispatchQueue.main.async {
            self.mahoragaRuns = result.runs
            self.mahoragaOnline = true
        }
    }.resume()
}
```

- [ ] **Step 5: Build**

```bash
cd ~/Projects/noctis/.worktrees/noctis-overhaul
xcodebuild -scheme Noctis -destination 'platform=macOS' build 2>&1 | tail -5
```
Expected: `** BUILD SUCCEEDED **`

- [ ] **Step 6: Commit**

```bash
git add Noctis/DataStore.swift
git commit -m "feat: add loadMahoragaLogs() with 2s polling to DataStore"
```

---

## Task 6: `LogsPanel.swift`

**Files:**
- Create: `Noctis/Views/LogsPanel.swift` in `~/Projects/noctis/.worktrees/noctis-overhaul/`

- [ ] **Step 1: Create `LogsPanel.swift`**

```swift
// Noctis/Views/LogsPanel.swift
import SwiftUI

struct LogsPanel: View {
    @ObservedObject var dataStore: DataStore
    let isExpanded: Bool
    let onToggle: () -> Void

    var body: some View {
        PanelChrome(
            title: "MAHORAGA LOGS",
            subtitle: subtitle,
            isExpandable: true,
            isExpanded: isExpanded,
            onToggle: onToggle,
            compactContent: { compactContent },
            detailContent: { detailContent }
        )
    }

    private var subtitle: String? {
        if !dataStore.mahoragaOnline && dataStore.mahoragaRuns.isEmpty { return "offline" }
        guard !dataStore.mahoragaRuns.isEmpty else { return nil }
        let total = dataStore.mahoragaRuns.reduce(0) { $0 + $1.events.count }
        let latest = dataStore.mahoragaRuns.first?.events.last?.type ?? ""
        return "\(total) events · \(latest)"
    }

    @ViewBuilder
    private var compactContent: some View {
        if !dataStore.mahoragaOnline && dataStore.mahoragaRuns.isEmpty {
            offlinePlaceholder
        } else if let latestEvent = dataStore.mahoragaRuns.first?.events.last {
            HStack(spacing: 8) {
                EventDot(type: latestEvent.type)
                Text(latestEvent.type)
                    .font(NoctisFont.mono(11))
                    .foregroundStyle(NoctisColor.muted)
                    .lineLimit(1)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
        } else {
            Text("No runs yet")
                .font(NoctisFont.label(12))
                .foregroundStyle(NoctisColor.dim)
                .padding(10)
        }
    }

    @ViewBuilder
    private var detailContent: some View {
        if !dataStore.mahoragaOnline && dataStore.mahoragaRuns.isEmpty {
            offlinePlaceholder
        } else {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(dataStore.mahoragaRuns.enumerated()), id: \.element.id) { idx, run in
                            RunSection(run: run, isFirst: idx == 0)
                        }
                        Color.clear.frame(height: 1).id("bottom")
                    }
                    .padding(.horizontal, 6)
                    .padding(.vertical, 4)
                }
                .onChange(of: dataStore.mahoragaRuns.first?.events.count) { _ in
                    withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
                }
            }
        }
    }

    @ViewBuilder
    private var offlinePlaceholder: some View {
        VStack {
            Spacer()
            Text("Mahoraga offline")
                .font(NoctisFont.label(13))
                .foregroundStyle(NoctisColor.dim)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - RunSection

private struct RunSection: View {
    let run: MahoragaRun
    let isFirst: Bool
    @State private var collapsed: Bool

    init(run: MahoragaRun, isFirst: Bool) {
        self.run = run
        self.isFirst = isFirst
        _collapsed = State(initialValue: !isFirst)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: {
                withAnimation(.easeInOut(duration: 0.2)) { collapsed.toggle() }
            }) {
                HStack(spacing: 8) {
                    Image(systemName: collapsed ? "chevron.right" : "chevron.down")
                        .font(.system(size: 9))
                        .foregroundStyle(NoctisColor.dim)
                    Circle()
                        .fill(runStatusColor(run.status))
                        .frame(width: 6, height: 6)
                    Text("RUN \(String(run.id.prefix(6)))")
                        .font(NoctisFont.mono(11))
                        .foregroundStyle(NoctisColor.text)
                    Text(run.status)
                        .font(NoctisFont.caption())
                        .foregroundStyle(runStatusColor(run.status))
                        .padding(.horizontal, 4)
                        .padding(.vertical, 1)
                        .background(runStatusColor(run.status).opacity(0.15))
                        .clipShape(RoundedRectangle(cornerRadius: 3))
                    Spacer()
                    Text(formattedTime(run.createdAt))
                        .font(NoctisFont.mono(10))
                        .foregroundStyle(NoctisColor.dim)
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 6)
            }
            .buttonStyle(.plain)

            if !collapsed {
                VStack(alignment: .leading, spacing: 1) {
                    ForEach(run.events) { event in
                        EventRow(event: event)
                    }
                }
                .padding(.leading, 16)
                .padding(.bottom, 4)
            }
        }
        .background(NoctisColor.surface.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func formattedTime(_ ts: Double) -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f.string(from: Date(timeIntervalSince1970: ts))
    }

    private func runStatusColor(_ status: String) -> Color {
        switch status {
        case "active":              return NoctisColor.accent
        case "completed":           return NoctisColor.green
        case "failed", "cancelled": return NoctisColor.red
        default:                    return NoctisColor.muted
        }
    }
}

// MARK: - EventRow

private struct EventRow: View {
    let event: MahoragaEvent

    var body: some View {
        HStack(spacing: 6) {
            EventDot(type: event.type)
            Text(event.type)
                .font(NoctisFont.mono(10))
                .foregroundStyle(NoctisColor.muted)
                .lineLimit(1)
            Spacer()
        }
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
    }
}

// MARK: - EventDot

private struct EventDot: View {
    let type: String
    var body: some View {
        Circle()
            .fill(eventColor(for: type))
            .frame(width: 5, height: 5)
    }
}

private func eventColor(for type: String) -> Color {
    if type.hasSuffix(".completed") || type.hasSuffix(".granted") { return NoctisColor.green }
    if type.hasSuffix(".failed") || type.hasSuffix(".escalated") || type.hasSuffix(".rejected") { return NoctisColor.red }
    if type.hasSuffix(".started") || type.hasSuffix(".assigned") || type.hasSuffix(".created") || type.hasSuffix(".ready") { return NoctisColor.accent }
    if type.hasSuffix(".blocked") || type.hasSuffix(".requested") { return NoctisColor.yellow }
    return NoctisColor.dim
}
```

- [ ] **Step 2: Build**

```bash
cd ~/Projects/noctis/.worktrees/noctis-overhaul
xcodebuild -scheme Noctis -destination 'platform=macOS' build 2>&1 | tail -5
```
Expected: `** BUILD SUCCEEDED **`

- [ ] **Step 3: Commit**

```bash
git add Noctis/Views/LogsPanel.swift
git commit -m "feat: add LogsPanel SwiftUI view"
```

---

## Task 7: Wire `LogsPanel` into `DashboardView`

**Files:**
- Modify: `Noctis/Views/DashboardView.swift` in `~/Projects/noctis/.worktrees/noctis-overhaul/`

- [ ] **Step 1: Replace the left column in `DashboardView`**

Find this block in `DashboardView.swift`:

```swift
                    // Left column: LiveAgents
                    LiveAgentsPanel(
                        dataStore: dataStore,
                        isExpanded: panelStateStore.expandedPanel == .liveAgents,
                        onToggle: { panelStateStore.toggleExpand(.liveAgents) }
                    )
                    .frame(width: widths.left)
```

Replace with:

```swift
                    // Left column: LiveAgents + Logs
                    VStack(spacing: 8) {
                        LiveAgentsPanel(
                            dataStore: dataStore,
                            isExpanded: panelStateStore.expandedPanel == .liveAgents,
                            onToggle: { panelStateStore.toggleExpand(.liveAgents) }
                        )
                        LogsPanel(
                            dataStore: dataStore,
                            isExpanded: panelStateStore.expandedPanel == .logs,
                            onToggle: { panelStateStore.toggleExpand(.logs) }
                        )
                    }
                    .frame(width: widths.left)
```

- [ ] **Step 2: Add `.logs` to the `fractions` switch**

Find:
```swift
        case .liveAgents:               return (0.65, 0.18, 0.17)
```

Replace with:
```swift
        case .liveAgents, .logs:        return (0.65, 0.18, 0.17)
```

- [ ] **Step 3: Build**

```bash
cd ~/Projects/noctis/.worktrees/noctis-overhaul
xcodebuild -scheme Noctis -destination 'platform=macOS' build 2>&1 | tail -5
```
Expected: `** BUILD SUCCEEDED **`

- [ ] **Step 4: Commit**

```bash
git add Noctis/Views/DashboardView.swift
git commit -m "feat: wire LogsPanel into DashboardView left column"
```

---

## Self-Review

**Spec coverage:**
- ✅ `setup.sh` — Task 1
- ✅ Structured executor logging — Task 2
- ✅ `GET /logs?limit=5` endpoint with nested events — Task 3
- ✅ Noctis `MahoragaRun`/`MahoragaEvent` models — Task 4
- ✅ `PanelID.logs` — Task 4
- ✅ DataStore 2s polling, `mahoragaOnline` flag, last-known state kept on offline — Task 5
- ✅ Runs as collapsible sections, latest expanded — Task 6
- ✅ Colour-coded event dots — Task 6
- ✅ Auto-scroll to bottom on new events — Task 6
- ✅ Offline placeholder — Task 6
- ✅ Compact subtitle (event count + latest type) — Task 6
- ✅ Wired into DashboardView left column — Task 7
- ✅ Column fractions updated — Task 7

**No placeholders found.**

**Type consistency:** `MahoragaRun`, `MahoragaEvent`, `MahoragaLogsResponse` defined in Task 4 and used consistently in Tasks 5 and 6. `PanelID.logs` added in Task 4, used in Tasks 6 and 7. `LogsResponse`, `LogRunItem`, `LogEventItem` defined and used entirely within Task 3.
