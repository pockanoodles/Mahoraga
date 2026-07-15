"""Tests for `_classify_bucket` (backend/orchestrator/store/metrics.py).

Written 2026-07-10 after discovering the classifier had zero test coverage
and was misrouting ~100% of real security-intent traffic into the `code`
bucket (see brain/state/findings.md Era 6). The fixture prompts below are
drawn directly from real production task_goal text that was found
misclassified, not synthesized after the fact.
"""
from __future__ import annotations

from backend.orchestrator.store.metrics import _classify_bucket


# ── Regression set: real security-intent prompts found misrouted to `code` ──
# (production task_goal text, 2026-07-10 audit — all 14 previously landed in
# `code` because `code`'s keywords were checked before `security`'s, and
# "vulnerability" (singular) never matched the much more common
# "vulnerabilities" (plural) used in all of these.)

_REAL_SECURITY_PROMPTS = [
    "Audit this multi-tenant API key scheme for tenant isolation vulnerabilities: def get_data(): return {}",
    "Identify all security vulnerabilities in this admin user creation endpoint: @require_admin\ndef create_user(): return True",
    "Audit this server-sent events implementation for resource leak vulnerabilities: def stream(): pass",
    "Identify all security vulnerabilities in this image processing endpoint: def resize(url): pass",
    "Identify all security vulnerabilities in this webhook delivery system: def deliver(url, payload): pass",
    "Audit this two-factor authentication implementation for bypass vulnerabilities: def verify(): pass",
    "Identify all security vulnerabilities in this GraphQL mutation that creates users: def create(): pass",
    "Audit this environment variable handling for secret exposure: def get_config(): return {'secret': os.environ['SECRET_KEY'], 'debug': os.environ.get('DEBUG', 'false')}",
    "Identify all security vulnerabilities in this JWT middleware: def auth(req): token = req.headers['Authorization']",
    "Audit this content security policy implementation for bypass vulnerabilities: response.headers['CSP'] = '...'",
    "Identify all security vulnerabilities in this dependency injection container: def resolve(): pass",
    "Audit this role-based access control implementation for privilege escalation vulnerabilities: def check_role(): pass",
    "Identify all security vulnerabilities in this S3 presigned URL generator: def presign(bucket, key): pass",
    "Audit this input sanitization for stored XSS vulnerabilities: def save_comment(text): db.insert(text)",
]


def test_real_security_prompts_route_to_security_not_code():
    for prompt in _REAL_SECURITY_PROMPTS:
        assert _classify_bucket(prompt, hint=None) == "security", (
            f"expected security, got {_classify_bucket(prompt)!r} for: {prompt[:70]!r}"
        )


def test_vulnerability_plural_matches():
    # The original bug: "vulnerability" (singular) is not a substring of
    # "vulnerabilities" (plural), so the keyword never fired on real text.
    assert "vulnerability" not in "vulnerabilities"
    assert _classify_bucket("Fix these vulnerabilities in the login form") == "security"
    assert _classify_bucket("Fix this vulnerability in the login form") == "security"


def test_code_snippet_context_does_not_override_intent_bucket():
    """A prompt whose *intent* is security/review/refactor/debug/test should
    win over `code`'s generic keywords even when it quotes real code
    (def/return/implement/function) as context — this was the core bug:
    code was checked first and swallowed everything with a snippet in it."""
    cases = {
        "security": "Identify all security vulnerabilities in this endpoint: def foo(): return None",
        "test": "Write a unit test for a function that adds two numbers.",
        "refactor": "Refactor this function that has 6 boolean parameters into a configuration object: def f(a,b,c,d,e,f): pass",
        "debug": "Debug why this function returns None: def get_user(id): pass",
    }
    for expected, prompt in cases.items():
        assert _classify_bucket(prompt) == expected, (
            f"expected {expected}, got {_classify_bucket(prompt)!r} for: {prompt[:70]!r}"
        )


def test_pure_code_generation_prompts_still_classify_as_code():
    """Prompts with no other bucket's intent keywords should still fall
    through to `code` — the fix reorders priority, it doesn't remove code
    as a valid destination."""
    cases = [
        "Write a Python function that returns the square of a number.",
        "Implement a thread-safe LRU cache in Python that supports get and put in O(1) time.",
        "Write a Python function that parses a cron expression string and returns the next N scheduled run times.",
    ]
    for prompt in cases:
        assert _classify_bucket(prompt) == "code", (
            f"expected code, got {_classify_bucket(prompt)!r} for: {prompt[:70]!r}"
        )


def test_check_keyword_removed_from_review_to_avoid_code_collision():
    """Regression guard: adding 'check' back to review's keyword list (or
    re-introducing an equally generic word) would break ordinary
    code-generation prompts that use the word "check" in their own
    instructions, since review is now checked before code."""
    prompt = "Write a function that checks if a string is a palindrome, ignoring case."
    assert _classify_bucket(prompt) == "code"


def test_each_bucket_has_a_working_canonical_trigger():
    cases = {
        "security": "Explain how to prevent a SQL injection vulnerability.",
        "test": "Write pytest tests for the login flow.",
        "refactor": "Refactor this module to simplify the control flow.",
        "debug": "Debug why this raises a traceback on startup.",
        "research": "Research how consistent hashing works.",
        "plan": "Design an approach to migrate the database.",
        "review": "Review this pull request for correctness.",
        "code": "Write a script that parses a CSV file.",
    }
    for expected, prompt in cases.items():
        assert _classify_bucket(prompt) == expected


def test_hint_overrides_keyword_classification():
    # A prompt that would keyword-match "code" is still forced to the hint.
    assert _classify_bucket("Write a function that adds two numbers", hint="test") == "test"


def test_invalid_hint_falls_back_to_keyword_classification():
    assert _classify_bucket("Write a function that adds two numbers", hint="not-a-real-bucket") == "code"


def test_no_keyword_match_falls_back_to_general():
    assert _classify_bucket("What's up? My name is Kaito.") == "general"
    assert _classify_bucket("Reply with exactly: PONG") == "general"
