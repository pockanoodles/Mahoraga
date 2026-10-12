"""The harness block of a run manifest says which Mahoraga measured the run."""
from __future__ import annotations

from backend.orchestrator.agentbench import manifest
from backend.orchestrator.version import package_version


def test_outside_a_checkout_it_records_the_version_and_no_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(manifest, "HARNESS_ROOT", tmp_path)  # like site-packages: no .git
    assert manifest.harness() == {"version": package_version(), "commit": None, "dirty": None}


def test_in_this_checkout_it_also_records_the_commit():
    h = manifest.harness()
    assert h["version"] == package_version()
    assert h["commit"] and not str(h["commit"]).startswith("error")
