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
