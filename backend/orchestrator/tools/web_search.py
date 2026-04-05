from __future__ import annotations
import os
import httpx
from .base import Tool, ToolResult

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_TOP_N = 5


class WebSearchTool(Tool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web and return summarized results. Params: {query: string}"

    async def execute(self, params: dict) -> ToolResult:
        api_key = os.environ.get("BRAVE_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                output="",
                error="BRAVE_API_KEY environment variable not set",
            )

        query = params.get("query")
        if not query:
            return ToolResult(success=False, output="", error="Missing required param: query")

        try:
            headers = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            }
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    _BRAVE_SEARCH_URL,
                    headers=headers,
                    params={"q": query, "count": _TOP_N},
                )
                response.raise_for_status()
                data = response.json()

            results = data.get("web", {}).get("results", [])[:_TOP_N]
            if not results:
                return ToolResult(success=True, output="No results found.")

            lines = []
            for i, r in enumerate(results, 1):
                title = r.get("title", "")
                description = r.get("description", "")
                url = r.get("url", "")
                lines.append(f"{i}. {title}\n   {description}\n   {url}")

            return ToolResult(success=True, output="\n\n".join(lines))
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc))
