"""Tests for worker output postprocessing (extract_code / strip_preamble)."""
from __future__ import annotations

from backend.orchestrator.workers.postprocess import extract_code, strip_preamble


def test_extract_code_complete_fenced_block():
    resp = "Sure:\n```python\ndef f():\n    return 1\n```\nDone."
    assert extract_code(resp) == "def f():\n    return 1"


def test_extract_code_first_of_multiple_blocks():
    resp = "```python\ndef a():\n    return 1\n```\ntext\n```python\ndef b():\n    return 2\n```"
    assert extract_code(resp) == "def a():\n    return 1"


def test_extract_code_no_fence_returns_raw():
    resp = "def f():\n    return 1"
    assert extract_code(resp) == "def f():\n    return 1"


def test_extract_code_unclosed_opening_fence_is_stripped():
    # Truncated output: opening fence present, closing fence never emitted.
    resp = "```python\ndef two_sum(nums, target):\n    return (0, 1)"
    got = extract_code(resp)
    assert got == "def two_sum(nums, target):\n    return (0, 1)"
    assert "```" not in got


def test_extract_code_unclosed_fence_result_is_runnable():
    resp = "```python\ndef sq(x):\n    return x * x"
    ns: dict = {}
    exec(extract_code(resp), ns)  # must be valid Python, not "```python..."
    assert ns["sq"](5) == 25


def test_strip_preamble_removes_lead_in_and_signoff():
    resp = "Sure, here's the answer.\nThe real content.\nHope this helps!"
    assert strip_preamble(resp) == "The real content."
