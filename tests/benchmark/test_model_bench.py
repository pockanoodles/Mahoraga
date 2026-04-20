from unittest.mock import MagicMock, patch

import httpx

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
