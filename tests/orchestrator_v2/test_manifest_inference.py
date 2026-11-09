"""A run's manifest says where the model answered, so a split setup (agent and
tests on one machine, the model on another) is never credited to either."""
from __future__ import annotations

import pytest

from backend.orchestrator.agentbench import manifest, ollama
from backend.orchestrator.agentbench.export import machine_label
from backend.orchestrator.audit import evidence

BOX = {"model": "OptiPlex 7070", "memory_gb": 16}


@pytest.fixture(autouse=True)
def _no_declaration(monkeypatch):
    monkeypatch.delenv("MAHORAGA_INFERENCE_MACHINE", raising=False)


def test_loopback_with_nothing_declared_is_here(monkeypatch):
    monkeypatch.setattr(ollama, "BASE_URL", "http://127.0.0.1:11434")
    assert manifest.inference() == {"ollama": "http://127.0.0.1:11434", "machine": None, "here": True}


def test_a_declared_machine_is_never_here_even_through_a_tunnel(monkeypatch):
    monkeypatch.setattr(ollama, "BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("MAHORAGA_INFERENCE_MACHINE", "Mac Studio M5 Max, 64 GB")
    inf = manifest.inference()
    assert inf["here"] is False and inf["machine"] == "Mac Studio M5 Max, 64 GB"


def test_a_remote_url_is_not_here(monkeypatch):
    monkeypatch.setattr(ollama, "BASE_URL", "http://100.84.222.75:11434")
    assert manifest.inference()["here"] is False


def test_the_label_names_both_machines_of_a_split_run():
    local = {"machine": BOX, "inference": {"ollama": "http://127.0.0.1:11434", "machine": None,
                                           "here": True}}
    split = {"machine": BOX, "inference": {"ollama": "http://127.0.0.1:11435",
                                           "machine": "Mac Studio M5 Max, 64 GB", "here": False}}
    by_url = {"machine": BOX, "inference": {"ollama": "http://100.84.222.75:11434",
                                            "machine": None, "here": False}}
    assert machine_label(local) == "OptiPlex 7070, 16 GB"
    assert machine_label({"machine": BOX}) == "OptiPlex 7070, 16 GB"  # manifests before this
    assert machine_label(split) == "OptiPlex 7070, 16 GB, model served by Mac Studio M5 Max, 64 GB"
    assert machine_label(by_url) == "OptiPlex 7070, 16 GB, model served by 100.84.222.75"


def test_evidence_refuses_a_split_run_pooled_with_a_local_one(tmp_path):
    from backend.orchestrator.agentbench.bank import Bench

    bench = Bench(tmp_path / "repo", tmp_path / "root")
    here = {"ollama": "http://127.0.0.1:11434", "machine": None, "here": True}
    there = {**here, "machine": "Mac Studio M5 Max, 64 GB", "here": False}
    manifest.write(bench, {"run_id": "r1", "machine": BOX, "inference": here})
    manifest.write(bench, {"run_id": "r2", "machine": BOX, "inference": there})
    assert evidence._hardware([bench], {"r2"}) == \
        "OptiPlex 7070, 16 GB, model served by Mac Studio M5 Max, 64 GB"
    with pytest.raises(ValueError, match="2 machines"):
        evidence._hardware([bench], {"r1", "r2"})


def test_a_receipt_tells_a_split_run_from_a_local_one():
    from backend.orchestrator.agentbench.report import _setup

    base = {"models": {"ollama": "0.40.13", "digests": {"m": "sha256:aaaabbbbcccc1111"}},
            "machine": BOX, "harness": {"version": "2.1.0", "commit": "c1bf98130ab"}}
    here = _setup({**base, "inference": {"ollama": "http://127.0.0.1:11434", "here": True}}, "fake:m")
    there = _setup({**base, "inference": {"ollama": "http://127.0.0.1:11435", "here": False,
                                          "machine": "Mac Studio M5 Max, 64 GB"}}, "fake:m")
    assert "OptiPlex 7070 16 GB, " in here and "served by" not in here
    assert "OptiPlex 7070 16 GB, model served by Mac Studio M5 Max, 64 GB, " in there
