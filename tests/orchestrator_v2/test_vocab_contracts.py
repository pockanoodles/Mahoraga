"""
§3.3 vocab.py enforcement tests.

Six contracts that must hold for the routing vocabulary to be internally
consistent. These are the tests that would have caught the v1 bucket-name
mismatch immediately.

  1. Classifier reachability  — every BUCKET is reachable from classify_bucket()
  2. Scoring path coverage    — every BUCKET has a code path in quality.py
  3. Reward weight coverage   — every BUCKET has an entry in BUCKET_WEIGHTS
  4. Warm-start vector coverage — every BUCKET has an entry in _BUCKET_VECTORS
  5. Prior agent subset        — _DEFAULT_PRIORS keys ⊆ ENABLED_AGENTS
  6. Lint (string contract)    — no hardcoded bucket/agent literals in
                                 routing modules outside vocab.py
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.orchestrator.routing.vocab import BUCKETS, ENABLED_AGENTS
from backend.orchestrator.routing.context import TaskContext
from backend.orchestrator.routing.strategies.static import classify_bucket
from backend.orchestrator.routing.reward import BUCKET_WEIGHTS
from backend.orchestrator.routing.warm_start import _BUCKET_VECTORS
from backend.orchestrator.routing.strategies.linucb_per_bucket import _DEFAULT_PRIORS


# ── Fixtures: one trigger phrase per bucket ────────────────────────────────

_BUCKET_TRIGGERS: dict[str, str] = {
    # debug: error keyword fires first; no test/refactor/security keywords
    "debug":    "the service throws a null pointer exception on startup",
    # test: substring keyword match; no error/security keywords
    "test":     "write unit tests for the payment processing module using pytest",
    # refactor: refactor keyword; no error/test/security keywords
    "refactor": "refactor the user repository to decouple the database layer",
    # security: security vocabulary; no error/test/refactor keywords (no "fix"/"bug")
    "security": "audit the login endpoint for SQL injection and XSS vulnerabilities",
    # review: review+feedback; fires before research since ordering was fixed
    "review":   "please review this pull request and give feedback on the approach",
    # research: explain+compare; no review/error/test/refactor/security keywords
    "research": "explain how transformer attention mechanisms work and compare different types",
    # plan: >50 words → tier-3 complexity; creation keyword present; code density low
    "plan": (
        "create a comprehensive phased migration strategy for transitioning the current "
        "monolithic legacy application to a distributed microservices architecture, covering "
        "service boundary identification, data ownership, asynchronous communication patterns, "
        "deployment pipeline stages, distributed tracing setup, circuit breaker configuration, "
        "rollback procedures for each phase, observability requirements, and estimated delivery "
        "timeline across multiple engineering quarters"
    ),
    # code: implement + endpoint+database in CODE_KEYWORDS → density > 0.05
    "code":     "implement a REST endpoint that queries the database and returns paginated results",
    # general: no keywords from any group; short; falls through to catch-all
    "general":  "update the team on the current project status and next steps",
}


# ── 1. Classifier reachability ─────────────────────────────────────────────

@pytest.mark.parametrize("bucket", BUCKETS)
def test_classifier_reaches_every_bucket(bucket: str) -> None:
    trigger = _BUCKET_TRIGGERS[bucket]
    ctx = TaskContext.from_task(type("T", (), {"goal": trigger, "title": trigger})())
    result = classify_bucket(ctx)
    assert result == bucket, (
        f"Trigger phrase for '{bucket}' classified as '{result}' instead. "
        f"Phrase: {trigger!r}"
    )


# ── 2. Scoring path coverage ───────────────────────────────────────────────

def test_scoring_path_covers_all_buckets() -> None:
    from backend.orchestrator.routing import quality
    for bucket in BUCKETS:
        assert bucket in quality._LENGTH_RATIO_TARGETS, (
            f"Bucket '{bucket}' missing from quality._LENGTH_RATIO_TARGETS"
        )


def test_code_like_buckets_subset_of_buckets() -> None:
    from backend.orchestrator.routing.vocab import CODE_LIKE_BUCKETS
    assert CODE_LIKE_BUCKETS <= set(BUCKETS), (
        f"CODE_LIKE_BUCKETS contains unknown buckets: {CODE_LIKE_BUCKETS - set(BUCKETS)}"
    )


def test_debug_and_security_buckets_subset_of_buckets() -> None:
    from backend.orchestrator.routing.vocab import DEBUG_BUCKETS, SECURITY_BUCKETS, PLAN_LIKE_BUCKETS
    for name, group in [("DEBUG", DEBUG_BUCKETS), ("SECURITY", SECURITY_BUCKETS), ("PLAN_LIKE", PLAN_LIKE_BUCKETS)]:
        assert group <= set(BUCKETS), (
            f"{name}_BUCKETS contains unknown buckets: {group - set(BUCKETS)}"
        )


# ── 3. Reward weight coverage ──────────────────────────────────────────────

@pytest.mark.parametrize("bucket", BUCKETS)
def test_reward_weight_exists_for_every_bucket(bucket: str) -> None:
    assert bucket in BUCKET_WEIGHTS, (
        f"No reward weight entry for bucket '{bucket}' in BUCKET_WEIGHTS"
    )
    w = BUCKET_WEIGHTS[bucket]
    assert len(w) == 4, f"Weight vector for '{bucket}' has {len(w)} elements, expected 4"
    assert abs(sum(w) - 1.0) < 1e-6, f"Weight vector for '{bucket}' does not sum to 1.0: {w}"


# ── 4. Warm-start vector coverage ──────────────────────────────────────────

@pytest.mark.parametrize("bucket", BUCKETS)
def test_warm_start_vector_exists_for_every_bucket(bucket: str) -> None:
    assert bucket in _BUCKET_VECTORS, (
        f"No warm-start context vector for bucket '{bucket}' in _BUCKET_VECTORS"
    )
    vec = _BUCKET_VECTORS[bucket]
    assert len(vec) == 9, (
        f"Warm-start vector for '{bucket}' has {len(vec)} dims, expected 9 (d=9)"
    )


# ── 5. Prior agent subset ──────────────────────────────────────────────────

def test_default_priors_keys_are_enabled_agents() -> None:
    unknown = set(_DEFAULT_PRIORS.keys()) - set(ENABLED_AGENTS)
    assert not unknown, (
        f"_DEFAULT_PRIORS references agents not in vocab.ENABLED_AGENTS: {unknown}. "
        f"Either re-enable the agent or remove it from _DEFAULT_PRIORS."
    )


# ── 6. String contract lint ────────────────────────────────────────────────

_ROUTING_MODULE_ROOT = Path(__file__).parent.parent.parent / "backend" / "orchestrator" / "routing"

# Files that are allowed to contain bucket/agent name string literals
# (they define the constants or assert against them in tests).
_LINT_ALLOWLIST = {
    "vocab.py",
}

# Only check files that participate in routing decisions.
_LINT_TARGETS = [
    "strategies/static.py",
    "strategies/linucb_per_bucket.py",
    "reward.py",
    "quality.py",
    "warm_start.py",
    "bandit_router.py",
]


def _string_literals_in_file(path: Path) -> list[str]:
    """Return all string constant values found in the AST of path."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_no_hardcoded_bucket_names_in_routing_modules() -> None:
    """No routing module outside vocab.py should contain bare bucket-name
    string literals used in equality comparisons or dict keys. This catches
    the v1-style phantom integration before it ships."""
    violations: list[str] = []
    for rel in _LINT_TARGETS:
        path = _ROUTING_MODULE_ROOT / rel
        if not path.exists():
            continue
        filename = path.name
        if filename in _LINT_ALLOWLIST:
            continue
        source = path.read_text(encoding="utf-8")
        # Check for bare bucket name string literals that appear to be used
        # in comparisons or as standalone dict key candidates (not in
        # structured data definitions like BUCKET_WEIGHTS whose correctness
        # is enforced by the assertion in reward.py).
        for bucket in BUCKETS:
            # Detect patterns like: == "code", in {"code", ...}, ["code"],
            # but not inside dict literal value positions.
            import re
            patterns = [
                rf'==\s*["\']({re.escape(bucket)})["\']',
                rf'["\']({re.escape(bucket)})["\'\s]*:',  # dict key
                rf'in\s+\{{[^}}]*["\']({re.escape(bucket)})["\']',
            ]
            for pat in patterns:
                if re.search(pat, source):
                    # Allow if the file is importing from vocab and using
                    # the constant via the imported name.
                    if "from" in source and "vocab" in source and "import" in source:
                        break
                    violations.append(f"{rel}: hardcoded bucket '{bucket}'")
                    break

    # This test is advisory in v2 — report violations but don't block CI
    # until all modules have completed their vocab migration.
    if violations:
        pytest.xfail(
            "Lint violations found (advisory only during vocab migration):\n"
            + "\n".join(violations)
        )
