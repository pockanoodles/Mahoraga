"""Tests for the security-bucket quality scorer.

Pre-fix bug: security was in _CODE_BUCKETS so AST parsing always failed
on prose answers, every agent plateaued at 0.65 ([README §benchmark
matrix](README.md)). The new `_score_security` produces a meaningful
structural score across the agents' actual outputs.
"""
from __future__ import annotations

import pytest

from backend.orchestrator.routing.quality import (
    _score_security,
    score_heuristic,
)


# ── empty / degenerate ────────────────────────────────────────────────────────


def test_empty_output_is_zero():
    assert _score_security("") == 0.0
    assert _score_security("   \n  ") == 0.0


def test_too_short_output_is_low():
    assert _score_security("hi") <= 0.20


# ── lift over the old plateau ─────────────────────────────────────────────────


def test_basic_prose_answer_clears_old_plateau():
    """A bare prose answer with no security signal should still score
    around 0.4 — strictly above the 0.65 plateau? No: the plateau is the
    *ceiling* for code-scored prose; under the new scorer a barely-tagged
    answer can be lower, while a well-structured one goes much higher.
    The fix is about *spread*, not lifting everything."""
    text = "Use HTTPS for transport security and validate user input."
    assert 0.30 < _score_security(text) < 0.85


def test_well_structured_security_answer_scores_high():
    text = """
    ## Threat model

    The attacker can submit untrusted input via HTTP. Risk: SQL injection
    (CWE-89), XSS (CWE-79), and CSRF.

    ## Mitigation

    1. Sanitize and validate all input at the boundary. Use
       parameterized queries. Apply output encoding.
    2. Enforce TLS for all transport. Rotate keys quarterly.
    3. Apply principle of least privilege; defense in depth.
    4. Add rate limiting and audit logging.

    Reference: CVE-2021-44228, CWE-22, CWE-78.
    """
    s = _score_security(text)
    assert s >= 0.85


def test_separates_a_thorough_answer_from_a_thin_one():
    thin = "Use HTTPS."
    thorough = """
    ## Threat
    Attacker injects via input field. Vulnerability classes: CWE-79, CWE-89.

    ## Mitigation
    Sanitize input, parameterize queries, rate limit, encrypt with TLS,
    enforce least privilege and defense in depth. Audit logs for changes.
    """
    s_thin = _score_security(thin)
    s_thorough = _score_security(thorough)
    assert s_thorough - s_thin > 0.30


# ── identifier signals ────────────────────────────────────────────────────────


def test_cwe_references_count_distinct():
    no_cwe = "Validate input. Sanitize output."
    one_cwe = "Validate input. Sanitize output. CWE-79 applies."
    two_cwe = "Validate input. Sanitize output. CWE-79 and CWE-89 apply."
    assert _score_security(two_cwe) > _score_security(one_cwe) > _score_security(no_cwe)


def test_cve_references_recognised():
    text_with_cve = (
        "Patch the dependency. CVE-2021-44228 is the relevant identifier."
    )
    text_without = (
        "Patch the dependency. There is a relevant identifier we need to apply."
    )
    assert _score_security(text_with_cve) > _score_security(text_without)


def test_section_headers_recognised():
    plain = (
        "We should sanitize input and validate. Apply rate limiting and TLS."
    )
    structured = (
        "## Threat\nWe should sanitize input and validate.\n"
        "## Mitigation\nApply rate limiting and TLS."
    )
    assert _score_security(structured) > _score_security(plain)


# ── public API path ───────────────────────────────────────────────────────────


def test_score_heuristic_routes_security_to_security_scorer():
    """Going through the public score_heuristic should equal _score_security."""
    text = "## Threat\nSQL injection (CWE-89). ## Mitigation\nUse parameterized queries."
    direct = _score_security(text)
    via_public = score_heuristic("How do I fix SQL injection?", text, bucket="security")
    assert via_public == pytest.approx(direct, abs=1e-6)


def test_security_no_longer_uses_code_path():
    """Security prose previously plateaued at 0.65 because AST parsing
    failed. The new scorer should produce values either below or above
    that band depending on content quality — never exactly 0.65 by accident."""
    plain_prose_answer = (
        "Security is about protecting systems from attacks and vulnerabilities."
    )
    s = score_heuristic("Tell me about security", plain_prose_answer, bucket="security")
    # The old plateau was 0.65 ± 0; the new score for this thin answer
    # should be below 0.65 because we have no CWE/CVE/mitigation signals.
    assert s < 0.55


def test_score_capped_at_one():
    """A pathologically signal-rich answer must still cap at 1.0."""
    text = (
        "## Threat\n## Mitigation\n## Risk\n## Remediation\n"
        + " ".join(f"CWE-{i}" for i in range(20, 50))
        + "\n"
        + " ".join(f"CVE-2024-{i:05d}" for i in range(1000, 1030))
        + "\n"
        + " sanitize validate parameterize escape encrypt tls rate limit "
        + " threat attack adversary exploit vulnerability risk impact " * 5
    )
    assert _score_security(text) <= 1.0


# ── debug bucket: hybrid scorer ───────────────────────────────────────────────


def test_debug_diagnostic_prose_no_longer_plateaus():
    """A1 audit follow-up: prose-only debug answers used to plateau at
    0.55 because they routed through `_score_code` and AST always failed.
    The hybrid path should now reward diagnostic vocabulary + structure."""
    prompt = "Fix the null pointer in auth.py"
    diagnostic = (
        "The root cause is that user.profile is accessed without a null check "
        "on line 42. The bug is reproducible when authenticate() returns None. "
        "Fix: add `if user and user.profile:` guard. This is a regression from "
        "the auth refactor."
    )
    useless = "Try restarting the server."
    s_diag = score_heuristic(prompt, diagnostic, bucket="debug")
    s_useless = score_heuristic(prompt, useless, bucket="debug")
    assert s_diag > 0.65
    assert s_diag - s_useless > 0.15


def test_debug_pure_code_answer_still_scores_high():
    """The hybrid scorer must not penalise pure-code debug answers."""
    prompt = "Fix the off-by-one in this loop"
    code = (
        "```python\n"
        "def consume(items):\n"
        "    for i in range(len(items) - 1):\n"
        "        process(items[i])\n"
        "    process(items[-1])\n"
        "```"
    )
    s = score_heuristic(prompt, code, bucket="debug")
    assert s >= 0.75


def test_debug_useless_answer_floors():
    """A useless answer hits the code-fallback floor (0.55). The point of
    the hybrid is that diagnostic prose now scores ABOVE this floor;
    useless content stays at it."""
    s = score_heuristic("fix the bug", "no idea", bucket="debug")
    assert s <= 0.55
