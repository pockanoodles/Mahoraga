from pathlib import Path
import pytest
from backend.orchestrator.eval.task_suite import load_suite, TaskSuite, TaskDef

_YAML = """
suite: test_suite
seed: 42
tasks:
  - id: code_1
    text: "write a hello world function"
    bucket: code
    difficulty: simple
    tags: [easy]
  - id: debug_1
    text: "find the bug in this code"
    bucket: debug
    difficulty: medium
"""

def test_load_suite_from_string(tmp_path):
    f = tmp_path / "suite.yaml"
    f.write_text(_YAML)
    suite = load_suite(f)
    assert suite.name == "test_suite"
    assert suite.seed == 42
    assert len(suite.tasks) == 2
    assert suite.tasks[0].id == "code_1"
    assert suite.tasks[0].bucket == "code"
    assert suite.tasks[0].difficulty == "simple"
    assert suite.tasks[1].bucket == "debug"

def test_task_def_defaults():
    t = TaskDef(id="x", text="hello", bucket="code", difficulty="simple")
    assert t.tags == []
    assert t.timeout_s is None
