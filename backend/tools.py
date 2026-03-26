import re
import subprocess
from pathlib import Path
from typing import Optional


def _resolve(workspace: str, path: str) -> Path:
    return Path(workspace) / path


def read_file(
    workspace: str,
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    lines = _resolve(workspace, path).read_text().splitlines()
    if offset is not None:
        lines = lines[offset - 1:]
    if limit is not None:
        lines = lines[:limit]
    return "\n".join(lines)


def write_file(workspace: str, path: str, content: str) -> str:
    full = _resolve(workspace, path)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return f"wrote {path}"


def run_bash(workspace: str, command: str, timeout: int = 30) -> str:
    result = subprocess.run(
        command,
        shell=True,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = result.stdout
    if result.returncode != 0:
        output += result.stderr
    return output


def list_dir(workspace: str, path: str) -> str:
    full = _resolve(workspace, path)
    entries = []
    for item in sorted(full.iterdir()):
        kind = "dir" if item.is_dir() else "file"
        entries.append(f"{kind}  {item.name}")
    return "\n".join(entries)


def grep(
    workspace: str,
    pattern: str,
    path: str,
    glob_filter: Optional[str] = None,
) -> str:
    base = _resolve(workspace, path)
    results = []
    files = list(base.rglob(glob_filter or "*")) if base.is_dir() else [base]
    for f in sorted(files):
        if not f.is_file():
            continue
        try:
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if re.search(pattern, line):
                    rel = f.relative_to(workspace)
                    results.append(f"{rel}:{i}: {line.strip()}")
        except (UnicodeDecodeError, PermissionError):
            continue
    return "\n".join(results) if results else "no matches"


def glob_files(workspace: str, pattern: str, path: str = ".") -> str:
    base = _resolve(workspace, path)
    matches = list(base.glob(pattern))
    rel = [str(m.relative_to(workspace)) for m in sorted(matches)]
    return "\n".join(rel) if rel else "no matches"


# Ollama tool definitions (sent in every /api/chat request)
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file. Returns file content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "Start line (1-indexed)"},
                    "limit": {"type": "integer", "description": "Max lines to return"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Creates parent directories if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "Run a bash command. cwd is the workspace root. 30s timeout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List directory contents with file/dir type labels.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Regex search across files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob_filter": {"type": "string", "description": "e.g. '*.py'"},
                },
                "required": ["pattern", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "File pattern matching.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
]


def dispatch(workspace: str, name: str, args: dict) -> str:
    """Execute a tool by name with the given args dict."""
    if name == "read_file":
        return read_file(workspace, args["path"], args.get("offset"), args.get("limit"))
    if name == "write_file":
        return write_file(workspace, args["path"], args["content"])
    if name == "run_bash":
        return run_bash(workspace, args["command"])
    if name == "list_dir":
        return list_dir(workspace, args["path"])
    if name == "grep":
        return grep(workspace, args["pattern"], args["path"], args.get("glob_filter"))
    if name == "glob":
        return glob_files(workspace, args["pattern"], args.get("path", "."))
    return f"unknown tool: {name}"
