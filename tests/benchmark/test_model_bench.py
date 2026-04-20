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
