"""The service commands on Linux: a `service.Job` as a systemd user unit.

No systemd is needed: systemctl is faked, except in the one test that asks a
real systemd-analyze whether the unit parses, which runs only where it exists.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import typer

from backend.orchestrator.cli.commands import service, systemd


def _job(tmp_path: Path, **kw) -> service.Job:
    return service.Job("com.mahoraga.audit-worker", ("agentbench", "worker"),
                       tmp_path / "state" / "worker.log", workdir=tmp_path / "my dir", **kw)


def test_a_unit_is_named_for_its_label():
    assert systemd.unit_name("com.mahoraga.audit-worker") == "mahoraga-audit-worker.service"
    assert systemd.unit_name(service.DAEMON.label) == "mahoraga-orch.service"


def test_a_job_renders_as_a_user_unit(tmp_path):
    env = {"PATH": "/venv/bin:/usr/bin", "WEIRD": 'a "q" 50% $x'}
    unit = systemd.render_unit(_job(tmp_path), Path("/venv/bin/orch"), env, "mahoraga audit service")
    lines = unit.splitlines()
    assert 'ExecStart="/venv/bin/orch" "agentbench" "worker"' in lines
    assert f"WorkingDirectory={tmp_path / 'my dir'}" in lines
    assert 'Environment="PATH=/venv/bin:/usr/bin"' in lines
    # Quotes escaped and % doubled; $ is literal outside ExecStart.
    assert 'Environment="WEIRD=a \\"q\\" 50%% $x"' in lines
    # Restarts after a crash, not after a stop, and a stop isn't a failure.
    assert {"Restart=on-failure", "SuccessExitStatus=130 143",
            "WantedBy=default.target"} <= set(lines)
    log = tmp_path / "state" / "worker.log"
    assert f"StandardOutput=append:{log}" in lines and f"StandardError=append:{log}" in lines


def test_exec_start_keeps_a_dollar_literal(tmp_path):
    job = service.Job("com.x", ("run", "$HOME"), tmp_path / "l", workdir=tmp_path)
    assert 'ExecStart="/o" "run" "$$HOME"' in systemd.render_unit(job, Path("/o"), {}, "c")


def test_a_setting_with_a_newline_is_refused(tmp_path):
    with pytest.raises(ValueError, match="newline"):
        systemd.render_unit(_job(tmp_path), Path("/o"), {"X": "a\nb"}, "c")


@pytest.mark.skipif(not (sys.platform.startswith("linux") and shutil.which("systemd-analyze")),
                    reason="needs systemd-analyze")
def test_systemd_parses_the_unit(tmp_path):
    (tmp_path / "my dir").mkdir()
    unit = tmp_path / "mahoraga-audit-worker.service"
    unit.write_text(systemd.render_unit(_job(tmp_path), Path(sys.executable),
                                        {"PATH": "/usr/bin"}, "c"))
    r = subprocess.run(["systemd-analyze", "verify", str(unit)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _fake_systemctl(monkeypatch, running: bool):
    calls = []
    monkeypatch.setattr(systemd, "_systemctl", lambda *a: calls.append(a) or
                        subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(systemd, "is_running", lambda job: running)
    monkeypatch.setattr(systemd, "_linger_note", lambda: None)
    return calls


def test_install_writes_the_unit_and_restarts_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    calls = _fake_systemctl(monkeypatch, running=True)
    job = _job(tmp_path)
    systemd.install(job, Path("/venv/bin/orch"), {"PATH": "/usr/bin"}, "c", settle=0)
    unit = tmp_path / "config" / "systemd" / "user" / "mahoraga-audit-worker.service"
    assert unit.read_text().startswith("[Unit]")
    assert calls == [("daemon-reload",), ("enable", unit.name), ("restart", unit.name)]
    assert job.log.parent.is_dir()


def test_an_install_that_does_not_stay_up_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _fake_systemctl(monkeypatch, running=False)
    with pytest.raises(typer.Exit):
        systemd.install(_job(tmp_path), Path("/o"), {}, "c", settle=0)


def test_on_linux_the_service_commands_use_systemd(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "_linux", lambda: True)
    monkeypatch.setattr(service, "_orch_bin", lambda: Path(sys.executable))
    monkeypatch.setattr(service, "_SERVICE_ENV_FILE", tmp_path / "absent.env")
    seen = {}
    monkeypatch.setattr(systemd, "install", lambda job, orch, env, cli: seen.update(env=env, cli=cli))
    service.install_job(_job(tmp_path, env={"PYTHONPATH": "/pin"}), "mahoraga audit service")
    assert seen["env"]["PYTHONPATH"] == "/pin" and "/.local/bin" in seen["env"]["PATH"]
    assert seen["cli"] == "mahoraga audit service"
    assert service.manager() == "systemd"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert not service.installed(_job(tmp_path))  # no plist is looked for
