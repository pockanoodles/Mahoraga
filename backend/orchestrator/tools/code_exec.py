from __future__ import annotations
import asyncio
from .base import Tool, ToolResult

_TIMEOUT_SECONDS = 30


class CodeExecTool(Tool):
    @property
    def name(self) -> str:
        return "code_exec"

    @property
    def description(self) -> str:
        return "Execute Python code in a sandboxed environment. Params: {code: string}"

    async def execute(self, params: dict) -> ToolResult:
        code = params.get("code")
        if not code:
            return ToolResult(success=False, output="", error="Missing required param: code")

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async def _run() -> tuple[bytes, bytes]:
                return await proc.communicate()

            stdout_bytes, stderr_bytes = await asyncio.wait_for(_run(), timeout=_TIMEOUT_SECONDS)

            if proc.returncode == 0:
                return ToolResult(success=True, output=stdout_bytes.decode())
            else:
                return ToolResult(
                    success=False,
                    output="",
                    error=stderr_bytes.decode(),
                )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return ToolResult(
                success=False,
                output="",
                error=f"Execution timed out after {_TIMEOUT_SECONDS} seconds",
            )
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc))
