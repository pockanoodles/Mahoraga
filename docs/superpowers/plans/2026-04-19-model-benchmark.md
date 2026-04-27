# Model Benchmark Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone CLI benchmark that measures real Ollama model throughput (t/s) and per-difficulty task times across 4 role-stratified prompt sets, appending results to `brain/benchmarks/hardware_log.md`.

**Architecture:** Two-file module (`benchmark/prompts.py` for data, `benchmark/model_bench.py` for runner logic). Runner discovers models via Ollama's `/api/tags`, calls `/api/generate` with `stream: false` to get exact `eval_count`/`eval_duration` fields, averages results per tier, and appends a dated markdown table section to the log.

**Tech Stack:** Python 3.12, httpx (already in requirements), pytest, argparse

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `benchmark/__init__.py` | Create | Package marker |
| `benchmark/prompts.py` | Create | All 24 fixed prompts, keyed by role + tier |
| `benchmark/model_bench.py` | Create | Ollama client, measurement, formatting, CLI |
| `tests/benchmark/__init__.py` | Create | Test package marker |
| `tests/benchmark/test_prompts.py` | Create | Validate prompt set structure |
| `tests/benchmark/test_model_bench.py` | Create | Unit tests with mocked httpx |
| `pyproject.toml` | Modify | Add `benchmark*` to packages.find include |
| `pytest.ini` | Modify | Add `pythonpath = .` so tests resolve `benchmark.*` |

---

## Task 1: Package scaffolding + prompt data

**Files:**
- Create: `benchmark/__init__.py`
- Create: `benchmark/prompts.py`
- Create: `tests/benchmark/__init__.py`
- Modify: `pyproject.toml`
- Modify: `pytest.ini`

- [ ] **Step 1: Create package markers**

```bash
mkdir -p benchmark tests/benchmark
touch benchmark/__init__.py tests/benchmark/__init__.py
```

- [ ] **Step 2: Add `benchmark*` to pyproject.toml**

In `pyproject.toml`, change:
```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["backend*"]
```
to:
```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["backend*", "benchmark*"]
```

- [ ] **Step 3: Add pythonpath to pytest.ini**

In `pytest.ini`, add `pythonpath = .` after the `[pytest]` header:
```ini
[pytest]
pythonpath = .
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
filterwarnings =
    ignore::DeprecationWarning:pytest_asyncio
```

- [ ] **Step 4: Reinstall package in editable mode**

```bash
pip install -e . --quiet
```

Expected: no errors, `benchmark` package importable.

- [ ] **Step 5: Write `benchmark/prompts.py`**

```python
PROMPT_SETS: dict[str, dict[str, list[str]]] = {
    "builder": {
        "easy": [
            "Write a Python function that reverses a string without using slicing.",
            "Write a bash one-liner to find all `.py` files modified in the last 7 days.",
        ],
        "medium": [
            "Implement a Python LRU cache class using only a dict and a doubly linked list. Include get and put methods.",
            "Write a FastAPI endpoint that accepts a JSON body with `user_id` and `amount`, validates both fields, and returns a receipt object.",
        ],
        "hard": [
            "Implement a thread-safe Python task queue with a worker pool. Workers pull tasks, execute them, and report results. Include shutdown logic.",
            (
                "Refactor this code to be async using aiohttp:\n"
                "```python\nimport requests, time\n"
                "def poll(url, retries=5):\n"
                "    for i in range(retries):\n"
                "        r = requests.get(url)\n"
                "        if r.ok: return r.json()\n"
                "        time.sleep(2**i)\n"
                "    raise RuntimeError('failed')\n```"
            ),
        ],
    },
    "security": {
        "easy": [
            "List the top 5 OWASP vulnerabilities and give one-line mitigations for each.",
            "What is the difference between authentication and authorization? Give a concrete example of each being bypassed.",
        ],
        "medium": [
            "Review this Python code for security vulnerabilities and explain each one:\n```python\nquery = f'SELECT * FROM users WHERE id = {user_input}'\n```",
            "Explain how a timing attack works against a password comparison function, then write a constant-time comparison in Python.",
        ],
        "hard": [
            "Design a threat model for a FastAPI service that handles JWT auth, user file uploads, and third-party OAuth. List assets, threats, and mitigations per STRIDE category.",
            "Write a Python script that scans a directory of Python files and flags: hardcoded secrets, SQL string formatting, and shell injection risks. Output structured findings.",
        ],
    },
    "research": {
        "easy": [
            "Summarize the key differences between RAG and fine-tuning for LLM adaptation in 3 bullet points.",
            "What is the transformer attention mechanism? Explain it as if to a software engineer who has never read a paper.",
        ],
        "medium": [
            "Compare LinUCB and Thompson Sampling for contextual bandits: when does each outperform the other, and why?",
            (
                "Given this abstract: 'We propose a reward shaping method that adds potential-based auxiliary "
                "rewards derived from a learned value function. Experiments on sparse-reward MuJoCo tasks show "
                "40% faster convergence vs. baseline PPO, with no reduction in final policy quality. Limitations "
                "include sensitivity to the quality of the learned potential and additional compute overhead.' "
                "— What are the core claims, limitations, and open questions?"
            ),
        ],
        "hard": [
            "Synthesize: what are the main failure modes of multi-agent LLM systems in production? Cover coordination, trust, cost, and quality. Cite reasoning, not sources.",
            "A user reports that their bandit router converges too quickly to one agent and stops exploring. Walk through possible causes, diagnostic steps, and fixes.",
        ],
    },
    "general": {
        "easy": [
            (
                "Parse this markdown task list and return only incomplete items as a Python list:\n"
                "- [x] Set up repo\n- [ ] Write tests\n- [x] Deploy to staging\n"
                "- [ ] Update docs\n- [x] Code review\n- [ ] Fix linting\n"
                "- [x] Merge PR\n- [ ] Notify team"
            ),
            (
                "You need to store user sessions. Option A: in-memory dict (fast, lost on restart). "
                "Option B: Redis (fast, persistent, extra infra). Option C: SQLite (slow, persistent, no infra). "
                "Your app restarts daily and has 500 concurrent users. Pick the best option and explain why."
            ),
        ],
        "medium": [
            (
                "Extract all rules that affect how code should be written from this CLAUDE.md and format them as a checklist:\n"
                "## Efficiency Rules\n- Read targeted — use offset/limit on large files.\n"
                "- Subagents for research.\n- Tight globs — never **/*.\n"
                "## Code Style\n- No comments unless WHY is non-obvious.\n"
                "- No error handling for impossible scenarios.\n"
                "- Default to no abstractions beyond what the task requires.\n"
                "- Don't add features beyond what was asked.\n"
                "## Testing\n- Run pytest from project root.\n"
                "- Don't mock the database in integration tests."
            ),
            "A service is slow. You have CPU at 20%, memory at 80%, and p99 latency spiking every 5 minutes. What are the most likely causes? How would you investigate each?",
        ],
        "hard": [
            "Write a project plan for migrating a monolithic FastAPI app to a microservices architecture. Include phases, risks, and rollback strategy. Output as structured markdown.",
            (
                "Read the following system design requirements and identify ambiguities, unstated assumptions, "
                "and missing constraints: 'Build a service that lets users upload files and share them with others. "
                "Files should be processed quickly. The system must be secure and handle many users. Admins can "
                "delete any file. Users should get notified when their file is ready. The service should not go down.'"
            ),
        ],
    },
}

ROLES: list[str] = list(PROMPT_SETS.keys())
TIERS: list[str] = ["easy", "medium", "hard"]
```

- [ ] **Step 6: Write the failing prompt structure test**

Create `tests/benchmark/test_prompts.py`:

```python
from benchmark.prompts import PROMPT_SETS, ROLES, TIERS


def test_all_roles_present():
    assert set(ROLES) == {"builder", "security", "research", "general"}


def test_all_tiers_present():
    for role in ROLES:
        assert set(PROMPT_SETS[role].keys()) == set(TIERS), f"{role} missing tiers"


def test_two_prompts_per_tier():
    for role in ROLES:
        for tier in TIERS:
            prompts = PROMPT_SETS[role][tier]
            assert len(prompts) == 2, f"{role}/{tier} should have 2 prompts, got {len(prompts)}"


def test_all_prompts_are_nonempty_strings():
    for role in ROLES:
        for tier in TIERS:
            for i, p in enumerate(PROMPT_SETS[role][tier]):
                assert isinstance(p, str) and len(p) > 20, f"{role}/{tier}[{i}] is too short or not a string"
```

- [ ] **Step 7: Run test to verify it passes**

```bash
pytest tests/benchmark/test_prompts.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add benchmark/__init__.py benchmark/prompts.py tests/benchmark/__init__.py tests/benchmark/test_prompts.py pyproject.toml pytest.ini
git commit -m "feat: benchmark prompt sets for builder/security/research/general roles"
```

---

## Task 2: Ollama client — model discovery and prompt execution

**Files:**
- Create: `benchmark/model_bench.py` (initial version — client functions only)
- Modify: `tests/benchmark/test_model_bench.py`

- [ ] **Step 1: Write failing tests for `discover_models` and `run_prompt`**

Create `tests/benchmark/test_model_bench.py`:

```python
from unittest.mock import MagicMock, patch

import httpx
import pytest

from benchmark.model_bench import discover_models, run_prompt


def _mock_tags_response():
    mock = MagicMock(spec=httpx.Response)
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "models": [{"name": "qwen3:4b"}, {"name": "qwen3:8b"}]
    }
    return mock


def _mock_generate_response(eval_count=150, eval_duration_ns=6_000_000_000, total_duration_ns=7_000_000_000):
    mock = MagicMock(spec=httpx.Response)
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "response": "some text",
        "eval_count": eval_count,
        "eval_duration": eval_duration_ns,
        "total_duration": total_duration_ns,
    }
    return mock


def test_discover_models_returns_names():
    with patch("httpx.get", return_value=_mock_tags_response()) as mock_get:
        models = discover_models()
    assert models == ["qwen3:4b", "qwen3:8b"]
    mock_get.assert_called_once_with("http://localhost:11434/api/tags", timeout=10)


def test_discover_models_empty():
    mock = MagicMock(spec=httpx.Response)
    mock.raise_for_status.return_value = None
    mock.json.return_value = {"models": []}
    with patch("httpx.get", return_value=mock):
        assert discover_models() == []


def test_run_prompt_returns_tps_and_duration():
    with patch("httpx.post", return_value=_mock_generate_response()):
        result = run_prompt("qwen3:4b", "hello")
    assert result is not None
    # eval_count=150, eval_duration=6s → 25 t/s
    assert abs(result["tps"] - 25.0) < 0.1
    # total_duration=7s
    assert abs(result["duration_s"] - 7.0) < 0.1


def test_run_prompt_returns_none_on_timeout():
    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        result = run_prompt("qwen3:4b", "hello")
    assert result is None


def test_run_prompt_returns_none_on_http_error():
    with patch("httpx.post", side_effect=httpx.HTTPError("bad")):
        result = run_prompt("qwen3:4b", "hello")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/benchmark/test_model_bench.py -v
```

Expected: ImportError or AttributeError — `model_bench` does not exist yet.

- [ ] **Step 3: Create `benchmark/model_bench.py` with client functions**

```python
import argparse
import datetime
import sys
from pathlib import Path
from typing import Optional

import httpx

from benchmark.prompts import PROMPT_SETS, ROLES, TIERS

OLLAMA_BASE = "http://localhost:11434"
LOG_PATH = Path(__file__).parent.parent / "brain" / "benchmarks" / "hardware_log.md"
PROMPT_TIMEOUT = 120.0


def discover_models() -> list[str]:
    resp = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=10)
    resp.raise_for_status()
    return [m["name"] for m in resp.json()["models"]]


def run_prompt(model: str, prompt: str, timeout: float = PROMPT_TIMEOUT) -> Optional[dict]:
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        eval_count = data.get("eval_count", 0)
        eval_duration_ns = data.get("eval_duration", 0)
        total_duration_ns = data.get("total_duration", eval_duration_ns)
        tps = eval_count / (eval_duration_ns / 1e9) if eval_duration_ns > 0 else 0.0
        return {"tps": tps, "duration_s": total_duration_ns / 1e9}
    except (httpx.TimeoutException, httpx.HTTPError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/benchmark/test_model_bench.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmark/model_bench.py tests/benchmark/test_model_bench.py
git commit -m "feat: ollama client — model discovery and prompt execution"
```

---

## Task 3: Aggregation — bench_role

**Files:**
- Modify: `benchmark/model_bench.py` (add `bench_role`)
- Modify: `tests/benchmark/test_model_bench.py`

- [ ] **Step 1: Write failing tests for `bench_role`**

Append to `tests/benchmark/test_model_bench.py`:

```python
from benchmark.model_bench import bench_role


def test_bench_role_averages_tiers():
    call_count = 0

    def fake_run_prompt(model, prompt, timeout=120.0):
        nonlocal call_count
        call_count += 1
        # Return increasing durations so easy < medium < hard
        return {"tps": 20.0, "duration_s": float(call_count * 10)}

    with patch("benchmark.model_bench.run_prompt", side_effect=fake_run_prompt):
        result = bench_role("qwen3:4b", "builder")

    # 2 prompts per tier × 3 tiers = 6 calls
    assert call_count == 6
    # easy prompts: calls 1 (10s) and 2 (20s) → avg 15s
    assert result["easy"] == 15.0
    # medium prompts: calls 3 (30s) and 4 (40s) → avg 35.0
    assert result["medium"] == 35.0
    # hard prompts: calls 5 (50s) and 6 (60s) → avg 55.0
    assert result["hard"] == 55.0
    # tps: all 20.0 → avg 20.0
    assert result["tps"] == 20.0


def test_bench_role_handles_none_results():
    with patch("benchmark.model_bench.run_prompt", return_value=None):
        result = bench_role("qwen3:4b", "builder")

    assert result["easy"] is None
    assert result["medium"] is None
    assert result["hard"] is None
    assert result["tps"] is None


def test_bench_role_partial_failure():
    responses = [{"tps": 10.0, "duration_s": 5.0}, None]

    with patch("benchmark.model_bench.run_prompt", side_effect=responses * 3):
        result = bench_role("qwen3:4b", "builder")

    # Only one successful result per tier → avg of one = that value
    assert result["easy"] == 5.0
    assert result["tps"] == 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/benchmark/test_model_bench.py::test_bench_role_averages_tiers -v
```

Expected: FAIL with AttributeError — `bench_role` not defined.

- [ ] **Step 3: Add `bench_role` to `benchmark/model_bench.py`**

Add after `run_prompt`:

```python
def bench_role(model: str, role: str) -> dict:
    prompts = PROMPT_SETS[role]
    tier_times: dict[str, list[float]] = {t: [] for t in TIERS}
    all_tps: list[float] = []

    for tier in TIERS:
        for prompt in prompts[tier]:
            result = run_prompt(model, prompt)
            if result is not None:
                tier_times[tier].append(result["duration_s"])
                all_tps.append(result["tps"])

    return {
        **{
            tier: round(sum(times) / len(times), 1) if times else None
            for tier, times in tier_times.items()
        },
        "tps": round(sum(all_tps) / len(all_tps), 1) if all_tps else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/benchmark/test_model_bench.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmark/model_bench.py tests/benchmark/test_model_bench.py
git commit -m "feat: bench_role aggregates tier averages and mean t/s"
```

---

## Task 4: Formatting — tables and run section

**Files:**
- Modify: `benchmark/model_bench.py` (add formatting functions)
- Modify: `tests/benchmark/test_model_bench.py`

- [ ] **Step 1: Write failing tests for formatting functions**

Append to `tests/benchmark/test_model_bench.py`:

```python
import datetime

from benchmark.prompts import ROLES
from benchmark.model_bench import format_table, format_run_section


def test_format_table_renders_values():
    model_results = {
        "qwen3:4b": {"tps": 21.0, "easy": 11.0, "medium": 34.0, "hard": 47.0},
    }
    table = format_table("builder", model_results)
    assert "### Builder" in table
    assert "qwen3:4b" in table
    assert "21 t/s" in table
    assert "11s" in table
    assert "34s" in table
    assert "47s" in table


def test_format_table_renders_dash_for_none():
    model_results = {
        "qwen3:8b": {"tps": None, "easy": None, "medium": None, "hard": None},
    }
    table = format_table("security", model_results)
    assert "—" in table


def test_format_run_section_contains_header_and_hardware():
    roles_data = {
        "builder": {"qwen3:4b": {"tps": 21.0, "easy": 11.0, "medium": 34.0, "hard": 47.0}},
    }
    run_time = datetime.datetime(2026, 4, 19, 14, 32)
    section = format_run_section(roles_data, run_time, ["builder"])
    assert "2026-04-19 14:32" in section
    assert "MacBook Pro" in section
    assert "### Builder" in section
    assert section.endswith("---\n")


def test_format_run_section_full_suite_label():
    roles_data = {r: {} for r in ROLES}
    run_time = datetime.datetime(2026, 4, 19, 14, 32)
    section = format_run_section(roles_data, run_time, list(ROLES))
    assert "Full Suite" in section


def test_format_run_section_partial_label():
    roles_data = {"builder": {}}
    run_time = datetime.datetime(2026, 4, 19, 14, 32)
    section = format_run_section(roles_data, run_time, ["builder"])
    assert "Roles: builder" in section
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/benchmark/test_model_bench.py::test_format_table_renders_values -v
```

Expected: FAIL — `format_table` not defined.

- [ ] **Step 3: Add formatting functions to `benchmark/model_bench.py`**

Add after `bench_role`:

```python
def _fmt_tier(val: Optional[float]) -> str:
    return "—" if val is None else f"{val:.0f}s"


def _fmt_tps(val: Optional[float]) -> str:
    return "—" if val is None else f"{val:.0f} t/s"


def format_table(role: str, model_results: dict[str, dict]) -> str:
    lines = [
        f"### {role.capitalize()}",
        "| Model | Throughput | Easy | Medium | Hard |",
        "|-------|-----------|------|--------|------|",
    ]
    for model, r in model_results.items():
        lines.append(
            f"| {model} | {_fmt_tps(r['tps'])} | {_fmt_tier(r['easy'])} | {_fmt_tier(r['medium'])} | {_fmt_tier(r['hard'])} |"
        )
    return "\n".join(lines) + "\n"


def format_run_section(
    roles_data: dict[str, dict[str, dict]],
    run_time: datetime.datetime,
    roles: list[str],
) -> str:
    suite_label = "Full Suite" if set(roles) == set(ROLES) else f"Roles: {', '.join(roles)}"
    parts = [
        f"## {run_time.strftime('%Y-%m-%d %H:%M')} — {suite_label}",
        "**Hardware:** MacBook Pro M-series, 16 GB unified memory\n",
    ]
    for role in roles:
        parts.append(format_table(role, roles_data[role]))
    parts.append("---\n")
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/benchmark/test_model_bench.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmark/model_bench.py tests/benchmark/test_model_bench.py
git commit -m "feat: format_table and format_run_section for markdown output"
```

---

## Task 5: Log append

**Files:**
- Modify: `benchmark/model_bench.py` (add `append_to_log`)
- Modify: `tests/benchmark/test_model_bench.py`

- [ ] **Step 1: Write failing test for `append_to_log`**

Append to `tests/benchmark/test_model_bench.py`:

```python
import tempfile
from pathlib import Path
from benchmark.model_bench import append_to_log


def test_append_to_log_creates_file_and_appends():
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "sub" / "hardware_log.md"
        append_to_log("## first run\n---\n", path=log_path)
        append_to_log("## second run\n---\n", path=log_path)

        content = log_path.read_text()
        assert "## first run" in content
        assert "## second run" in content
        # second run comes after first
        assert content.index("first") < content.index("second")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/benchmark/test_model_bench.py::test_append_to_log_creates_file_and_appends -v
```

Expected: FAIL — `append_to_log` not defined.

- [ ] **Step 3: Add `append_to_log` to `benchmark/model_bench.py`**

Add after `format_run_section`:

```python
def append_to_log(section: str, path: Path = LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write("\n" + section)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/benchmark/test_model_bench.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmark/model_bench.py tests/benchmark/test_model_bench.py
git commit -m "feat: append_to_log writes dated sections to hardware_log.md"
```

---

## Task 6: CLI — wire it all together

**Files:**
- Modify: `benchmark/model_bench.py` (add `main`)

- [ ] **Step 1: Write failing CLI test**

Append to `tests/benchmark/test_model_bench.py`:

```python
from unittest.mock import call, patch
from benchmark.model_bench import main


def test_main_full_run(capsys):
    fake_result = {"tps": 21.0, "easy": 11.0, "medium": 34.0, "hard": 47.0}

    with (
        patch("benchmark.model_bench.discover_models", return_value=["qwen3:4b"]),
        patch("benchmark.model_bench.bench_role", return_value=fake_result),
        patch("benchmark.model_bench.append_to_log") as mock_log,
    ):
        import sys
        sys.argv = ["model_bench.py"]
        main()

    captured = capsys.readouterr()
    assert "qwen3:4b" in captured.out
    assert mock_log.called


def test_main_specific_models(capsys):
    fake_result = {"tps": 12.0, "easy": 27.0, "medium": 58.0, "hard": None}

    with (
        patch("benchmark.model_bench.discover_models") as mock_disc,
        patch("benchmark.model_bench.bench_role", return_value=fake_result),
        patch("benchmark.model_bench.append_to_log"),
    ):
        import sys
        sys.argv = ["model_bench.py", "qwen3:8b"]
        main()
        mock_disc.assert_not_called()

    captured = capsys.readouterr()
    assert "qwen3:8b" in captured.out


def test_main_single_role(capsys):
    fake_result = {"tps": 21.0, "easy": 11.0, "medium": 34.0, "hard": 47.0}

    with (
        patch("benchmark.model_bench.discover_models", return_value=["qwen3:4b"]),
        patch("benchmark.model_bench.bench_role", return_value=fake_result) as mock_bench,
        patch("benchmark.model_bench.append_to_log"),
    ):
        import sys
        sys.argv = ["model_bench.py", "--role", "builder"]
        main()

    # Only called once — for the single role
    assert mock_bench.call_count == 1
    assert mock_bench.call_args == call("qwen3:4b", "builder")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/benchmark/test_model_bench.py::test_main_full_run -v
```

Expected: FAIL — `main` not defined.

- [ ] **Step 3: Add `main` to `benchmark/model_bench.py`**

Add at the end of the file:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local Ollama models")
    parser.add_argument("models", nargs="*", help="Model names (default: all from ollama list)")
    parser.add_argument("--role", choices=ROLES, help="Run a single role set only")
    args = parser.parse_args()

    if args.models:
        models = args.models
    else:
        try:
            models = discover_models()
        except Exception as e:
            print(f"Error connecting to Ollama: {e}", file=sys.stderr)
            sys.exit(1)

    if not models:
        print("No models found. Pull a model with: ollama pull <model>", file=sys.stderr)
        sys.exit(1)

    roles = [args.role] if args.role else list(ROLES)
    run_time = datetime.datetime.now()

    print(f"Benchmarking {len(models)} model(s) across {len(roles)} role set(s)...")
    print(f"Models: {', '.join(models)}\n")

    roles_data: dict[str, dict[str, dict]] = {role: {} for role in roles}

    for model in models:
        print(f"  [{model}]")
        for role in roles:
            print(f"    {role}... ", end="", flush=True)
            result = bench_role(model, role)
            roles_data[role][model] = result
            tps_str = _fmt_tps(result["tps"])
            print(f"done ({tps_str})")

    section = format_run_section(roles_data, run_time, roles)
    print("\n" + section)
    append_to_log(section)
    print(f"Results appended to {LOG_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/benchmark/ -v
```

Expected: all tests PASS.

- [ ] **Step 5: Smoke test against real Ollama (if running)**

```bash
python benchmark/model_bench.py --role builder
```

Expected: connects to Ollama, prints progress per model, prints markdown table, writes to `brain/benchmarks/hardware_log.md`.

If Ollama is not running: `Error connecting to Ollama: ...` and clean exit.

- [ ] **Step 6: Final commit**

```bash
git add benchmark/model_bench.py tests/benchmark/test_model_bench.py
git commit -m "feat: benchmark CLI — full suite and per-role mode"
```
