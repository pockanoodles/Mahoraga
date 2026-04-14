"""Tests for TaskContext feature extraction."""
import numpy as np
import pytest
from backend.orchestrator.routing.context import TaskContext


def test_from_task_simple_qa():
    """Short factual question -> tier 1, is_question=1, low code density."""
    class T:
        goal = "what does HTTP stand for?"
    ctx = TaskContext.from_task(T())
    assert ctx.is_question == 1.0
    assert ctx.code_keyword_density < 0.1
    # word_count=5, code_count=0, file_refs=0 → tier 1
    assert ctx.complexity_tier == pytest.approx(1 / 3.0)


def test_from_task_code_generation():
    """Code task → high code keyword density, creation keywords."""
    class T:
        goal = "write a python function to implement binary search in a sorted array"
    ctx = TaskContext.from_task(T())
    assert ctx.code_keyword_density > 0
    assert ctx.has_creation_keywords == 1.0


def test_from_task_research():
    """Research question → has_research_keywords, is_question."""
    class T:
        goal = "explain how TCP handshake works"
    ctx = TaskContext.from_task(T())
    assert ctx.has_research_keywords == 1.0


def test_to_vector_shape_and_range():
    """Vector must be float64, shape (9,), all values in [0, 1]."""
    class T:
        goal = "fix the bug in the API endpoint"
    ctx = TaskContext.from_task(T())
    v = ctx.to_vector()
    assert v.dtype == np.float64
    assert v.shape == (9,)
    assert all(0.0 <= x <= 1.0 for x in v), f"Out-of-range values: {v}"


def test_d_property():
    class T:
        goal = "hello world"
    assert TaskContext.from_task(T()).d == 9


def test_from_dict():
    """Works with dict input, not just object."""
    ctx = TaskContext.from_task({"goal": "explain what recursion means"})
    assert ctx.has_research_keywords == 1.0


def test_tier_clamped():
    """External tier values are clamped to [1,3]."""
    class T:
        goal = "some task"
        tier = 99
    ctx = TaskContext.from_task(T())
    assert ctx.complexity_tier == pytest.approx(3 / 3.0)


def test_error_keywords():
    class T:
        goal = "fix the bug and debug the traceback"
    ctx = TaskContext.from_task(T())
    assert ctx.has_error_keywords == 1.0


def test_word_count_norm_capped_at_one():
    """Very long goal should have word_count_norm == 1.0 (capped)."""
    class T:
        goal = " ".join(["word"] * 300)
    ctx = TaskContext.from_task(T())
    assert ctx.word_count_norm == 1.0


def test_question_mark_triggers_is_question():
    """A goal ending in '?' is detected as a question."""
    class T:
        goal = "is this working?"
    ctx = TaskContext.from_task(T())
    assert ctx.is_question == 1.0


def test_no_special_keywords():
    """Neutral goal should have all keyword flags at 0."""
    class T:
        goal = "run the program"
    ctx = TaskContext.from_task(T())
    # "run" is not in error/creation/research/code keyword sets
    assert ctx.has_error_keywords == 0.0
    assert ctx.has_creation_keywords == 0.0
    assert ctx.has_research_keywords == 0.0
    assert ctx.is_question == 0.0
