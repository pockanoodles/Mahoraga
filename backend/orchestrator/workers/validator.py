from __future__ import annotations
import ast

from .postprocess import extract_code


def validate_code_output(response: str, language: str = "python") -> tuple[bool, str]:
    """Validate a code response. Returns (is_valid, reason)."""
    if "```" not in response:
        return False, "no_code_block"

    code = extract_code(response)

    if len(code.strip()) < 20:
        return False, "too_short"

    non_comment_lines = [
        ln for ln in code.splitlines()
        if ln.strip() and not ln.strip().startswith(("#", "//", "/*", "*"))
    ]
    if len(non_comment_lines) < 2:
        return False, "only_comments"

    if language == "python":
        try:
            ast.parse(code)
        except SyntaxError as exc:
            return False, f"syntax_error: {exc.msg}"

    if language in ("javascript", "typescript"):
        opens = code.count("{") + code.count("(") + code.count("[")
        closes = code.count("}") + code.count(")") + code.count("]")
        if abs(opens - closes) > 1:
            return False, "unbalanced_brackets"

    return True, "ok"


def validate_general_output(response: str) -> tuple[bool, str]:
    """Validate a non-code response."""
    if len(response.strip()) < 10:
        return False, "too_short"
    return True, "ok"
