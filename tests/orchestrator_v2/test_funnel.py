"""Tests for the delegation funnel — recorder (scripts/) and reader (routing/).

The funnel is the only measurement whose *denominator* is the point, so these
tests are mostly about what gets counted and what does not. Two properties
matter more than the arithmetic:

  - the recorder can never break a Claude Code session, whatever it is handed;
  - the reported rate errs LOW. It exists to argue the tool is underused, so an
    over-stated rate would quietly retire a problem that is still there.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.orchestrator.routing.funnel_report import (
    compute_funnel,
    install_hint,
    render_funnel,
)

_HOOK_PATH = Path(__file__).resolve().parents[2] / "scripts" / "claude_code_funnel_hook.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("funnel_hook", _HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load_hook()


def _write_payload(tool="Write", path="a/b.py", content="x = 1\n" * 40, **extra):
    return {
        "tool_name": tool,
        "session_id": "sess-1",
        "cwd": "/repo",
        "tool_input": {"file_path": path, "content": content, **extra},
    }


# ── the recorder: classification ─────────────────────────────────────────────


def test_new_code_file_of_workable_size_is_a_candidate():
    rec = hook.build_record(_write_payload())
    assert rec["event"] == "inline"
    assert rec["candidate"] is True
    assert rec["ext"] == ".py"


def test_edit_in_place_is_not_delegable():
    """The arms get a prompt and no repo, so a surgical edit is not work the
    cascade could have taken. Counting it would flatter the denominator."""
    rec = hook.build_record({
        "tool_name": "Edit", "session_id": "s", "cwd": "/repo",
        "tool_input": {"file_path": "a/b.py", "old_string": "x", "new_string": "y"},
    })
    assert rec["candidate"] is False
    assert rec["reason"] == "edit-in-place"


def test_non_code_files_are_excluded():
    rec = hook.build_record(_write_payload(path="docs/notes.md"))
    assert rec["candidate"] is False
    assert rec["reason"] == "non-code-file"


def test_tiny_writes_are_excluded():
    """Below the round-trip threshold, delegating loses to just writing it —
    counting these would manufacture a gap that should not be closed."""
    rec = hook.build_record(_write_payload(content="x = 1\n"))
    assert rec["candidate"] is False
    assert rec["reason"] == "below-round-trip-threshold"


def test_oversized_writes_are_excluded():
    rec = hook.build_record(_write_payload(content="x = 1\n" * 500))
    assert rec["candidate"] is False
    assert rec["reason"] == "oversized-for-local-arm"


def test_run_task_is_recorded_as_a_delegation():
    rec = hook.build_record({
        "tool_name": "mcp__mahoraga__run_task", "session_id": "s", "cwd": "/repo",
        "tool_input": {"prompt": "write chunk(lst, n)"},
    })
    assert rec["event"] == "delegated"


def test_unrelated_tools_are_ignored():
    assert hook.build_record({"tool_name": "Bash", "tool_input": {}}) is None


def test_no_file_contents_are_logged():
    """The log lives outside the repo but still must not carry source."""
    secret = "SECRET_TOKEN_VALUE\n" * 40
    rec = hook.build_record(_write_payload(content=secret))
    assert "SECRET_TOKEN_VALUE" not in json.dumps(rec)
    assert rec["lines"] == 41 and rec["chars"] == len(secret)


# ── the recorder: it must never break a session ──────────────────────────────


@pytest.mark.parametrize("payload", ['{"tool_name": "Write"}', "not json", "", "[]", "null"])
def test_hook_exits_zero_on_any_input(payload, tmp_path, monkeypatch):
    proc = subprocess.run(
        [sys.executable, str(_HOOK_PATH)],
        input=payload, capture_output=True, text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr


def test_hook_appends_a_line_to_the_log(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(_HOOK_PATH)],
        input=json.dumps(_write_payload()), capture_output=True, text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0
    log = tmp_path / ".mahoraga-v2" / "funnel.jsonl"
    assert log.is_file()
    row = json.loads(log.read_text().strip())
    assert row["event"] == "inline" and row["candidate"] is True


# ── the reader ───────────────────────────────────────────────────────────────


def _log(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "funnel.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def _inline(candidate=True, reason="", ts="2026-08-12T10:00:00+00:00", session="s"):
    return {"ts": ts, "session": session, "event": "inline",
            "candidate": candidate, "reason": reason}


def _delegated(ts="2026-08-12T10:00:00+00:00", session="s"):
    return {"ts": ts, "session": session, "event": "delegated", "candidate": True}


def test_rate_is_delegated_over_delegable(tmp_path):
    log = _log(tmp_path, [_delegated(), _inline(), _inline(), _inline()])
    r = compute_funnel(log)
    assert (r.delegated, r.candidates, r.delegable) == (1, 3, 4)
    assert r.delegation_rate == pytest.approx(0.25)


def test_excluded_actions_stay_out_of_the_denominator(tmp_path):
    """Otherwise every markdown file makes delegation look worse than it is."""
    log = _log(tmp_path, [
        _delegated(),
        _inline(candidate=False, reason="non-code-file"),
        _inline(candidate=False, reason="edit-in-place"),
    ])
    r = compute_funnel(log)
    assert r.delegable == 1
    assert r.delegation_rate == pytest.approx(1.0)
    assert r.excluded_by_reason == {"non-code-file": 1, "edit-in-place": 1}
    assert r.inline_total == 2


def test_no_delegable_work_is_unknown_not_zero(tmp_path):
    """0% would read as a finding; the truth is there is no data."""
    log = _log(tmp_path, [_inline(candidate=False, reason="non-code-file")])
    r = compute_funnel(log)
    assert r.delegation_rate is None
    assert "unknown" in render_funnel(r)


def test_missing_log_is_an_empty_report_not_an_error(tmp_path):
    r = compute_funnel(tmp_path / "nope.jsonl")
    assert r.delegable == 0
    assert "No funnel traffic recorded yet" in render_funnel(r)


def test_torn_line_does_not_discard_the_rest(tmp_path):
    p = tmp_path / "funnel.jsonl"
    p.write_text(json.dumps(_delegated()) + "\n{ broken\n" + json.dumps(_inline()) + "\n")
    r = compute_funnel(p)
    assert r.delegable == 2


def test_window_filters_by_date(tmp_path):
    log = _log(tmp_path, [
        _delegated(ts="2026-08-01T10:00:00+00:00"),
        _delegated(ts="2026-08-10T10:00:00+00:00"),
        _delegated(ts="2026-08-20T10:00:00+00:00"),
    ])
    assert compute_funnel(log, since="2026-08-05").delegated == 2
    assert compute_funnel(log, until="2026-08-10").delegated == 2


def test_sessions_are_counted_distinctly(tmp_path):
    log = _log(tmp_path, [_delegated(session="a"), _inline(session="a"),
                          _inline(session="b")])
    assert compute_funnel(log).sessions == 2


def test_render_states_the_lower_bound(tmp_path):
    """The caveat must travel with the number, not sit beside it in a doc."""
    text = render_funnel(compute_funnel(_log(tmp_path, [_delegated(), _inline()])))
    assert "LOWER bound" in text


def test_install_hint_points_at_the_shipped_recorder():
    text = install_hint(_HOOK_PATH)
    assert "PostToolUse" in text
    assert "claude_code_funnel_hook.py" in text
    assert '"async": true' in text


def test_shipped_hook_is_executable_and_imports_nothing_heavy():
    """Constraint 2: it runs on every Write; loading the orchestrator would put
    ~500 ms of import in front of the user's own tool call."""
    source = _HOOK_PATH.read_text()
    assert "from backend" not in source and "import backend" not in source
