from __future__ import annotations
import re

_PREAMBLE_RE = re.compile(
    r"^(here'?s?|sure|below|certainly|of course|let me|i'?ll)",
    re.IGNORECASE,
)
_SIGNOFF_RE = re.compile(
    r"^(let me know|hope this|feel free|happy to)",
    re.IGNORECASE,
)


def extract_code(response: str) -> str:
    """Return the first fenced code block content, or the raw response if none found.

    Tolerates an *unclosed* opening fence — common when a model's output is
    truncated mid-block (hit the token cap before emitting the closing ```).
    Without this, a truncated ```python\\n<code> was returned verbatim, leaving
    a literal "```python" as the first line of "code" and breaking anything that
    tried to run it (found 2026-07-15 while scoring the verifiable-reward bench).
    """
    blocks = re.findall(r"```(?:\w*)\n(.*?)```", response, re.DOTALL)
    if blocks:
        return blocks[0].strip()
    stripped = response.strip()
    opening = re.match(r"```[^\n]*\n(.*)$", stripped, re.DOTALL)
    if opening:
        body: str = opening.group(1)
        body = re.sub(r"\n?```\s*$", "", body)  # drop a dangling close, if any
        return body.strip()
    return stripped


def strip_preamble(response: str) -> str:
    """Remove common LLM preamble and sign-off lines."""
    lines = response.strip().splitlines()

    while lines and _PREAMBLE_RE.match(lines[0].strip()):
        lines.pop(0)

    while lines and _SIGNOFF_RE.match(lines[-1].strip()):
        lines.pop()

    return "\n".join(lines).strip()
