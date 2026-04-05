"""Tests for the tool system — registry, URL reader, document reader, code exec."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from backend.orchestrator.tools.base import Tool, ToolResult
from backend.orchestrator.tools.registry import ToolRegistry
from backend.orchestrator.tools.url_reader import UrlReaderTool
from backend.orchestrator.tools.document_reader import DocumentReaderTool
from backend.orchestrator.tools.code_exec import CodeExecTool


# --- Registry ---

class DummyTool(Tool):
    @property
    def name(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "A test tool"

    async def execute(self, params: dict) -> ToolResult:
        return ToolResult(success=True, output="ok")


def test_tool_registry_register_and_get():
    reg = ToolRegistry()
    reg.register(DummyTool())
    assert reg.get("dummy") is not None
    assert reg.get("nonexistent") is None


def test_tool_registry_list_all():
    reg = ToolRegistry()
    reg.register(DummyTool())
    assert len(reg.list_all()) == 1


def test_tool_registry_descriptions():
    reg = ToolRegistry()
    reg.register(DummyTool())
    desc = reg.descriptions()
    assert "dummy" in desc
    assert "A test tool" in desc


# --- URL Reader ---

@pytest.mark.asyncio
async def test_url_reader_success():
    tool = UrlReaderTool()
    with patch("backend.orchestrator.tools.url_reader.httpx.AsyncClient") as MockClient:
        mock_resp = MagicMock()
        mock_resp.text = "<html><body><p>Hello world content here.</p></body></html>"
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        MockClient.return_value = mock_client

        result = await tool.execute({"url": "https://example.com"})

    assert result.success
    assert "Hello world" in result.output


@pytest.mark.asyncio
async def test_url_reader_missing_url():
    tool = UrlReaderTool()
    result = await tool.execute({})
    assert not result.success
    assert result.error is not None


# --- Document Reader ---

@pytest.mark.asyncio
async def test_document_reader_text_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("Hello from a text file")
    tool = DocumentReaderTool()
    result = await tool.execute({"path": str(f)})
    assert result.success
    assert "Hello from a text file" in result.output


@pytest.mark.asyncio
async def test_document_reader_missing_file():
    tool = DocumentReaderTool()
    result = await tool.execute({"path": "/nonexistent/file.txt"})
    assert not result.success


@pytest.mark.asyncio
async def test_document_reader_pdf_unsupported():
    tool = DocumentReaderTool()
    result = await tool.execute({"path": "/tmp/fake.pdf"})
    assert not result.success


# --- Code Exec ---

@pytest.mark.asyncio
async def test_code_exec_success():
    tool = CodeExecTool()
    result = await tool.execute({"code": "print(2 + 2)"})
    assert result.success
    assert "4" in result.output


@pytest.mark.asyncio
async def test_code_exec_error():
    tool = CodeExecTool()
    result = await tool.execute({"code": "raise ValueError('boom')"})
    assert not result.success
    assert "boom" in (result.error or "")


@pytest.mark.asyncio
async def test_code_exec_missing_code():
    tool = CodeExecTool()
    result = await tool.execute({})
    assert not result.success


@pytest.mark.asyncio
async def test_code_exec_timeout():
    tool = CodeExecTool()
    result = await tool.execute({"code": "import time; time.sleep(60)"})
    assert not result.success
    assert "timed out" in (result.error or "").lower()
