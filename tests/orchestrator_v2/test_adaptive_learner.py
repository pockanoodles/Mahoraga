import json
from unittest.mock import MagicMock, patch

import pytest

from backend.orchestrator.adaptive.learner import Learner
from backend.orchestrator.adaptive.models import AdaptationCategory, UserAdaptation


def _make_mock_response(data: list) -> MagicMock:
    content_block = MagicMock()
    content_block.text = json.dumps(data)
    response = MagicMock()
    response.content = [content_block]
    return response


async def test_learner_extracts_correction():
    correction_data = [
        {
            "category": "correction",
            "key": "response_format",
            "value": "use bullet points",
            "confidence": 0.9,
        }
    ]

    with patch("backend.orchestrator.adaptive.learner.anthropic.Anthropic") as MockClient:
        mock_client = MockClient.return_value
        mock_client.messages.create.return_value = _make_mock_response(correction_data)

        learner = Learner()
        result = await learner.analyze_interaction(
            user_message="Stop giving me walls of text. Use bullet points.",
            assistant_response="Here is a long paragraph explaining everything...",
            existing_adaptations=[],
        )

    assert len(result) == 1
    assert result[0]["category"] == "correction"
    assert result[0]["key"] == "response_format"
    assert result[0]["value"] == "use bullet points"
    assert result[0]["confidence"] == 0.9


async def test_learner_returns_empty_for_smooth_interaction():
    with patch("backend.orchestrator.adaptive.learner.anthropic.Anthropic") as MockClient:
        mock_client = MockClient.return_value
        mock_client.messages.create.return_value = _make_mock_response([])

        learner = Learner()
        result = await learner.analyze_interaction(
            user_message="Thanks, that looks great!",
            assistant_response="You're welcome! Let me know if you need anything else.",
            existing_adaptations=[],
        )

    assert result == []


async def test_learner_returns_empty_on_api_failure():
    with patch("backend.orchestrator.adaptive.learner.anthropic.Anthropic") as MockClient:
        mock_client = MockClient.return_value
        mock_client.messages.create.side_effect = Exception("API error")

        learner = Learner()
        result = await learner.analyze_interaction(
            user_message="Hello",
            assistant_response="Hi there",
            existing_adaptations=[],
        )

    assert result == []


async def test_learner_returns_empty_on_invalid_json():
    content_block = MagicMock()
    content_block.text = "not valid json {{{}"
    response = MagicMock()
    response.content = [content_block]

    with patch("backend.orchestrator.adaptive.learner.anthropic.Anthropic") as MockClient:
        mock_client = MockClient.return_value
        mock_client.messages.create.return_value = response

        learner = Learner()
        result = await learner.analyze_interaction(
            user_message="Hello",
            assistant_response="Hi",
            existing_adaptations=[],
        )

    assert result == []


async def test_learner_passes_existing_adaptations():
    existing = [
        UserAdaptation.new("u1", AdaptationCategory.preference, "theme", "dark")
    ]

    with patch("backend.orchestrator.adaptive.learner.anthropic.Anthropic") as MockClient:
        mock_client = MockClient.return_value
        mock_client.messages.create.return_value = _make_mock_response([])

        learner = Learner()
        await learner.analyze_interaction(
            user_message="Change the font",
            assistant_response="Done",
            existing_adaptations=existing,
        )

        call_kwargs = mock_client.messages.create.call_args
        user_content = call_kwargs[1]["messages"][0]["content"]
        assert "theme" in user_content
        assert "dark" in user_content
