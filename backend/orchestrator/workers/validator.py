"""Output validators for Ollama worker results."""
from __future__ import annotations


def validate_code_output(response: str) -> tuple[bool, str]:
    """Validate a code response. Returns (is_valid, reason).

    NOTE: called with already-extracted code (backtick fences already stripped
    by OllamaWorker.execute → extract_code). Do NOT check for fence presence.
    """
    code = response.strip()

    if len(code) < 20:
        return False, "too_short"

    non_comment_lines = [
        ln for ln in code.splitlines()
        if ln.strip() and not ln.strip().startswith(("#", "//", "/*", "*"))
    ]
    if len(non_comment_lines) < 2:
        return False, "only_comments"

    return True, "ok"


def validate_general_output(response: str) -> tuple[bool, str]:
    """Validate a non-code response. Any non-empty answer is valid —
    short answers like '4' or 'Paris' are correct for simple questions."""
    if not response.strip():
        return False, "empty_response"
    return True, "ok"
