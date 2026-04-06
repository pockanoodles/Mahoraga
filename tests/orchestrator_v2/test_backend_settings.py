# tests/orchestrator_v2/test_backend_settings.py
import json
import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient, ASGITransport
from backend.orchestrator.service.app import app


@pytest.mark.asyncio
async def test_get_backend_settings_returns_defaults():
    with patch("backend.orchestrator.service.app._config") as mock_cfg:
        mock_cfg.all.return_value = {
            "active_backend": "claude",
            "ollama_base_url": "http://localhost:11434",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/settings/backend")

    assert response.status_code == 200
    body = response.json()
    assert body["active_backend"] == "claude"
    assert body["ollama_base_url"] == "http://localhost:11434"


@pytest.mark.asyncio
async def test_post_backend_settings_switches_to_ollama():
    with patch("backend.orchestrator.service.app._config") as mock_cfg:
        mock_cfg.all.return_value = {
            "active_backend": "ollama",
            "ollama_base_url": "http://localhost:11434",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/settings/backend",
                json={"active_backend": "ollama"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["active_backend"] == "ollama"
    mock_cfg.set.assert_called_once_with("active_backend", "ollama")


@pytest.mark.asyncio
async def test_post_backend_settings_rejects_invalid_backend():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/settings/backend",
            json={"active_backend": "openai"},
        )
    assert response.status_code == 422
