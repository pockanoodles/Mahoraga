"""
Start the orchestrator service:
    python -m backend.orchestrator_svc.main

Runs on port 11279. Extension worker expected at localhost:11278.
Requires ANTHROPIC_API_KEY in environment for Claude worker.
"""
import uvicorn
from . import service as svc
from ..workers.extension_adapter import ExtensionAdapter
from ..workers.claude_adapter import ClaudeAdapter


def main() -> None:
    svc._registry.register(ExtensionAdapter())
    svc._registry.register(ClaudeAdapter())
    uvicorn.run(svc.app, host="0.0.0.0", port=11279, log_level="info")


if __name__ == "__main__":
    main()
