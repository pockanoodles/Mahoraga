from __future__ import annotations
import pathlib
from .base import Tool, ToolResult

_MAX_CHARS = 20_000
_PLAIN_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts"}


class DocumentReaderTool(Tool):
    @property
    def name(self) -> str:
        return "document_reader"

    @property
    def description(self) -> str:
        return "Extract text from a local file (PDF, TXT, etc). Params: {path: string}"

    async def execute(self, params: dict) -> ToolResult:
        path_str = params.get("path")
        if not path_str:
            return ToolResult(success=False, output="", error="Missing required param: path")

        path = pathlib.Path(path_str)
        if not path.exists():
            return ToolResult(success=False, output="", error=f"File not found: {path_str}")

        suffix = path.suffix.lower()

        if suffix == ".pdf":
            return ToolResult(
                success=False,
                output="",
                error="PDF support requires PyPDF2. Install with: pip install PyPDF2",
            )

        if suffix in _PLAIN_TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8")
                if len(text) > _MAX_CHARS:
                    text = text[:_MAX_CHARS]
                return ToolResult(success=True, output=text)
            except Exception as exc:
                return ToolResult(success=False, output="", error=str(exc))

        # Fallback: try reading as text
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > _MAX_CHARS:
                text = text[:_MAX_CHARS]
            return ToolResult(success=True, output=text)
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc))
