"""Tests for the launchd daemon's environment surface (cli.commands.service).

A launchd job inherits nothing: no user variables, and a PATH of
/usr/bin:/bin:/usr/sbin:/sbin. Before this block existed, running Mahoraga as a
daemon meant every MAHORAGA_* knob was pinned to its code default with no way to
change it, and the escalation cascade's `claude` binary was unresolvable — which
degrades silently, serving local answers with no error.

These tests pin the two properties that make the daemon configurable at all:
the PATH covers where the arms actually live, and service.env reaches the plist.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.orchestrator.cli.commands import service


# ── service.env parsing ──────────────────────────────────────────────────────


def test_parse_env_file_reads_key_values(tmp_path: Path):
    f = tmp_path / "service.env"
    f.write_text("MAHORAGA_REWARD_JUDGE=code\nMAHORAGA_ESCALATE_MAX_PER_DAY=40\n")
    assert service._parse_env_file(f) == {
        "MAHORAGA_REWARD_JUDGE": "code",
        "MAHORAGA_ESCALATE_MAX_PER_DAY": "40",
    }


def test_parse_env_file_ignores_comments_and_blanks(tmp_path: Path):
    f = tmp_path / "service.env"
    f.write_text("# a comment\n\nMAHORAGA_CASCADE=on\n   \n# another\n")
    assert service._parse_env_file(f) == {"MAHORAGA_CASCADE": "on"}


def test_parse_env_file_strips_surrounding_quotes(tmp_path: Path):
    f = tmp_path / "service.env"
    f.write_text('MAHORAGA_ESCALATE_TO="claude-cli"\n')
    assert service._parse_env_file(f) == {"MAHORAGA_ESCALATE_TO": "claude-cli"}


def test_parse_env_file_missing_is_empty(tmp_path: Path):
    assert service._parse_env_file(tmp_path / "nope.env") == {}


def test_parse_env_file_skips_malformed_lines(tmp_path: Path):
    """A junk line must not take the whole daemon config down with it."""
    f = tmp_path / "service.env"
    f.write_text("this line has no equals sign\nMAHORAGA_CASCADE=off\n=novalue\n")
    assert service._parse_env_file(f) == {"MAHORAGA_CASCADE": "off"}


# ── the PATH the daemon actually runs with ───────────────────────────────────


def test_daemon_path_includes_local_bin(monkeypatch, tmp_path):
    """~/.local/bin is where the `claude` CLI lives — without it the escalation
    arm cannot spawn and the cascade degrades without an error."""
    monkeypatch.setattr(service, "_SERVICE_ENV_FILE", tmp_path / "absent.env")
    path = service._build_environment()["PATH"]
    assert str(Path.home() / ".local" / "bin") in path.split(":")


def test_daemon_path_includes_venv_and_homebrew(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_SERVICE_ENV_FILE", tmp_path / "absent.env")
    entries = service._build_environment()["PATH"].split(":")
    assert any(e.endswith(".venv/bin") for e in entries), "venv bin missing from daemon PATH"
    assert "/opt/homebrew/bin" in entries, "ollama's Homebrew bin missing from daemon PATH"


def test_service_env_overrides_defaults(monkeypatch, tmp_path):
    f = tmp_path / "service.env"
    f.write_text("PATH=/custom/only\nMAHORAGA_REWARD_JUDGE=code\n")
    monkeypatch.setattr(service, "_SERVICE_ENV_FILE", f)
    env = service._build_environment()
    assert env["PATH"] == "/custom/only"
    assert env["MAHORAGA_REWARD_JUDGE"] == "code"


def test_home_is_passed_through(monkeypatch, tmp_path):
    """State paths resolve from HOME; launchd does not guarantee it."""
    monkeypatch.setattr(service, "_SERVICE_ENV_FILE", tmp_path / "absent.env")
    assert service._build_environment()["HOME"] == str(Path.home())


# ── plist rendering ──────────────────────────────────────────────────────────


def test_render_env_entries_emits_key_string_pairs():
    rendered = service._render_env_entries({"A": "1", "B": "2"})
    assert "<key>A</key>" in rendered
    assert "<string>1</string>" in rendered
    assert "<key>B</key>" in rendered


def test_render_env_entries_escapes_xml():
    """An unescaped & or < would produce a plist launchd refuses to load."""
    rendered = service._render_env_entries({"K": "a&b<c>d"})
    assert "a&amp;b&lt;c&gt;d" in rendered
    assert "a&b<c>d" not in rendered


def test_rendered_plist_is_valid_and_carries_settings(monkeypatch, tmp_path):
    """The full template must parse as a plist with the settings applied."""
    import plistlib

    f = tmp_path / "service.env"
    f.write_text("MAHORAGA_REWARD_JUDGE=code\n")
    monkeypatch.setattr(service, "_SERVICE_ENV_FILE", f)

    env = service._build_environment()
    content = service._PLIST_TEMPLATE.format(
        label="com.test.orch",
        orch_bin="/tmp/orch",
        workdir="/tmp",
        log="/tmp/x.log",
        env_entries=service._render_env_entries(env),
    )
    parsed = plistlib.loads(content.encode())
    assert parsed["Label"] == "com.test.orch"
    assert parsed["EnvironmentVariables"]["MAHORAGA_REWARD_JUDGE"] == "code"
    assert str(Path.home() / ".local" / "bin") in parsed["EnvironmentVariables"]["PATH"]


# ── load verification ────────────────────────────────────────────────────────


def test_load_job_reports_failure_when_not_listed(monkeypatch):
    """`launchctl load` exits 0 even when it fails — trust `list`, not the code.

    This is the bug that reported "Service installed and started." while the
    daemon was not running at all.
    """
    calls: list[tuple[str, ...]] = []

    def _fake_launchctl(*args: str):
        calls.append(args)
        return type("R", (), {"returncode": 0, "stdout": "Load failed: 5: Input/output error",
                              "stderr": ""})()

    monkeypatch.setattr(service, "_launchctl", _fake_launchctl)
    monkeypatch.setattr(service, "_is_loaded", lambda: False)

    loaded, detail = service._load_job()
    assert loaded is False
    assert "Input/output error" in detail


def test_load_job_clears_the_disabled_flag_first(monkeypatch):
    """A label disabled in launchd's database fails every load until enabled.

    The disabled state survives unload, reinstall, and reboot, so `install`
    must clear it rather than assuming a fresh plist is enough.
    """
    calls: list[tuple[str, ...]] = []

    def _fake_launchctl(*args: str):
        calls.append(args)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(service, "_launchctl", _fake_launchctl)
    monkeypatch.setattr(service, "_is_loaded", lambda: True)

    loaded, _detail = service._load_job()
    assert loaded is True
    assert calls[0][0] == "enable", f"enable must precede load, got {calls}"
    assert service._LABEL in calls[0][1]
    assert calls[1][0] == "load"
