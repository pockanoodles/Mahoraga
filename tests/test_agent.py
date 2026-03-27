import json
import pytest
from pathlib import Path
from unittest.mock import patch
from backend.agent import run_agent, _extract_tool_call


# --- Helpers to build fake Ollama NDJSON stream lines ---

def _line(content="", tool_calls=None, done=False):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return json.dumps({"message": msg, "done": done})


def make_lines(*msgs, terminal_done=True):
    lines = list(msgs)
    if terminal_done:
        lines.append(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}))
    return lines


# --- Fake async HTTP context manager ---

class FakeStream:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeClient:
    def __init__(self, responses):
        # responses is a list of line-lists, one per call
        self._responses = iter(responses)

    def stream(self, *args, **kwargs):
        return FakeStream(next(self._responses))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# --- Tests ---

@pytest.mark.asyncio
async def test_agent_streams_tokens(tmp_path):
    ws = str(tmp_path)
    responses = [
        make_lines(
            _line("Hello"),
            _line(" world"),
        )
    ]
    with patch("backend.agent._client", FakeClient(responses)):
        events = [e async for e in run_agent("qwen2.5-coder:7b", [{"role": "user", "content": "hi"}], ws)]

    tokens = [e for e in events if e["type"] == "token"]
    assert [t["content"] for t in tokens] == ["Hello", " world"]
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_agent_executes_tool_and_continues(tmp_path):
    ws = str(tmp_path)
    Path(ws, "note.txt").write_text("secret content")

    tool_call_response = make_lines(
        _line(tool_calls=[{"function": {"name": "read_file", "arguments": {"path": "note.txt"}}}]),
    )
    final_response = make_lines(
        _line("The file says: secret content"),
    )

    with patch("backend.agent._client", FakeClient([tool_call_response, final_response])):
        events = [e async for e in run_agent("qwen2.5-coder:7b", [{"role": "user", "content": "read note.txt"}], ws)]

    tool_events = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_events) == 1
    assert tool_events[0]["tool"] == "read_file"
    assert tool_events[0]["path"] == "note.txt"

    token_events = [e for e in events if e["type"] == "token"]
    assert any("secret content" in t["content"] for t in token_events)


@pytest.mark.asyncio
async def test_agent_respects_max_iterations(tmp_path):
    ws = str(tmp_path)
    # Every response is a tool call — agent should stop after max_iterations
    one_tool_call = make_lines(
        _line(tool_calls=[{"function": {"name": "list_dir", "arguments": {"path": "."}}}]),
    )

    with patch("backend.agent._client", FakeClient([one_tool_call] * 5)):
        events = [e async for e in run_agent("qwen2.5-coder:7b", [{"role": "user", "content": "loop"}], ws, max_iterations=3)]

    # Must terminate with done
    assert events[-1]["type"] == "done"
    tool_events = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_events) <= 3


def test_extract_tool_call_finds_json_in_text():
    content = 'Let me read the file. {"name": "read_file", "arguments": {"path": "main.py"}}'
    result = _extract_tool_call(content)
    assert result is not None
    assert result["function"]["name"] == "read_file"
    assert result["function"]["arguments"] == {"path": "main.py"}

def test_extract_tool_call_ignores_unknown_tools():
    content = '{"name": "delete_everything", "arguments": {}}'
    result = _extract_tool_call(content)
    assert result is None

def test_extract_tool_call_returns_none_on_plain_text():
    result = _extract_tool_call("No tool call here, just text.")
    assert result is None

def test_extract_tool_call_handles_malformed_json():
    result = _extract_tool_call("{broken json{{{")
    assert result is None
