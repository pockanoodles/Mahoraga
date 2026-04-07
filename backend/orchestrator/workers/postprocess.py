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
    """Return the first fenced code block content, or the raw response if none found."""
    blocks = re.findall(r"```(?:\w*)\n(.*?)```", response, re.DOTALL)
    return blocks[0].strip() if blocks else response.strip()


def strip_preamble(response: str) -> str:
    """Remove common LLM preamble and sign-off lines."""
    lines = response.strip().splitlines()

    while lines and _PREAMBLE_RE.match(lines[0].strip()):
        lines.pop(0)

    while lines and _SIGNOFF_RE.match(lines[-1].strip()):
        lines.pop()

    return "\n".join(lines).strip()
