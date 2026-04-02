import dataclasses
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from backend.orchestrator.verifier.verifier import Verifier, VerificationResult, VerifierError
from backend.orchestrator.domain.models import Task


def make_task(goal="Fix auth", done_criteria="All auth tests pass") -> Task:
    t = Task.new(run_id="r1", title="T", goal=goal)
    return dataclasses.replace(t, done_criteria=done_criteria)


def _mock_client(score: int, feedback: str = "") -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps({"score": score, "feedback": feedback}))]
    client = MagicMock()
    client.messages.create = MagicMock(return_value=resp)
    return client


async def test_verify_score_8_returns_pass():
    client = _mock_client(score=8)
    v = Verifier(client)
    result = await v.verify(make_task(), "output text")
    assert result.passed is True
    assert result.action == "pass"
    assert result.score == 8


async def test_verify_score_10_returns_pass():
    client = _mock_client(score=10)
    v = Verifier(client)
    result = await v.verify(make_task(), "output")
    assert result.passed is True
    assert result.action == "pass"


async def test_verify_score_7_returns_retry():
    client = _mock_client(score=7, feedback="missing edge case")
    v = Verifier(client)
    result = await v.verify(make_task(), "output")
    assert result.passed is False
    assert result.action == "retry"
    assert result.feedback == "missing edge case"


async def test_verify_score_4_returns_retry():
    client = _mock_client(score=4, feedback="incomplete")
    v = Verifier(client)
    result = await v.verify(make_task(), "output")
    assert result.action == "retry"


async def test_verify_score_3_returns_escalate():
    client = _mock_client(score=3, feedback="wrong direction")
    v = Verifier(client)
    result = await v.verify(make_task(), "output")
    assert result.passed is False
    assert result.action == "escalate"


async def test_verify_score_0_returns_escalate():
    client = _mock_client(score=0, feedback="completely wrong")
    v = Verifier(client)
    result = await v.verify(make_task(), "output")
    assert result.action == "escalate"


async def test_verify_bad_json_raises_verifier_error():
    resp = MagicMock()
    resp.content = [MagicMock(text="not json at all")]
    client = MagicMock()
    client.messages.create = MagicMock(return_value=resp)
    v = Verifier(client)
    with pytest.raises(VerifierError):
        await v.verify(make_task(), "output")


async def test_verify_missing_score_key_raises_verifier_error():
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps({"feedback": "ok"}))]
    client = MagicMock()
    client.messages.create = MagicMock(return_value=resp)
    v = Verifier(client)
    with pytest.raises(VerifierError):
        await v.verify(make_task(), "output")


async def test_verify_api_exception_raises_verifier_error():
    client = MagicMock()
    client.messages.create = MagicMock(side_effect=Exception("API down"))
    v = Verifier(client)
    with pytest.raises(VerifierError):
        await v.verify(make_task(), "output")
