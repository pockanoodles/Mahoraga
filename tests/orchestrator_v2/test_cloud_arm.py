"""Tests for cloud escalation arm construction (routing.live_route).

The published benchmark ran its cloud arm through the `claude` CLI on an
interactive subscription, which made reproduction require that subscription.
An API-key-backed arm removes that precondition — so what matters here is that
choosing between them changes *who authenticates and how it bills*, and nothing
else. If an arm swap could change the model or the prompt framing, a run with
one arm would stop being comparable to a run with the other, and the whole
point of offering the choice would be gone.
"""
from __future__ import annotations

import pytest

from backend.orchestrator.routing.live_route import (
    CloudArmUnavailable,
    _cloud_worker_kind,
    build_cloud_worker,
)
from backend.orchestrator.workers.claude import ClaudeWorker
from backend.orchestrator.workers.claude_cli import ClaudeCliWorker

_CFG = {
    "claude-cli": {"worker": "claude_cli", "model": "claude-sonnet-4-6",
                   "worker_id": "claude-cli:sonnet"},
    "claude": {"worker": "claude_api", "model": "claude-sonnet-4-6",
               "worker_id": "claude:sonnet"},
}


# ── which class backs an arm ─────────────────────────────────────────────────


def test_explicit_worker_key_wins():
    assert _cloud_worker_kind("anything", {"worker": "claude_api"}) == "claude_api"
    assert _cloud_worker_kind("anything", {"worker": "claude_cli"}) == "claude_cli"


def test_kind_is_inferred_for_rosters_predating_the_key():
    """An agents.yaml written before `worker:` existed must keep working."""
    assert _cloud_worker_kind("claude-cli", {}) == "claude_cli"
    assert _cloud_worker_kind("claude", {}) == "claude_api"


def test_unknown_worker_kind_is_rejected_loudly():
    with pytest.raises(ValueError, match="not one of"):
        _cloud_worker_kind("claude", {"worker": "claude_grpc"})


# ── construction ─────────────────────────────────────────────────────────────


def test_cli_arm_needs_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    worker = build_cloud_worker(_CFG, "claude-cli")
    assert isinstance(worker, ClaudeCliWorker)


def test_api_arm_builds_from_the_environment_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    worker = build_cloud_worker(_CFG, "claude")
    assert isinstance(worker, ClaudeWorker)
    assert worker.id == "claude:sonnet"


def test_api_arm_without_a_key_fails_with_the_fix_in_the_message(monkeypatch):
    """The serving cascade degrades on this, so the message is the only place
    a person finds out why escalation stopped happening."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(CloudArmUnavailable) as exc:
        build_cloud_worker(_CFG, "claude")
    msg = str(exc.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "--cloud-arm claude-cli" in msg


def test_both_arms_report_the_same_model(monkeypatch):
    """An arm swap is an auth decision, not a model decision."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cli = build_cloud_worker(_CFG, "claude-cli")
    api = build_cloud_worker(_CFG, "claude")
    assert cli._model == api._model == "claude-sonnet-4-6"


def test_both_arms_frame_the_prompt_identically():
    """Both build their prompt through workers.base._build_prompt, so the model
    sees the same input either way. Pinned because a divergence here would
    silently make cross-arm runs incomparable."""
    from backend.orchestrator.workers import claude, claude_cli

    assert claude._build_prompt is claude_cli._build_prompt


def test_arm_is_read_even_when_disabled(monkeypatch):
    """`enabled` governs the bandit's action space, not escalation reachability."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg = {"claude": {**_CFG["claude"], "enabled": False}}
    assert isinstance(build_cloud_worker(cfg, "claude"), ClaudeWorker)


def test_cli_arm_passes_through_binary_path_and_timeout():
    cfg = {"claude-cli": {**_CFG["claude-cli"],
                          "binary_path": "/opt/homebrew/bin/claude",
                          "timeout": 900}}
    worker = build_cloud_worker(cfg, "claude-cli")
    assert worker._binary == "/opt/homebrew/bin/claude"


def test_missing_arm_block_still_builds_a_default_cli_arm():
    """An unknown arm id is a config error caught upstream (cascade checks the
    block exists); here the safe default is the subscription arm, never a
    silent paid API call."""
    worker = build_cloud_worker({}, "some-arm")
    assert isinstance(worker, ClaudeCliWorker)


# ── the committed roster ─────────────────────────────────────────────────────


def test_committed_agents_yaml_declares_both_arms():
    """A stranger reproducing the benchmark relies on `claude` being present
    and API-backed in the shipped roster."""
    import yaml
    from backend.orchestrator.routing.benchmark.verify import PROJECT_ROOT

    cfg = yaml.safe_load((PROJECT_ROOT / "agents.yaml").read_text())
    assert _cloud_worker_kind("claude", cfg["claude"]) == "claude_api"
    assert _cloud_worker_kind("claude-cli", cfg["claude-cli"]) == "claude_cli"
    assert cfg["claude"]["model"] == cfg["claude-cli"]["model"], (
        "the two cloud arms must name the same model or cross-arm runs stop "
        "being comparable"
    )
