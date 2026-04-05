from __future__ import annotations
import re
import httpx
from .base import Tool, ToolResult

_MAX_CHARS = 10_000


def _strip_html(html: str) -> str:
    # Remove script and style blocks (including content)
    text = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


class UrlReaderTool(Tool):
    @property
    def name(self) -> str:
        return "url_reader"

    @property
    def description(self) -> str:
        return "Fetch and extract text content from a URL. Params: {url: string}"

    async def execute(self, params: dict) -> ToolResult:
        url = params.get("url")
        if not url:
            return ToolResult(success=False, output="", error="Missing required param: url")

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                response = await client.get(url)
                response.raise_for_status()
                text = _strip_html(response.text)
                if len(text) > _MAX_CHARS:
                    text = text[:_MAX_CHARS]
                return ToolResult(success=True, output=text)
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc))
