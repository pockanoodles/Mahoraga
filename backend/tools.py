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
    try:
        lines = _resolve(workspace, path).read_text().splitlines()
    except FileNotFoundError:
        return f"error: file not found: {path}"
    except (PermissionError, OSError) as e:
        return f"error: {e}"
    start = (offset - 1) if offset is not None else 0
    if offset is not None:
        lines = lines[offset - 1:]
    if limit is not None:
        lines = lines[:limit]
    truncated = False
    if len(lines) > 300:
        lines = lines[:300]
        truncated = True
    numbered = [f"{start + i + 1:>4}│ {line}" for i, line in enumerate(lines)]
    result = "\n".join(numbered)
    if truncated:
        result += "\n[output truncated at 300 lines — use offset/limit to read more]"
    return result


def write_file(workspace: str, path: str, content: str) -> str:
    try:
        full = _resolve(workspace, path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return f"wrote {path}"
    except (PermissionError, OSError) as e:
        return f"error: {e}"


def run_bash(workspace: str, command: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        output += f"\nexit: {result.returncode}"
        lines = output.splitlines()
        truncated = False
        if len(lines) > 300:
            lines = lines[:300]
            truncated = True
        result_str = "\n".join(lines)
        if truncated:
            result_str += "\n[output truncated at 300 lines]"
        return result_str
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {timeout}s"


def list_dir(workspace: str, path: str) -> str:
    full = _resolve(workspace, path)
    try:
        entries = []
        for item in sorted(full.iterdir()):
            kind = "dir" if item.is_dir() else "file"
            entries.append(f"{kind}  {item.name}")
        return "\n".join(entries)
    except FileNotFoundError:
        return f"error: path not found: {path}"
    except (PermissionError, OSError) as e:
        return f"error: {e}"


def edit_file(workspace: str, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Surgical patch — replace old_string with new_string in an existing file."""
    try:
        full = _resolve(workspace, path)
        content = full.read_text()
    except FileNotFoundError:
        return f"error: file not found: {path}"
    except (PermissionError, OSError) as e:
        return f"error: {e}"

    if replace_all:
        count = content.count(old_string)
        if count == 0:
            return f"error: old_string not found in {path}"
        full.write_text(content.replace(old_string, new_string))
        return f"edited {path} ({count} replacements)"
    else:
        count = content.count(old_string)
        if count == 0:
            return f"error: old_string not found in {path} — read the file first and match exactly"
        if count > 1:
            return f"error: old_string matches {count} locations in {path} — include more surrounding context to make it unique"
        full.write_text(content.replace(old_string, new_string, 1))
        return f"edited {path}"


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
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"},
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
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Surgical edit — replace one exact occurrence of old_string with new_string in an existing file. Fails if old_string is not found or matches multiple locations. Set replace_all=true for bulk replacements. Always prefer this over write_file for existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string", "description": "Exact text to replace. Must be unique — include surrounding lines for context if needed."},
                    "new_string": {"type": "string", "description": "Text to replace it with."},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false). Use for renames and bulk updates."},
                },
                "required": ["path", "old_string", "new_string"],
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
        return run_bash(workspace, args["command"], timeout=args.get("timeout", 30))
    if name == "list_dir":
        return list_dir(workspace, args["path"])
    if name == "grep":
        return grep(workspace, args["pattern"], args["path"], args.get("glob_filter"))
    if name == "glob":
        return glob_files(workspace, args["pattern"], args.get("path", "."))
    if name == "edit_file":
        return edit_file(workspace, args["path"], args["old_string"], args["new_string"], replace_all=args.get("replace_all", False))
    return f"unknown tool: {name}"
