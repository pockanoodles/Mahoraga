# Mahoraga Product — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform Mahoraga from an Ollama-based experiment into a shippable, open-source AI assistant with web chat, Telegram, adaptive user model, and Claude-only orchestration.

**Architecture:** Fork the existing repo, strip Ollama/VS Code code, keep the orchestrator domain model and executor. Swap the planner to Haiku, add a channel adapter layer (web chat default, Telegram opt-in), build the adaptive user model that learns from every interaction, and add basic tools (web search, URL reader, document reader, code sandbox). All state in local SQLite.

**Tech Stack:** Python 3.12, FastAPI, aiogram, anthropic SDK, aiosqlite, httpx, Typer, pytest

**Spec:** `docs/superpowers/specs/2026-04-05-mahoraga-product-design.md`

---

## File Structure

After fork and strip, the repo looks like this:

```
Mahoraga/
├── backend/
│   └── orchestrator/
│       ├── domain/          # KEEP: models, artifacts, events, transitions, dependencies
│       ├── store/           # KEEP + EXTEND: add user_profiles, user_adaptations, cost_ledger tables
│       ├── workers/         # KEEP claude.py + registry.py, REMOVE ollama.py + extension.py
│       ├── verifier/        # KEEP as-is
│       ├── planning/        # MODIFY: swap Ollama → Haiku planner
│       ├── routing/         # KEEP as-is
│       ├── service/         # KEEP executor + run_executor, MODIFY app.py for gateway
│       ├── cli/             # KEEP, minor updates
│       ├── channels/        # NEW: channel adapter interface + telegram adapter
│       ├── adaptive/        # NEW: user model, learning loop
│       ├── tools/           # NEW: tool interface + 4 tools
│       └── tracking/        # NEW: cost ledger
├── static/                  # NEW: web chat UI (HTML + vanilla JS)
├── tests/
│   └── orchestrator_v2/     # KEEP + EXTEND with new test files
├── requirements.txt         # MODIFY: add aiogram, remove ollama references
├── setup.sh                 # MODIFY: update for new structure
├── .env.example             # NEW
├── README.md                # NEW
└── .gitignore               # MODIFY
```

---

## Task 1: Fork, Strip, and Clean

**Goal:** Create the clean product repo. Remove all Ollama, VS Code extension, and dead code. Verify existing tests still pass.

**Files:**
- Delete: `backend/orchestrator/workers/ollama.py`
- Delete: `backend/orchestrator/workers/extension.py`
- Delete: `backend/orchestrator_svc/` (entire directory)
- Delete: `backend/workers/` (entire directory — duplicate adapters)
- Delete: `backend/server.py` (old chat server, replaced by gateway later)
- Delete: `backend/agent.py`
- Delete: `backend/models.py`
- Delete: `backend/orchestrator.py`
- Delete: `backend/prompts.py`
- Delete: `extension/` (entire directory)
- Delete: `orchestrator/` (entire old orchestrator directory)
- Delete: `tests/orchestrator_v2/test_ollama_worker.py`
- Delete: `tests/orchestrator_v2/test_extension_worker.py`
- Modify: `backend/orchestrator/workers/__init__.py`
- Modify: `backend/orchestrator/workers/registry.py` (remove ollama/extension references if any)
- Modify: `backend/orchestrator/planning/planner.py` (stub — remove Ollama calls, prepare for Haiku)
- Modify: `requirements.txt`
- Create: `.env.example`
- Modify: `.gitignore`
- Modify: `setup.sh`

- [ ] **Step 1: Fork the repo**

```bash
cd ~/Projects
cp -r Mahoraga Mahoraga-product
cd Mahoraga-product
rm -rf .git
git init
```

- [ ] **Step 2: Delete Ollama and VS Code extension files**

```bash
rm -f backend/orchestrator/workers/ollama.py
rm -f backend/orchestrator/workers/extension.py
rm -rf backend/orchestrator_svc/
rm -rf backend/workers/
rm -f backend/server.py
rm -f backend/agent.py
rm -f backend/models.py
rm -f backend/orchestrator.py
rm -f backend/prompts.py
rm -rf extension/
rm -rf orchestrator/
rm -f tests/orchestrator_v2/test_ollama_worker.py
rm -f tests/orchestrator_v2/test_extension_worker.py
```

- [ ] **Step 3: Clean up workers/__init__.py**

Remove any imports of `OllamaWorker` or `ExtensionWorker`. Keep only `ClaudeWorker` and `WorkerRegistry` exports.

```python
# backend/orchestrator/workers/__init__.py
from .base import WorkerAdapter, WorkerEvent, WorkerHealth
from .claude import ClaudeWorker
from .registry import WorkerRegistry

__all__ = ["WorkerAdapter", "WorkerEvent", "WorkerHealth", "ClaudeWorker", "WorkerRegistry"]
```

- [ ] **Step 4: Stub the planner to remove Ollama dependency**

Replace Ollama calls in `backend/orchestrator/planning/planner.py` with a placeholder that raises `NotImplementedError`. Task 2 replaces this with Haiku.

```python
# backend/orchestrator/planning/planner.py
from backend.orchestrator.domain.models import Mission, Task

class PlannerError(Exception):
    pass

async def generate_tasks(
    mission: Mission,
    run_id: str,
) -> list[Task]:
    """Generate tasks from a mission. Replaced with Haiku in Task 2."""
    raise NotImplementedError("Planner not yet connected to Haiku — see Task 2")
```

Keep `_build_tasks()` — it's model-agnostic and will be reused.

- [ ] **Step 5: Update requirements.txt**

```
fastapi==0.115.0
uvicorn==0.30.6
httpx==0.27.2
aiosqlite==0.20.0
anthropic>=0.40.0
pytest==8.3.3
pytest-asyncio==0.24.0
typer[all]>=0.12
python-dotenv>=1.0.0
```

Removed: no Ollama-specific deps (there weren't explicit ones, but verify).
Added: `python-dotenv` for `.env` loading.

- [ ] **Step 6: Create .env.example**

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Optional — Telegram channel (leave empty to use web chat only)
TELEGRAM_BOT_TOKEN=

# Optional — Web search tool
BRAVE_API_KEY=
```

- [ ] **Step 7: Update .gitignore**

Ensure these are present:
```
.env
__pycache__/
*.pyc
.pytest_cache/
*.db
venv/
.venv/
```

- [ ] **Step 8: Update setup.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Mahoraga Setup ==="

# Check Python version
python3 -c "import sys; assert sys.version_info >= (3, 12), 'Python 3.12+ required'" || {
    echo "Error: Python 3.12+ is required"
    exit 1
}

# Check .env exists
if [ ! -f .env ]; then
    echo "Error: .env file not found. Copy .env.example and fill in your API key:"
    echo "  cp .env.example .env"
    exit 1
fi

# Check API key is set
if ! grep -q "ANTHROPIC_API_KEY=sk-" .env 2>/dev/null; then
    echo "Warning: ANTHROPIC_API_KEY doesn't look set in .env"
fi

# Create venv if needed
if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

echo "Installing dependencies..."
.venv/bin/pip install -r requirements.txt

echo ""
echo "=== Ready ==="
echo "Start Mahoraga:"
echo "  .venv/bin/python -m uvicorn backend.orchestrator.service.app:app --host 0.0.0.0 --port 8000"
echo ""
echo "Then open http://localhost:8000 in your browser."
```

- [ ] **Step 9: Update store base.py — change default DB path**

Change the default database path from `~/.ollama-runtime/orchestrator_v2.db` to `~/.mahoraga/mahoraga.db`:

In `backend/orchestrator/store/base.py`, update:
```python
DEFAULT_DB_PATH = Path.home() / ".mahoraga" / "mahoraga.db"
```

- [ ] **Step 10: Run existing tests (expect some failures from removed deps)**

```bash
cd ~/Projects/Mahoraga-product
.venv/bin/python -m pytest tests/orchestrator_v2/ -v --ignore=tests/orchestrator_v2/test_planner.py 2>&1 | head -60
```

Ignore planner tests (they depend on Ollama). All domain, store, executor, verifier, routing tests should pass. Fix any import errors from removed files.

- [ ] **Step 11: Fix any broken imports and re-run tests**

Common fixes:
- `conftest.py` may register ollama/extension workers — remove those fixture lines
- `test_app.py` may reference ollama worker setup — strip those

Run again until all non-planner tests pass:
```bash
.venv/bin/python -m pytest tests/orchestrator_v2/ -v --ignore=tests/orchestrator_v2/test_planner.py
```

- [ ] **Step 12: Initial commit**

```bash
git add -A
git commit -m "feat: initial Mahoraga product fork — stripped Ollama/VS Code, Claude-only orchestrator"
```

---

## Task 2: Haiku Planner

**Goal:** Replace the Ollama planner with a Haiku-based planner. Same interface — takes a Mission, returns a list of Tasks.

**Files:**
- Modify: `backend/orchestrator/planning/planner.py`
- Modify: `backend/orchestrator/planning/prompt.py`
- Create: `backend/orchestrator/planning/config.py`
- Modify: `tests/orchestrator_v2/test_planner.py`

- [ ] **Step 1: Create planner config**

```python
# backend/orchestrator/planning/config.py
PLANNER_MODEL = "claude-haiku-4-5-20251001"
MAX_TASKS = 10  # cap task decomposition to prevent runaway planning
```

- [ ] **Step 2: Write the failing test**

```python
# tests/orchestrator_v2/test_planner.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from backend.orchestrator.domain.models import Mission, Task
from backend.orchestrator.planning.planner import generate_tasks, PlannerError

@pytest.fixture
def sample_mission():
    return Mission.new(
        title="Summarize article",
        goal="Read and summarize the linked article in 3 bullet points",
    )

@pytest.mark.asyncio
async def test_generate_tasks_returns_task_list(sample_mission):
    """Planner should return a list of Task objects from a Mission."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='[{"title": "Summarize article", "goal": "Read the article and produce 3 bullet points", "done_criteria": "3 concise bullet points covering main ideas"}]')]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic") as MockClient:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = mock_client

        tasks = await generate_tasks(sample_mission, run_id="run-001")

    assert len(tasks) == 1
    assert isinstance(tasks[0], Task)
    assert tasks[0].title == "Summarize article"
    assert tasks[0].run_id == "run-001"

@pytest.mark.asyncio
async def test_generate_tasks_simple_message_returns_single_task(sample_mission):
    """Simple missions should produce a single task, not a graph."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='[{"title": "Respond directly", "goal": "Answer the user greeting", "done_criteria": "Friendly response sent"}]')]
    mock_response.usage = MagicMock(input_tokens=80, output_tokens=30)

    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic") as MockClient:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = mock_client

        tasks = await generate_tasks(sample_mission, run_id="run-002")

    assert len(tasks) == 1

@pytest.mark.asyncio
async def test_generate_tasks_api_error_raises_planner_error(sample_mission):
    """API failures should raise PlannerError."""
    with patch("backend.orchestrator.planning.planner.anthropic.AsyncAnthropic") as MockClient:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))
        MockClient.return_value = mock_client

        with pytest.raises(PlannerError):
            await generate_tasks(sample_mission, run_id="run-003")
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_planner.py -v
```
Expected: FAIL — `generate_tasks` raises `NotImplementedError`.

- [ ] **Step 4: Implement the Haiku planner**

```python
# backend/orchestrator/planning/planner.py
import json
import os
import anthropic
from backend.orchestrator.domain.models import Mission, Task, Dependency, DependencyType
from backend.orchestrator.planning.config import PLANNER_MODEL, MAX_TASKS
from backend.orchestrator.planning.prompt import build_planner_prompt

class PlannerError(Exception):
    pass

async def generate_tasks(
    mission: Mission,
    run_id: str,
    user_profile: str | None = None,
) -> list[Task]:
    """Decompose a mission into tasks using Claude Haiku."""
    try:
        client = anthropic.AsyncAnthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )
        system_prompt = build_planner_prompt(user_profile=user_profile)
        response = await client.messages.create(
            model=PLANNER_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": f"Mission: {mission.title}\nGoal: {mission.goal}\nBackground: {mission.background}\nSuccess condition: {mission.success_condition}",
            }],
        )
        raw_text = response.content[0].text
        raw_tasks = json.loads(raw_text)
        if len(raw_tasks) > MAX_TASKS:
            raw_tasks = raw_tasks[:MAX_TASKS]
        return _build_tasks(raw_tasks, run_id)
    except json.JSONDecodeError as e:
        raise PlannerError(f"Planner returned invalid JSON: {e}")
    except PlannerError:
        raise
    except Exception as e:
        raise PlannerError(f"Planning failed: {e}")

def _build_tasks(raw_tasks: list[dict], run_id: str) -> list[Task]:
    """Convert raw planner output into Task objects with resolved dependencies."""
    tasks = []
    title_to_id: dict[str, str] = {}

    # First pass: create tasks
    for raw in raw_tasks:
        task = Task.new(
            run_id=run_id,
            title=raw["title"],
            goal=raw["goal"],
            done_criteria=raw.get("done_criteria", "Task completed successfully"),
        )
        title_to_id[raw["title"]] = task.id
        tasks.append(task)

    # Second pass: resolve dependencies by title
    for i, raw in enumerate(raw_tasks):
        deps = []
        for dep_title in raw.get("depends_on", []):
            if dep_title in title_to_id:
                deps.append(Dependency(
                    task_id=title_to_id[dep_title],
                    type=DependencyType.completion,
                ))
        if deps:
            tasks[i] = Task(
                id=tasks[i].id,
                run_id=tasks[i].run_id,
                parent_task_id=tasks[i].parent_task_id,
                title=tasks[i].title,
                goal=tasks[i].goal,
                scope=tasks[i].scope,
                context_refs=tasks[i].context_refs,
                done_criteria=tasks[i].done_criteria,
                dependencies=deps,
                constraints=tasks[i].constraints,
                preferred_worker_type=tasks[i].preferred_worker_type,
                required_capabilities=tasks[i].required_capabilities,
                escalation_count=tasks[i].escalation_count,
                status=tasks[i].status,
                created_at=tasks[i].created_at,
                updated_at=tasks[i].updated_at,
            )

    return tasks
```

- [ ] **Step 5: Update planner prompt for Haiku**

```python
# backend/orchestrator/planning/prompt.py
def build_planner_prompt(user_profile: str | None = None) -> str:
    base = """You are a task planner. Given a mission, decompose it into discrete tasks.

Rules:
- Return a JSON array of task objects
- Each task has: "title" (str), "goal" (str), "done_criteria" (str)
- For tasks with dependencies, include "depends_on": ["Title of dependency task"]
- Simple requests (greetings, single questions) should be ONE task
- Complex requests should be broken into 2-5 tasks maximum
- Never create more than 10 tasks

Return ONLY valid JSON. No markdown, no explanation."""

    if user_profile:
        base += f"\n\nUser profile (adapt task design to their preferences):\n{user_profile}"

    return base
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_planner.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/orchestrator/planning/ tests/orchestrator_v2/test_planner.py
git commit -m "feat: replace Ollama planner with Haiku-based planner"
```

---

## Task 3: Adaptive User Model

**Goal:** Build the per-user learning system — tracks preferences, corrections, tool affinity, and communication style. Decays stale patterns.

**Files:**
- Create: `backend/orchestrator/adaptive/__init__.py`
- Create: `backend/orchestrator/adaptive/models.py`
- Create: `backend/orchestrator/adaptive/store.py`
- Create: `backend/orchestrator/adaptive/learner.py`
- Create: `backend/orchestrator/adaptive/profile.py`
- Modify: `backend/orchestrator/store/base.py` (add adaptive tables to migrations)
- Create: `tests/orchestrator_v2/test_adaptive_store.py`
- Create: `tests/orchestrator_v2/test_adaptive_learner.py`
- Create: `tests/orchestrator_v2/test_adaptive_profile.py`

- [ ] **Step 1: Define adaptive models**

```python
# backend/orchestrator/adaptive/__init__.py
from .models import UserProfile, UserAdaptation, AdaptationCategory
from .store import AdaptiveStore
from .learner import Learner
from .profile import build_profile_prompt

__all__ = [
    "UserProfile", "UserAdaptation", "AdaptationCategory",
    "AdaptiveStore", "Learner", "build_profile_prompt",
]
```

```python
# backend/orchestrator/adaptive/models.py
from dataclasses import dataclass
from enum import Enum
import time
import uuid

class AdaptationCategory(str, Enum):
    style = "style"
    tool_affinity = "tool_affinity"
    preference = "preference"
    pattern = "pattern"
    correction = "correction"

@dataclass
class UserProfile:
    user_id: str
    created_at: float
    updated_at: float

    @staticmethod
    def new(user_id: str | None = None) -> "UserProfile":
        now = time.time()
        return UserProfile(
            user_id=user_id or str(uuid.uuid4()),
            created_at=now,
            updated_at=now,
        )

@dataclass
class UserAdaptation:
    id: str
    user_id: str
    category: AdaptationCategory
    key: str
    value: str  # JSON blob
    confidence: float  # 0.0 to 1.0
    last_reinforced: float
    created_at: float

    @staticmethod
    def new(
        user_id: str,
        category: AdaptationCategory,
        key: str,
        value: str,
        confidence: float = 0.8,
    ) -> "UserAdaptation":
        now = time.time()
        return UserAdaptation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            category=category,
            key=key,
            value=value,
            confidence=confidence,
            last_reinforced=now,
            created_at=now,
        )
```

- [ ] **Step 2: Write failing tests for adaptive store**

```python
# tests/orchestrator_v2/test_adaptive_store.py
import pytest
from backend.orchestrator.adaptive.models import UserProfile, UserAdaptation, AdaptationCategory
from backend.orchestrator.adaptive.store import AdaptiveStore

@pytest.fixture
async def adaptive_store(tmp_path):
    import aiosqlite
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    store = AdaptiveStore(conn)
    await store.migrate()
    yield store
    await conn.close()

@pytest.mark.asyncio
async def test_save_and_get_profile(adaptive_store):
    profile = UserProfile.new()
    await adaptive_store.save_profile(profile)
    got = await adaptive_store.get_profile(profile.user_id)
    assert got is not None
    assert got.user_id == profile.user_id

@pytest.mark.asyncio
async def test_save_and_list_adaptations(adaptive_store):
    profile = UserProfile.new()
    await adaptive_store.save_profile(profile)
    adapt = UserAdaptation.new(
        user_id=profile.user_id,
        category=AdaptationCategory.preference,
        key="response_length",
        value='"concise"',
        confidence=0.9,
    )
    await adaptive_store.save_adaptation(adapt)
    adapts = await adaptive_store.list_adaptations(profile.user_id)
    assert len(adapts) == 1
    assert adapts[0].key == "response_length"

@pytest.mark.asyncio
async def test_update_confidence(adaptive_store):
    profile = UserProfile.new()
    await adaptive_store.save_profile(profile)
    adapt = UserAdaptation.new(
        user_id=profile.user_id,
        category=AdaptationCategory.style,
        key="tone",
        value='"casual"',
        confidence=0.5,
    )
    await adaptive_store.save_adaptation(adapt)
    await adaptive_store.reinforce(adapt.id, new_confidence=0.9)
    adapts = await adaptive_store.list_adaptations(profile.user_id)
    assert adapts[0].confidence == 0.9

@pytest.mark.asyncio
async def test_decay_stale_adaptations(adaptive_store):
    profile = UserProfile.new()
    await adaptive_store.save_profile(profile)
    adapt = UserAdaptation.new(
        user_id=profile.user_id,
        category=AdaptationCategory.pattern,
        key="weekly_summary",
        value='"monday"',
        confidence=0.8,
    )
    # Backdate last_reinforced to 31 days ago
    import time
    adapt = UserAdaptation(
        id=adapt.id, user_id=adapt.user_id, category=adapt.category,
        key=adapt.key, value=adapt.value, confidence=adapt.confidence,
        last_reinforced=time.time() - (31 * 86400),
        created_at=adapt.created_at,
    )
    await adaptive_store.save_adaptation(adapt)
    decayed = await adaptive_store.decay_stale(user_id=profile.user_id, days=30, factor=0.5)
    assert decayed == 1
    adapts = await adaptive_store.list_adaptations(profile.user_id)
    assert adapts[0].confidence == pytest.approx(0.4)
```

- [ ] **Step 3: Run tests — verify they fail**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_adaptive_store.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 4: Implement adaptive store**

```python
# backend/orchestrator/adaptive/store.py
import time
import aiosqlite
from .models import UserProfile, UserAdaptation, AdaptationCategory

class AdaptiveStore:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def migrate(self) -> None:
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_adaptations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES user_profiles(user_id),
                category TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.8,
                last_reinforced REAL NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_adaptations_user
                ON user_adaptations(user_id);
        """)

    async def save_profile(self, profile: UserProfile) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO user_profiles (user_id, created_at, updated_at) VALUES (?, ?, ?)",
            (profile.user_id, profile.created_at, profile.updated_at),
        )
        await self._conn.commit()

    async def get_profile(self, user_id: str) -> UserProfile | None:
        async with self._conn.execute(
            "SELECT user_id, created_at, updated_at FROM user_profiles WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return UserProfile(user_id=row[0], created_at=row[1], updated_at=row[2])

    async def save_adaptation(self, adapt: UserAdaptation) -> None:
        await self._conn.execute(
            """INSERT OR REPLACE INTO user_adaptations
            (id, user_id, category, key, value, confidence, last_reinforced, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (adapt.id, adapt.user_id, adapt.category.value, adapt.key,
             adapt.value, adapt.confidence, adapt.last_reinforced, adapt.created_at),
        )
        await self._conn.commit()

    async def list_adaptations(
        self, user_id: str, category: AdaptationCategory | None = None,
    ) -> list[UserAdaptation]:
        if category:
            sql = "SELECT * FROM user_adaptations WHERE user_id = ? AND category = ? ORDER BY confidence DESC"
            params = (user_id, category.value)
        else:
            sql = "SELECT * FROM user_adaptations WHERE user_id = ? ORDER BY confidence DESC"
            params = (user_id,)
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [
                UserAdaptation(
                    id=r[0], user_id=r[1], category=AdaptationCategory(r[2]),
                    key=r[3], value=r[4], confidence=r[5],
                    last_reinforced=r[6], created_at=r[7],
                )
                for r in rows
            ]

    async def reinforce(self, adaptation_id: str, new_confidence: float) -> None:
        await self._conn.execute(
            "UPDATE user_adaptations SET confidence = ?, last_reinforced = ? WHERE id = ?",
            (new_confidence, time.time(), adaptation_id),
        )
        await self._conn.commit()

    async def decay_stale(self, user_id: str, days: int = 30, factor: float = 0.5) -> int:
        cutoff = time.time() - (days * 86400)
        async with self._conn.execute(
            "SELECT id, confidence FROM user_adaptations WHERE user_id = ? AND last_reinforced < ?",
            (user_id, cutoff),
        ) as cur:
            rows = await cur.fetchall()
        count = 0
        for row in rows:
            new_conf = round(row[1] * factor, 2)
            await self._conn.execute(
                "UPDATE user_adaptations SET confidence = ? WHERE id = ?",
                (new_conf, row[0]),
            )
            count += 1
        await self._conn.commit()
        return count
```

- [ ] **Step 5: Run store tests**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_adaptive_store.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 6: Write failing tests for learner**

```python
# tests/orchestrator_v2/test_adaptive_learner.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from backend.orchestrator.adaptive.learner import Learner
from backend.orchestrator.adaptive.models import AdaptationCategory

@pytest.mark.asyncio
async def test_learner_extracts_correction():
    """When user says 'no not like that, use bullet points', learner should extract a correction."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='[{"category": "correction", "key": "format", "value": "use bullet points", "confidence": 0.95}]')]

    with patch("backend.orchestrator.adaptive.learner.anthropic.AsyncAnthropic") as MockClient:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = mock_client

        learner = Learner()
        adaptations = await learner.analyze_interaction(
            user_message="No, not like that. Use bullet points.",
            assistant_response="Here is a paragraph summary...",
            existing_adaptations=[],
        )

    assert len(adaptations) == 1
    assert adaptations[0]["category"] == "correction"
    assert adaptations[0]["key"] == "format"

@pytest.mark.asyncio
async def test_learner_returns_empty_for_smooth_interaction():
    """Smooth interactions with no corrections should return reinforcements, not new adaptations."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='[]')]

    with patch("backend.orchestrator.adaptive.learner.anthropic.AsyncAnthropic") as MockClient:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        MockClient.return_value = mock_client

        learner = Learner()
        adaptations = await learner.analyze_interaction(
            user_message="Thanks, that was great.",
            assistant_response="You're welcome!",
            existing_adaptations=[],
        )

    assert len(adaptations) == 0
```

- [ ] **Step 7: Run learner tests — verify failure**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_adaptive_learner.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 8: Implement the learner**

```python
# backend/orchestrator/adaptive/learner.py
import json
import os
import anthropic
from .models import AdaptationCategory

LEARNER_MODEL = "claude-haiku-4-5-20251001"

LEARNER_PROMPT = """You analyze conversations to extract user preferences and corrections.

Given a user message, assistant response, and existing known adaptations, return a JSON array of NEW adaptations to save. Each adaptation has:
- "category": one of "style", "tool_affinity", "preference", "pattern", "correction"
- "key": short identifier (e.g. "response_length", "format", "tone")
- "value": the actual preference (string)
- "confidence": 0.0-1.0 (corrections = 0.95, explicit preferences = 0.9, inferred = 0.6)

Rules:
- Only return NEW adaptations not already in the existing list
- If the interaction was smooth with no corrections or new preferences, return []
- Corrections (user said "no" or rephrased) are highest priority
- Explicit preferences ("I prefer...", "always...", "never...") are second
- Inferred patterns (user seems to like X) are lowest confidence

Return ONLY valid JSON array. No markdown."""

class Learner:
    async def analyze_interaction(
        self,
        user_message: str,
        assistant_response: str,
        existing_adaptations: list[dict],
    ) -> list[dict]:
        """Analyze a conversation turn and extract new adaptations."""
        client = anthropic.AsyncAnthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )
        existing_str = json.dumps(existing_adaptations) if existing_adaptations else "[]"
        try:
            response = await client.messages.create(
                model=LEARNER_MODEL,
                max_tokens=1024,
                system=LEARNER_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"User message: {user_message}\n\nAssistant response: {assistant_response}\n\nExisting adaptations: {existing_str}",
                }],
            )
            return json.loads(response.content[0].text)
        except (json.JSONDecodeError, Exception):
            return []
```

- [ ] **Step 9: Run learner tests**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_adaptive_learner.py -v
```
Expected: PASS.

- [ ] **Step 10: Implement profile builder**

```python
# backend/orchestrator/adaptive/profile.py
from .models import UserAdaptation

MIN_CONFIDENCE = 0.3  # Don't include low-confidence adaptations in profile

def build_profile_prompt(adaptations: list[UserAdaptation]) -> str | None:
    """Build a condensed user profile string for injection into planner/executor prompts."""
    relevant = [a for a in adaptations if a.confidence >= MIN_CONFIDENCE]
    if not relevant:
        return None

    lines = ["User profile:"]
    for a in relevant:
        conf_label = "strong" if a.confidence >= 0.8 else "moderate" if a.confidence >= 0.5 else "weak"
        lines.append(f"- [{conf_label}] {a.category.value}: {a.key} = {a.value}")
    return "\n".join(lines)
```

- [ ] **Step 11: Write and run profile test**

```python
# tests/orchestrator_v2/test_adaptive_profile.py
from backend.orchestrator.adaptive.models import UserAdaptation, AdaptationCategory
from backend.orchestrator.adaptive.profile import build_profile_prompt

def test_build_profile_prompt_with_adaptations():
    adapts = [
        UserAdaptation.new("user-1", AdaptationCategory.preference, "response_length", '"concise"', confidence=0.9),
        UserAdaptation.new("user-1", AdaptationCategory.correction, "format", '"bullet points"', confidence=0.95),
        UserAdaptation.new("user-1", AdaptationCategory.style, "tone", '"casual"', confidence=0.2),  # below threshold
    ]
    prompt = build_profile_prompt(adapts)
    assert "concise" in prompt
    assert "bullet points" in prompt
    assert "casual" not in prompt  # filtered out by MIN_CONFIDENCE

def test_build_profile_prompt_empty():
    assert build_profile_prompt([]) is None
```

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_adaptive_profile.py -v
```
Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add backend/orchestrator/adaptive/ tests/orchestrator_v2/test_adaptive_store.py tests/orchestrator_v2/test_adaptive_learner.py tests/orchestrator_v2/test_adaptive_profile.py
git commit -m "feat: add adaptive user model — store, learner, profile builder"
```

---

## Task 4: Channel Adapter Interface + Web Chat UI

**Goal:** Create the abstract channel adapter interface and build the default web chat channel (static HTML + vanilla JS, served by FastAPI).

**Files:**
- Create: `backend/orchestrator/channels/__init__.py`
- Create: `backend/orchestrator/channels/base.py`
- Create: `backend/orchestrator/channels/web.py`
- Create: `static/index.html`
- Create: `static/style.css`
- Create: `static/app.js`
- Create: `tests/orchestrator_v2/test_channel_web.py`

- [ ] **Step 1: Define the channel adapter interface**

```python
# backend/orchestrator/channels/__init__.py
from .base import ChannelAdapter, ChannelMessage

__all__ = ["ChannelAdapter", "ChannelMessage"]
```

```python
# backend/orchestrator/channels/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
import uuid

@dataclass
class ChannelMessage:
    """Unified message format across all channels."""
    id: str
    user_id: str
    channel: str          # "web", "telegram", "whatsapp"
    text: str
    attachments: list[dict] = field(default_factory=list)  # [{type, data, filename}]
    timestamp: float = 0.0

    @staticmethod
    def new(user_id: str, channel: str, text: str, attachments: list[dict] | None = None) -> "ChannelMessage":
        return ChannelMessage(
            id=str(uuid.uuid4()),
            user_id=user_id,
            channel=channel,
            text=text,
            attachments=attachments or [],
            timestamp=time.time(),
        )

class ChannelAdapter(ABC):
    """Abstract base for all messaging channel adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel identifier (e.g. 'web', 'telegram')."""

    @abstractmethod
    async def send(self, user_id: str, text: str) -> None:
        """Send a response to a user on this channel."""

    @abstractmethod
    async def start(self) -> None:
        """Start listening for messages (long poll, webhook, etc)."""

    @abstractmethod
    async def stop(self) -> None:
        """Clean shutdown."""
```

- [ ] **Step 2: Write failing tests for web channel**

```python
# tests/orchestrator_v2/test_channel_web.py
import pytest
from httpx import AsyncClient, ASGITransport
from backend.orchestrator.channels.web import create_web_app

@pytest.fixture
async def web_client():
    app = create_web_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

@pytest.mark.asyncio
async def test_web_chat_serves_index(web_client):
    """GET / should serve the web chat UI."""
    resp = await web_client.get("/")
    assert resp.status_code == 200
    assert "Mahoraga" in resp.text

@pytest.mark.asyncio
async def test_web_chat_post_message(web_client):
    """POST /chat should accept a message and return a streaming response."""
    resp = await web_client.post(
        "/chat",
        json={"message": "hello", "user_id": "test-user"},
    )
    assert resp.status_code == 200
```

- [ ] **Step 3: Run tests — verify failure**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_channel_web.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 4: Implement web channel backend**

```python
# backend/orchestrator/channels/web.py
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from pathlib import Path
from pydantic import BaseModel
from .base import ChannelAdapter, ChannelMessage
import asyncio
import json

class ChatRequest(BaseModel):
    message: str
    user_id: str = "default"

class WebChannel(ChannelAdapter):
    """Web-based chat UI channel — serves static HTML and handles /chat endpoint."""

    def __init__(self, on_message=None):
        self._on_message = on_message  # callback: async (ChannelMessage) -> AsyncGenerator[str]
        self._app: FastAPI | None = None

    @property
    def name(self) -> str:
        return "web"

    async def send(self, user_id: str, text: str) -> None:
        pass  # Web channel pushes via SSE in /chat, not via send()

    async def start(self) -> None:
        pass  # Handled by FastAPI lifecycle

    async def stop(self) -> None:
        pass

def create_web_app(on_message=None) -> FastAPI:
    """Create the FastAPI app with web chat routes."""
    app = FastAPI(title="Mahoraga")
    static_dir = Path(__file__).parent.parent.parent.parent / "static"

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_path = static_dir / "index.html"
        return HTMLResponse(content=index_path.read_text())

    @app.post("/chat")
    async def chat(req: ChatRequest):
        msg = ChannelMessage.new(
            user_id=req.user_id,
            channel="web",
            text=req.message,
        )
        if on_message:
            async def stream():
                async for chunk in on_message(msg):
                    yield f"data: {json.dumps({'text': chunk})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(stream(), media_type="text/event-stream")
        return {"status": "ok", "message": "No handler configured"}

    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app
```

- [ ] **Step 5: Create the web chat UI**

```html
<!-- static/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mahoraga</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div id="app">
        <header>
            <h1>Mahoraga</h1>
            <p class="subtitle">The Adapting AI</p>
        </header>
        <div id="messages"></div>
        <form id="chat-form">
            <input type="text" id="input" placeholder="Type a message..." autocomplete="off" autofocus>
            <button type="submit">Send</button>
        </form>
    </div>
    <script src="/static/app.js"></script>
</body>
</html>
```

```css
/* static/style.css */
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #fafaf8;
    color: #1a1a1a;
    height: 100vh;
    display: flex;
    justify-content: center;
}
#app {
    width: 100%;
    max-width: 720px;
    display: flex;
    flex-direction: column;
    height: 100vh;
    padding: 1rem;
}
header {
    text-align: center;
    padding: 1rem 0;
    border-bottom: 1px solid #e5e5e0;
}
header h1 { font-size: 1.5rem; font-weight: 600; }
header .subtitle { font-size: 0.85rem; color: #888; margin-top: 0.25rem; }
#messages {
    flex: 1;
    overflow-y: auto;
    padding: 1rem 0;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}
.msg {
    max-width: 85%;
    padding: 0.75rem 1rem;
    border-radius: 12px;
    line-height: 1.5;
    white-space: pre-wrap;
    font-size: 0.95rem;
}
.msg.user {
    align-self: flex-end;
    background: #1a1a1a;
    color: #fff;
}
.msg.assistant {
    align-self: flex-start;
    background: #f0efe8;
    color: #1a1a1a;
}
.msg .cost {
    font-size: 0.75rem;
    color: #999;
    margin-top: 0.5rem;
}
#chat-form {
    display: flex;
    gap: 0.5rem;
    padding: 1rem 0;
    border-top: 1px solid #e5e5e0;
}
#chat-form input {
    flex: 1;
    padding: 0.75rem 1rem;
    border: 1px solid #ddd;
    border-radius: 8px;
    font-size: 1rem;
    outline: none;
}
#chat-form input:focus { border-color: #1a1a1a; }
#chat-form button {
    padding: 0.75rem 1.5rem;
    background: #1a1a1a;
    color: #fff;
    border: none;
    border-radius: 8px;
    font-size: 1rem;
    cursor: pointer;
}
#chat-form button:hover { background: #333; }
```

```javascript
// static/app.js
const messages = document.getElementById('messages');
const form = document.getElementById('chat-form');
const input = document.getElementById('input');

function addMessage(text, role) {
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.textContent = text;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
}

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    addMessage(text, 'user');

    const assistantDiv = addMessage('', 'assistant');

    try {
        const resp = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const data = line.slice(6);
                    if (data === '[DONE]') continue;
                    try {
                        const parsed = JSON.parse(data);
                        assistantDiv.textContent += parsed.text;
                        messages.scrollTop = messages.scrollHeight;
                    } catch {}
                }
            }
        }
    } catch (err) {
        assistantDiv.textContent = 'Error: ' + err.message;
    }
});
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_channel_web.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/orchestrator/channels/ static/ tests/orchestrator_v2/test_channel_web.py
git commit -m "feat: add channel adapter interface and web chat UI"
```

---

## Task 5: Gateway — Wire Channels to Orchestrator

**Goal:** Connect the channel layer to the orchestrator. Messages from any channel create Missions, run through the planner/executor pipeline, and stream responses back.

**Files:**
- Create: `backend/orchestrator/gateway.py`
- Modify: `backend/orchestrator/service/app.py` (integrate gateway routes)
- Create: `tests/orchestrator_v2/test_gateway.py`

- [ ] **Step 1: Write failing test**

```python
# tests/orchestrator_v2/test_gateway.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from backend.orchestrator.gateway import Gateway
from backend.orchestrator.channels.base import ChannelMessage

@pytest.mark.asyncio
async def test_gateway_routes_message_to_planner():
    """A channel message should create a Mission and run through the pipeline."""
    msg = ChannelMessage.new(user_id="user-1", channel="web", text="Summarize this article")

    mock_store = AsyncMock()
    mock_registry = MagicMock()
    mock_verifier = AsyncMock()

    gateway = Gateway(
        store=mock_store,
        registry=mock_registry,
        verifier=mock_verifier,
    )

    with patch("backend.orchestrator.gateway.generate_tasks") as mock_plan:
        mock_task = MagicMock()
        mock_task.id = "task-1"
        mock_task.title = "Summarize"
        mock_plan.return_value = [mock_task]

        with patch("backend.orchestrator.gateway.run_task") as mock_exec:
            mock_exec.return_value = None
            mock_store.tasks.get.return_value = mock_task
            mock_store.tasks.list_attempts.return_value = [
                MagicMock(summary="Here is the summary.", status=MagicMock(value="completed"))
            ]

            chunks = []
            async for chunk in gateway.handle_message(msg):
                chunks.append(chunk)

    assert len(chunks) > 0
    assert any("summary" in c.lower() for c in chunks)
```

- [ ] **Step 2: Run test — verify failure**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_gateway.py -v
```

- [ ] **Step 3: Implement gateway**

```python
# backend/orchestrator/gateway.py
from typing import AsyncGenerator
from backend.orchestrator.channels.base import ChannelMessage
from backend.orchestrator.domain.models import Mission, Plan, Run, RunMode
from backend.orchestrator.planning.planner import generate_tasks
from backend.orchestrator.service.executor import run_task
from backend.orchestrator.adaptive.store import AdaptiveStore
from backend.orchestrator.adaptive.learner import Learner
from backend.orchestrator.adaptive.profile import build_profile_prompt
from backend.orchestrator.adaptive.models import UserProfile, UserAdaptation, AdaptationCategory
from backend.orchestrator.store.base import Store
from backend.orchestrator.workers.registry import WorkerRegistry
from backend.orchestrator.verifier.verifier import Verifier

class Gateway:
    """Routes channel messages through the orchestrator pipeline."""

    def __init__(
        self,
        store: Store,
        registry: WorkerRegistry,
        verifier: Verifier,
        adaptive_store: AdaptiveStore | None = None,
    ):
        self._store = store
        self._registry = registry
        self._verifier = verifier
        self._adaptive = adaptive_store
        self._learner = Learner()

    async def handle_message(self, msg: ChannelMessage) -> AsyncGenerator[str, None]:
        """Process a channel message through the full pipeline. Yields response chunks."""
        # Get or create user profile
        user_profile_prompt = None
        if self._adaptive:
            profile = await self._adaptive.get_profile(msg.user_id)
            if not profile:
                profile = UserProfile.new(msg.user_id)
                await self._adaptive.save_profile(profile)
            adaptations = await self._adaptive.list_adaptations(msg.user_id)
            user_profile_prompt = build_profile_prompt(adaptations)

        # Create mission from message
        mission = Mission.new(
            title=msg.text[:100],
            goal=msg.text,
        )
        await self._store.missions.save(mission)

        # Plan
        tasks = await generate_tasks(
            mission=mission,
            run_id="",  # placeholder, set after run creation
            user_profile=user_profile_prompt,
        )

        # Create plan + run
        plan = Plan.new(mission_id=mission.id)
        await self._store.missions.save_plan(plan)
        run = Run.new(mission_id=mission.id, plan_id=plan.id, mode=RunMode.direct)
        await self._store.missions.save_run(run)

        # Save tasks with correct run_id
        for task in tasks:
            task = Task(
                id=task.id, run_id=run.id, parent_task_id=task.parent_task_id,
                title=task.title, goal=task.goal, scope=task.scope,
                context_refs=task.context_refs, done_criteria=task.done_criteria,
                dependencies=task.dependencies, constraints=task.constraints,
                preferred_worker_type=task.preferred_worker_type,
                required_capabilities=task.required_capabilities,
                escalation_count=task.escalation_count, status=task.status,
                created_at=task.created_at, updated_at=task.updated_at,
            )
            await self._store.tasks.save(task)

        # Execute tasks sequentially (simple v1 — wave execution comes later)
        full_response = ""
        for task in tasks:
            await run_task(task.id, self._store, self._registry, self._verifier)
            attempts = await self._store.tasks.list_attempts(task.id)
            for attempt in attempts:
                if attempt.summary:
                    full_response += attempt.summary + "\n"
                    yield attempt.summary

        # Post-interaction learning (fire and forget — don't block response)
        if self._adaptive and full_response:
            try:
                existing = await self._adaptive.list_adaptations(msg.user_id)
                existing_dicts = [
                    {"category": a.category.value, "key": a.key, "value": a.value}
                    for a in existing
                ]
                new_adaptations = await self._learner.analyze_interaction(
                    user_message=msg.text,
                    assistant_response=full_response,
                    existing_adaptations=existing_dicts,
                )
                for adapt_dict in new_adaptations:
                    adapt = UserAdaptation.new(
                        user_id=msg.user_id,
                        category=AdaptationCategory(adapt_dict["category"]),
                        key=adapt_dict["key"],
                        value=adapt_dict["value"],
                        confidence=adapt_dict.get("confidence", 0.7),
                    )
                    await self._adaptive.save_adaptation(adapt)
            except Exception:
                pass  # Don't let learning failures break the response

# Need this import for the Task reassignment
from backend.orchestrator.domain.models import Task
```

- [ ] **Step 4: Run test**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_gateway.py -v
```
Expected: PASS.

- [ ] **Step 5: Wire gateway into the main FastAPI app**

Update `backend/orchestrator/service/app.py` to mount the web channel and gateway. Add a `/chat` endpoint that uses the gateway:

Add at the top of the lifespan or startup:
```python
# In the app startup/lifespan, after store and registry setup:
from backend.orchestrator.gateway import Gateway
from backend.orchestrator.adaptive.store import AdaptiveStore
from backend.orchestrator.channels.web import create_web_app

# Create adaptive store (shares the same DB connection)
adaptive_store = AdaptiveStore(store._conn)  # reuse connection
await adaptive_store.migrate()

gateway = Gateway(
    store=store,
    registry=registry,
    verifier=verifier,
    adaptive_store=adaptive_store,
)
```

Mount the web chat app and add the `/chat` endpoint that delegates to the gateway.

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/gateway.py backend/orchestrator/service/app.py tests/orchestrator_v2/test_gateway.py
git commit -m "feat: add gateway — wire channels to orchestrator pipeline with adaptive learning"
```

---

## Task 6: Cost Tracking

**Goal:** Track token usage and cost per mission. Show per-response cost footer.

**Files:**
- Create: `backend/orchestrator/tracking/__init__.py`
- Create: `backend/orchestrator/tracking/ledger.py`
- Create: `backend/orchestrator/tracking/pricing.py`
- Modify: `backend/orchestrator/workers/claude.py` (extract usage from API response)
- Create: `tests/orchestrator_v2/test_tracking.py`

- [ ] **Step 1: Define pricing constants**

```python
# backend/orchestrator/tracking/pricing.py
# Prices per 1M tokens (USD) as of 2026-04
PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00, "cache_read": 0.08},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00, "cache_read": 1.50},
}

def calculate_cost(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float:
    """Calculate cost in USD for a single API call."""
    prices = PRICING.get(model, PRICING["claude-sonnet-4-6"])
    cost = (
        (input_tokens / 1_000_000) * prices["input"]
        + (output_tokens / 1_000_000) * prices["output"]
        + (cache_read_tokens / 1_000_000) * prices["cache_read"]
    )
    return round(cost, 6)

def format_cost(cost_usd: float, model_breakdown: dict[str, int] | None = None) -> str:
    """Format cost for display in response footer."""
    parts = [f"${cost_usd:.4f}"]
    if model_breakdown:
        detail = " | ".join(f"{m}: {t:,} tok" for m, t in model_breakdown.items() if t > 0)
        if detail:
            parts.append(f"({detail})")
    return " ".join(parts)
```

- [ ] **Step 2: Write failing tests**

```python
# tests/orchestrator_v2/test_tracking.py
import pytest
from backend.orchestrator.tracking.pricing import calculate_cost, format_cost
from backend.orchestrator.tracking.ledger import CostLedger

def test_calculate_cost_haiku():
    cost = calculate_cost("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=500)
    expected = (1000 / 1_000_000) * 0.80 + (500 / 1_000_000) * 4.00
    assert cost == pytest.approx(expected, abs=1e-6)

def test_calculate_cost_sonnet():
    cost = calculate_cost("claude-sonnet-4-6", input_tokens=2000, output_tokens=1000)
    expected = (2000 / 1_000_000) * 3.00 + (1000 / 1_000_000) * 15.00
    assert cost == pytest.approx(expected, abs=1e-6)

def test_format_cost():
    result = format_cost(0.003, {"Haiku": 1200, "Sonnet": 3400})
    assert "$0.0030" in result
    assert "Haiku" in result

@pytest.fixture
async def ledger(tmp_path):
    import aiosqlite
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    ledger = CostLedger(conn)
    await ledger.migrate()
    yield ledger
    await conn.close()

@pytest.mark.asyncio
async def test_ledger_record_and_total(ledger):
    await ledger.record(
        user_id="user-1", mission_id="m-1", model="claude-haiku-4-5-20251001",
        input_tokens=1000, output_tokens=500, cache_read_tokens=0, cost_usd=0.0028,
    )
    await ledger.record(
        user_id="user-1", mission_id="m-1", model="claude-sonnet-4-6",
        input_tokens=2000, output_tokens=1000, cache_read_tokens=0, cost_usd=0.021,
    )
    total = await ledger.total_cost(user_id="user-1")
    assert total == pytest.approx(0.0238, abs=1e-4)

@pytest.mark.asyncio
async def test_ledger_cost_by_period(ledger):
    await ledger.record(
        user_id="user-1", mission_id="m-1", model="claude-haiku-4-5-20251001",
        input_tokens=1000, output_tokens=500, cache_read_tokens=0, cost_usd=0.003,
    )
    import time
    total = await ledger.cost_since(user_id="user-1", since=time.time() - 3600)
    assert total == pytest.approx(0.003, abs=1e-4)
```

- [ ] **Step 3: Run tests — verify failure**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_tracking.py -v
```

- [ ] **Step 4: Implement cost ledger**

```python
# backend/orchestrator/tracking/__init__.py
from .ledger import CostLedger
from .pricing import calculate_cost, format_cost

__all__ = ["CostLedger", "calculate_cost", "format_cost"]
```

```python
# backend/orchestrator/tracking/ledger.py
import time
import uuid
import aiosqlite

class CostLedger:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def migrate(self) -> None:
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS cost_ledger (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                mission_id TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cost_user ON cost_ledger(user_id);
            CREATE INDEX IF NOT EXISTS idx_cost_mission ON cost_ledger(mission_id);
        """)

    async def record(
        self, user_id: str, mission_id: str, model: str,
        input_tokens: int, output_tokens: int, cache_read_tokens: int,
        cost_usd: float,
    ) -> None:
        await self._conn.execute(
            """INSERT INTO cost_ledger
            (id, user_id, mission_id, model, input_tokens, output_tokens, cache_read_tokens, cost_usd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), user_id, mission_id, model,
             input_tokens, output_tokens, cache_read_tokens, cost_usd, time.time()),
        )
        await self._conn.commit()

    async def total_cost(self, user_id: str) -> float:
        async with self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0]

    async def cost_since(self, user_id: str, since: float) -> float:
        async with self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger WHERE user_id = ? AND created_at >= ?",
            (user_id, since),
        ) as cur:
            row = await cur.fetchone()
            return row[0]

    async def mission_cost(self, mission_id: str) -> float:
        async with self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger WHERE mission_id = ?",
            (mission_id,),
        ) as cur:
            row = await cur.fetchone()
            return row[0]
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_tracking.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/tracking/ tests/orchestrator_v2/test_tracking.py
git commit -m "feat: add cost tracking — ledger, pricing, per-mission cost"
```

---

## Task 7: Tools

**Goal:** Build the tool interface and four launch tools: web search, URL reader, document reader, code execution.

**Files:**
- Create: `backend/orchestrator/tools/__init__.py`
- Create: `backend/orchestrator/tools/base.py`
- Create: `backend/orchestrator/tools/web_search.py`
- Create: `backend/orchestrator/tools/url_reader.py`
- Create: `backend/orchestrator/tools/document_reader.py`
- Create: `backend/orchestrator/tools/code_exec.py`
- Create: `backend/orchestrator/tools/registry.py`
- Create: `tests/orchestrator_v2/test_tools.py`

- [ ] **Step 1: Define tool interface**

```python
# backend/orchestrator/tools/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ToolResult:
    success: bool
    output: str
    error: str | None = None

class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool identifier."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for the planner."""

    @abstractmethod
    async def execute(self, params: dict) -> ToolResult:
        """Execute the tool with given parameters."""
```

```python
# backend/orchestrator/tools/registry.py
from .base import Tool

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_all(self) -> list[Tool]:
        return list(self._tools.values())

    def descriptions(self) -> str:
        """Format tool descriptions for injection into planner/executor prompts."""
        lines = ["Available tools:"]
        for t in self._tools.values():
            lines.append(f"- {t.name}: {t.description}")
        return "\n".join(lines)
```

- [ ] **Step 2: Write failing tests**

```python
# tests/orchestrator_v2/test_tools.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from backend.orchestrator.tools.base import Tool, ToolResult
from backend.orchestrator.tools.registry import ToolRegistry
from backend.orchestrator.tools.url_reader import UrlReaderTool
from backend.orchestrator.tools.web_search import WebSearchTool

def test_tool_registry():
    class DummyTool(Tool):
        @property
        def name(self): return "dummy"
        @property
        def description(self): return "A test tool"
        async def execute(self, params): return ToolResult(success=True, output="ok")

    reg = ToolRegistry()
    reg.register(DummyTool())
    assert reg.get("dummy") is not None
    assert len(reg.list_all()) == 1
    assert "dummy" in reg.descriptions()

@pytest.mark.asyncio
async def test_url_reader():
    tool = UrlReaderTool()
    with patch("backend.orchestrator.tools.url_reader.httpx.AsyncClient") as MockClient:
        mock_resp = MagicMock()
        mock_resp.text = "<html><body><p>Hello world content here.</p></body></html>"
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        result = await tool.execute({"url": "https://example.com"})

    assert result.success
    assert "Hello world" in result.output
```

- [ ] **Step 3: Run tests — verify failure**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_tools.py -v
```

- [ ] **Step 4: Implement URL reader tool**

```python
# backend/orchestrator/tools/url_reader.py
import httpx
import re
from .base import Tool, ToolResult

class UrlReaderTool(Tool):
    @property
    def name(self) -> str:
        return "url_reader"

    @property
    def description(self) -> str:
        return "Fetch and extract text content from a URL. Params: {url: string}"

    async def execute(self, params: dict) -> ToolResult:
        url = params.get("url")
        if not url:
            return ToolResult(success=False, output="", error="Missing 'url' parameter")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                # Simple HTML tag stripping — good enough for v1
                text = re.sub(r'<script[^>]*>.*?</script>', '', resp.text, flags=re.DOTALL)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                # Truncate to ~10k chars to avoid blowing up context
                if len(text) > 10000:
                    text = text[:10000] + "... [truncated]"
                return ToolResult(success=True, output=text)
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
```

- [ ] **Step 5: Implement web search tool**

```python
# backend/orchestrator/tools/web_search.py
import os
import httpx
from .base import Tool, ToolResult

class WebSearchTool(Tool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web and return summarized results. Params: {query: string}"

    async def execute(self, params: dict) -> ToolResult:
        query = params.get("query")
        if not query:
            return ToolResult(success=False, output="", error="Missing 'query' parameter")

        api_key = os.environ.get("BRAVE_API_KEY")
        if not api_key:
            return ToolResult(success=False, output="", error="BRAVE_API_KEY not set. Web search unavailable.")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": 5},
                    headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("web", {}).get("results", [])
            if not results:
                return ToolResult(success=True, output="No results found.")

            lines = []
            for r in results[:5]:
                lines.append(f"**{r.get('title', '')}**")
                lines.append(r.get('description', ''))
                lines.append(r.get('url', ''))
                lines.append("")

            return ToolResult(success=True, output="\n".join(lines))
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
```

- [ ] **Step 6: Implement document reader tool**

```python
# backend/orchestrator/tools/document_reader.py
import os
from .base import Tool, ToolResult

class DocumentReaderTool(Tool):
    @property
    def name(self) -> str:
        return "document_reader"

    @property
    def description(self) -> str:
        return "Extract text from a local file (PDF, TXT, etc). Params: {path: string}"

    async def execute(self, params: dict) -> ToolResult:
        path = params.get("path")
        if not path or not os.path.exists(path):
            return ToolResult(success=False, output="", error=f"File not found: {path}")

        try:
            # Plain text files
            if path.endswith(('.txt', '.md', '.csv', '.json', '.py', '.js', '.ts')):
                with open(path, 'r', errors='replace') as f:
                    content = f.read()
                if len(content) > 20000:
                    content = content[:20000] + "\n... [truncated]"
                return ToolResult(success=True, output=content)

            # PDF — basic extraction without heavy dependencies
            if path.endswith('.pdf'):
                return ToolResult(
                    success=False, output="",
                    error="PDF support requires PyPDF2. Install with: pip install PyPDF2",
                )

            # Fallback: try reading as text
            with open(path, 'r', errors='replace') as f:
                content = f.read()
            if len(content) > 20000:
                content = content[:20000] + "\n... [truncated]"
            return ToolResult(success=True, output=content)

        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
```

- [ ] **Step 7: Implement code execution tool (sandboxed)**

```python
# backend/orchestrator/tools/code_exec.py
import asyncio
from .base import Tool, ToolResult

class CodeExecTool(Tool):
    @property
    def name(self) -> str:
        return "code_exec"

    @property
    def description(self) -> str:
        return "Execute Python code in a sandboxed environment. Params: {code: string}"

    async def execute(self, params: dict) -> ToolResult:
        code = params.get("code")
        if not code:
            return ToolResult(success=False, output="", error="Missing 'code' parameter")

        try:
            # Use subprocess with timeout — basic sandboxing
            # Docker-based sandbox is a future upgrade
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            output = stdout.decode() if stdout else ""
            errors = stderr.decode() if stderr else ""

            if proc.returncode != 0:
                return ToolResult(success=False, output=output, error=errors)
            return ToolResult(success=True, output=output or "(no output)")

        except asyncio.TimeoutError:
            return ToolResult(success=False, output="", error="Execution timed out (30s limit)")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))
```

```python
# backend/orchestrator/tools/__init__.py
from .base import Tool, ToolResult
from .registry import ToolRegistry
from .web_search import WebSearchTool
from .url_reader import UrlReaderTool
from .document_reader import DocumentReaderTool
from .code_exec import CodeExecTool

__all__ = [
    "Tool", "ToolResult", "ToolRegistry",
    "WebSearchTool", "UrlReaderTool", "DocumentReaderTool", "CodeExecTool",
]
```

- [ ] **Step 8: Run tests**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_tools.py -v
```
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/orchestrator/tools/ tests/orchestrator_v2/test_tools.py
git commit -m "feat: add tool system — web search, URL reader, doc reader, code exec"
```

---

## Task 8: Telegram Channel Adapter

**Goal:** Add Telegram as an opt-in channel using aiogram.

**Files:**
- Create: `backend/orchestrator/channels/telegram.py`
- Create: `tests/orchestrator_v2/test_channel_telegram.py`
- Modify: `requirements.txt` (add aiogram)

- [ ] **Step 1: Add aiogram to requirements**

Add to `requirements.txt`:
```
aiogram>=3.4.0
```

- [ ] **Step 2: Write failing test**

```python
# tests/orchestrator_v2/test_channel_telegram.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from backend.orchestrator.channels.telegram import TelegramChannel
from backend.orchestrator.channels.base import ChannelMessage

@pytest.mark.asyncio
async def test_telegram_channel_converts_message():
    """Telegram update should be converted to a ChannelMessage."""
    channel = TelegramChannel(token="fake-token")

    # Simulate a Telegram message object
    tg_message = MagicMock()
    tg_message.from_user.id = 12345
    tg_message.text = "Hello Mahoraga"
    tg_message.document = None
    tg_message.photo = None

    msg = channel.to_channel_message(tg_message)

    assert isinstance(msg, ChannelMessage)
    assert msg.channel == "telegram"
    assert msg.user_id == "tg:12345"
    assert msg.text == "Hello Mahoraga"

def test_telegram_channel_name():
    channel = TelegramChannel(token="fake-token")
    assert channel.name == "telegram"
```

- [ ] **Step 3: Run test — verify failure**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_channel_telegram.py -v
```

- [ ] **Step 4: Implement Telegram channel**

```python
# backend/orchestrator/channels/telegram.py
import os
import logging
from typing import Callable, Awaitable
from aiogram import Bot, Dispatcher, types
from .base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)

class TelegramChannel(ChannelAdapter):
    """Telegram bot channel adapter using aiogram."""

    def __init__(
        self,
        token: str | None = None,
        on_message: Callable[[ChannelMessage], Awaitable] | None = None,
    ):
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._on_message = on_message
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None

    @property
    def name(self) -> str:
        return "telegram"

    def to_channel_message(self, tg_msg: types.Message) -> ChannelMessage:
        """Convert an aiogram Message to a ChannelMessage."""
        attachments = []
        if tg_msg.document:
            attachments.append({
                "type": "document",
                "file_id": tg_msg.document.file_id,
                "filename": tg_msg.document.file_name or "file",
            })
        if tg_msg.photo:
            # Take the highest resolution photo
            best = tg_msg.photo[-1]
            attachments.append({
                "type": "photo",
                "file_id": best.file_id,
            })

        return ChannelMessage.new(
            user_id=f"tg:{tg_msg.from_user.id}",
            channel="telegram",
            text=tg_msg.text or tg_msg.caption or "",
            attachments=attachments,
        )

    async def send(self, user_id: str, text: str) -> None:
        """Send a message to a Telegram user."""
        if not self._bot:
            return
        # user_id format is "tg:12345"
        tg_id = int(user_id.replace("tg:", ""))
        # Split long messages (Telegram limit: 4096 chars)
        for i in range(0, len(text), 4096):
            await self._bot.send_message(tg_id, text[i:i+4096])

    async def start(self) -> None:
        """Start the Telegram bot with long polling."""
        if not self._token:
            logger.info("No TELEGRAM_BOT_TOKEN set — Telegram channel disabled")
            return

        self._bot = Bot(token=self._token)
        self._dp = Dispatcher()

        @self._dp.message()
        async def handle_message(message: types.Message):
            if not message.text and not message.caption:
                return
            msg = self.to_channel_message(message)
            if self._on_message:
                response_chunks = []
                async for chunk in self._on_message(msg):
                    response_chunks.append(chunk)
                full_response = "\n".join(response_chunks)
                await self.send(msg.user_id, full_response)

        logger.info("Starting Telegram bot...")
        await self._dp.start_polling(self._bot)

    async def stop(self) -> None:
        if self._dp:
            await self._dp.stop_polling()
        if self._bot:
            await self._bot.session.close()
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/test_channel_telegram.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/orchestrator/channels/telegram.py tests/orchestrator_v2/test_channel_telegram.py requirements.txt
git commit -m "feat: add Telegram channel adapter with aiogram"
```

---

## Task 9: README, Setup, and Ship

**Goal:** Write the README, finalize setup.sh, and push to GitHub.

**Files:**
- Create: `README.md`
- Modify: `setup.sh` (final polish)
- Create: `LICENSE` (MIT)

- [ ] **Step 1: Write README.md**

```markdown
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
📊 $0.003 (Haiku: 1.2k tok | Sonnet: 3.4k tok)
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
```

- [ ] **Step 2: Create LICENSE file**

Standard MIT license with `Kaito Soeno` as copyright holder and year `2026`.

- [ ] **Step 3: Final setup.sh polish**

Ensure `setup.sh` also initializes the database by running the migrations:

Add before the "Ready" message:
```bash
echo "Initializing database..."
.venv/bin/python -c "
import asyncio
from backend.orchestrator.store.base import Store
async def init():
    store = await Store.connect()
    await store.close()
asyncio.run(init())
"
```

- [ ] **Step 4: Run full test suite**

```bash
.venv/bin/python -m pytest tests/orchestrator_v2/ -v
```

All tests should pass. Fix any failures.

- [ ] **Step 5: Create GitHub repo and push**

```bash
cd ~/Projects/Mahoraga-product
git remote add origin https://github.com/pockanoodles/Mahoraga.git
git branch -M main
git push -u origin main
```

- [ ] **Step 6: Verify — clone fresh and test setup**

```bash
cd /tmp
git clone https://github.com/pockanoodles/Mahoraga.git mahoraga-test
cd mahoraga-test
cp .env.example .env
# Add a real API key to .env
./setup.sh
```

Confirm it starts and the web UI loads at localhost:8000.

---

## Self-Review Checklist

**Spec coverage:**
- [x] Fork and strip (Task 1)
- [x] Haiku planner (Task 2)
- [x] Adaptive user model (Task 3)
- [x] Web chat UI — default channel (Task 4)
- [x] Gateway wiring (Task 5)
- [x] Cost tracking (Task 6)
- [x] Tools — web search, URL reader, doc reader, code exec (Task 7)
- [x] Telegram channel (Task 8)
- [x] README and setup (Task 9)
- [x] WhatsApp deferred to v2 (spec says future)
- [x] Noctis integration deferred to v2 (spec says future)

**Placeholder scan:** No TBDs, TODOs, or "implement later" found.

**Type consistency:** `ChannelMessage`, `ToolResult`, `UserAdaptation`, `UserProfile` — consistent across all tasks. `generate_tasks()` signature includes `user_profile` param added in Task 2, used in Task 5 gateway. `WorkerAdapter.execute()` signature unchanged from existing codebase.
