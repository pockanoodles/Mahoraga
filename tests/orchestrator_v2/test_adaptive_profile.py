import pytest

from backend.orchestrator.adaptive.models import AdaptationCategory, UserAdaptation
from backend.orchestrator.adaptive.profile import build_profile_prompt, MIN_CONFIDENCE


def _make_adaptation(category, key, value, confidence):
    a = UserAdaptation.new("user-test", category, key, value, confidence=confidence)
    return a


def test_build_profile_prompt_with_adaptations():
    adaptations = [
        _make_adaptation(AdaptationCategory.preference, "theme", "dark", 0.9),
        _make_adaptation(AdaptationCategory.style, "verbosity", "concise", 0.6),
        _make_adaptation(AdaptationCategory.correction, "tone", "formal", 0.4),
    ]
    result = build_profile_prompt(adaptations)

    assert result is not None
    assert result.startswith("User profile:")
    assert "[strong] preference: theme = dark" in result
    assert "[moderate] style: verbosity = concise" in result
    assert "[weak] correction: tone = formal" in result


def test_build_profile_prompt_filters_below_min_confidence():
    adaptations = [
        _make_adaptation(AdaptationCategory.preference, "theme", "dark", 0.9),
        _make_adaptation(AdaptationCategory.pattern, "noise", "ignored", 0.1),
        _make_adaptation(AdaptationCategory.pattern, "below", "cutoff", 0.29),
    ]
    result = build_profile_prompt(adaptations)

    assert result is not None
    assert "noise" not in result
    assert "below" not in result
    assert "theme" in result


def test_build_profile_prompt_empty_returns_none():
    result = build_profile_prompt([])
    assert result is None


def test_build_profile_prompt_all_below_min_confidence_returns_none():
    adaptations = [
        _make_adaptation(AdaptationCategory.preference, "a", "b", 0.1),
        _make_adaptation(AdaptationCategory.style, "c", "d", 0.05),
    ]
    result = build_profile_prompt(adaptations)
    assert result is None


def test_build_profile_prompt_strength_labels():
    adaptations = [
        _make_adaptation(AdaptationCategory.preference, "a", "1", 0.8),   # strong
        _make_adaptation(AdaptationCategory.preference, "b", "2", 0.79),  # moderate
        _make_adaptation(AdaptationCategory.preference, "c", "3", 0.5),   # moderate
        _make_adaptation(AdaptationCategory.preference, "d", "4", 0.49),  # weak
        _make_adaptation(AdaptationCategory.preference, "e", "5", 0.3),   # weak (at boundary)
    ]
    result = build_profile_prompt(adaptations)

    assert result is not None
    assert "[strong] preference: a = 1" in result
    assert "[moderate] preference: b = 2" in result
    assert "[moderate] preference: c = 3" in result
    assert "[weak] preference: d = 4" in result
    assert "[weak] preference: e = 5" in result


def test_min_confidence_is_correct():
    assert MIN_CONFIDENCE == 0.3
