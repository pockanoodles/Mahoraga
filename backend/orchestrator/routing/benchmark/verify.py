"""verify.py — recompute every published benchmark number from its artifact.

`bench repro` re-runs the benchmark: ~3.5 h, a 16 GB Mac, Ollama, and a
`claude` CLI. That is the right tool for "is this result real on my hardware",
and the wrong tool for "did the README drift from the data". This module is the
second question, and it costs a few milliseconds: every headline figure in the
README and in `docs/RESULTS.md` is recomputed from the committed per-case JSONL
it came from, then compared against the published value.

The comparison is deliberately literal. A claim records the number **as
printed** plus the decimal place it was printed to, and passes only when the
recomputed metric rounds to exactly that. So the assertion under test is not
"these are roughly similar" but "the digits in the README are the digits this
file yields" — which is the only property a reader actually relies on.

Consequences of that design worth knowing before adding a claim:

  - a claim whose artifact is missing FAILS; it never silently skips, because
    an unbacked published number is the exact thing this exists to catch;
  - a metric that cannot be computed (no true failures, so no recall) FAILS
    rather than defaulting, for the same reason;
  - claims live in data (`experiments/claims.json`), not here, so publishing a
    new number is a manifest edit and gets reviewed as one.

This runs in CI. If someone edits a headline number in the README without the
artifact to support it, the build goes red.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Optional

# Repo root: backend/orchestrator/routing/benchmark/verify.py -> parents[4]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CLAIMS = PROJECT_ROOT / "experiments" / "claims.json"


# ── metrics ──────────────────────────────────────────────────────────────────
#
# Field names are the live-route artifact's own schema. Anything absent is
# treated as "not computable" rather than zero: a run that never recorded cloud
# baselines must not report a 100% cost cut.

def _mean_flag(rows: list[dict], key: str) -> Optional[float]:
    if not rows or any(key not in r for r in rows):
        return None
    return sum(1 for r in rows if r.get(key)) / len(rows)


def _sum_cost(rows: list[dict], key: str) -> Optional[float]:
    if not rows or any(key not in r for r in rows):
        return None
    return sum(float(r.get(key) or 0.0) for r in rows)


def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    """Every published quantity, derived from one live-route artifact.

    Pure: no I/O, no clock, no config. Given the same rows it returns the same
    numbers on any machine, which is what makes the CI check meaningful.
    """
    n = len(rows)
    if n == 0:
        return {"n": 0}

    routed_cost = _sum_cost(rows, "total_cost")
    cloud_cost = _sum_cost(rows, "cloud_cost")

    # Recall is measured against ground truth (the hidden tests), not against
    # the judge's own confidence: of the answers that genuinely failed, what
    # fraction did the judge send to the cloud arm? That is the number the
    # cascade's quality actually rides on.
    true_failures = [r for r in rows if "local_passed" in r and not r["local_passed"]]
    if true_failures and all("escalated" in r for r in true_failures):
        judge_recall: Optional[float] = (
            sum(1 for r in true_failures if r.get("escalated")) / len(true_failures)
        )
    else:
        judge_recall = None

    metrics: dict[str, Optional[float]] = {
        "n": float(n),
        "routed_pass_at_1": _mean_flag(rows, "final_passed"),
        "cloud_pass_at_1": _mean_flag(rows, "cloud_passed"),
        "local_pass_at_1": _mean_flag(rows, "local_passed"),
        "escalation_rate": _mean_flag(rows, "escalated"),
        "judge_fail_recall": judge_recall,
        "judge_cost_total": _sum_cost(rows, "judge_cost"),
    }
    metrics["routed_cost_per_1k"] = None if routed_cost is None else routed_cost / n * 1000
    metrics["cloud_cost_per_1k"] = None if cloud_cost is None else cloud_cost / n * 1000
    # A cost cut is only meaningful against a baseline that actually cost
    # something; with no cloud baseline there is nothing to have reduced.
    if routed_cost is None or not cloud_cost:
        metrics["cost_reduction"] = None
    else:
        metrics["cost_reduction"] = 1.0 - (routed_cost / cloud_cost)
    return metrics


def _round_half_up(value: float, decimals: int) -> float:
    """Round the way a person writing a README rounds.

    Python's built-in `round` is half-to-even, so `round(0.6875, 3)` landing on
    0.688 is luck rather than intent. Publishing a number must not depend on
    which side of even it fell.
    """
    quant = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(value)).quantize(quant, rounding=ROUND_HALF_UP))


# ── claims ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricCheck:
    metric: str
    published: float
    decimals: int
    actual: Optional[float]
    ok: bool

    @property
    def detail(self) -> str:
        if self.actual is None:
            return "not computable from this artifact"
        shown = _round_half_up(self.actual, self.decimals)
        if self.ok:
            return f"{shown:.{self.decimals}f}"
        return (
            f"published {self.published:.{self.decimals}f}, "
            f"artifact yields {shown:.{self.decimals}f} (raw {self.actual!r})"
        )


@dataclass
class ClaimResult:
    claim_id: str
    description: str
    artifact: str
    published_in: list[str] = field(default_factory=list)
    checks: list[MetricCheck] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and all(c.ok for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "description": self.description,
            "artifact": self.artifact,
            "published_in": self.published_in,
            "ok": self.ok,
            "error": self.error,
            "checks": [
                {
                    "metric": c.metric,
                    "published": c.published,
                    "decimals": c.decimals,
                    "actual": c.actual,
                    "ok": c.ok,
                }
                for c in self.checks
            ],
        }


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL artifact, skipping blank lines."""
    rows: list[dict[str, Any]] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def verify_claim(claim: dict[str, Any], root: Path) -> ClaimResult:
    result = ClaimResult(
        claim_id=claim.get("id", "(unnamed)"),
        description=claim.get("description", ""),
        artifact=claim.get("artifact", ""),
        published_in=list(claim.get("published_in", [])),
    )
    artifact = root / result.artifact
    if not artifact.is_file():
        result.error = f"artifact not found: {result.artifact}"
        return result
    try:
        rows = load_rows(artifact)
    except (OSError, json.JSONDecodeError) as exc:
        result.error = f"artifact unreadable: {exc}"
        return result

    metrics = compute_metrics(rows)
    for name, spec in (claim.get("metrics") or {}).items():
        published = float(spec["value"])
        decimals = int(spec.get("decimals", 3))
        actual = metrics.get(name)
        ok = actual is not None and _round_half_up(actual, decimals) == _round_half_up(
            published, decimals
        )
        result.checks.append(
            MetricCheck(
                metric=name,
                published=published,
                decimals=decimals,
                actual=actual,
                ok=ok,
            )
        )
    return result


def verify_claims(
    claims_path: Path = DEFAULT_CLAIMS,
    root: Optional[Path] = None,
) -> list[ClaimResult]:
    """Verify every claim in the manifest. Raises only if the manifest itself
    is missing or malformed — a broken manifest is a bug, a broken claim is a
    finding."""
    root = root or claims_path.resolve().parent.parent
    manifest = json.loads(claims_path.read_text())
    return [verify_claim(c, root) for c in manifest.get("claims", [])]


# ── rendering ────────────────────────────────────────────────────────────────

_LABELS = {
    "n": "cases",
    "routed_pass_at_1": "routed pass@1",
    "cloud_pass_at_1": "always-cloud pass@1",
    "local_pass_at_1": "always-local pass@1",
    "routed_cost_per_1k": "routed $/1k tasks",
    "cloud_cost_per_1k": "always-cloud $/1k tasks",
    "cost_reduction": "cost reduction",
    "escalation_rate": "escalation rate",
    "judge_fail_recall": "judge fail-recall",
    "judge_cost_total": "judge cost (total)",
}


def render_verification(results: list[ClaimResult]) -> str:
    lines: list[str] = []
    if not results:
        lines.append("No claims in the manifest — nothing to verify.")
        return "\n".join(lines)

    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        lines.append(f"[{mark}] {r.claim_id} — {r.description}")
        lines.append(f"       artifact: {r.artifact}")
        if r.published_in:
            lines.append(f"       published in: {', '.join(r.published_in)}")
        if r.error:
            lines.append(f"       ERROR: {r.error}")
        for c in r.checks:
            label = _LABELS.get(c.metric, c.metric)
            sign = " " if c.ok else "!"
            lines.append(f"     {sign} {label:<24} {c.detail}")
        lines.append("")

    failed = [r for r in results if not r.ok]
    if failed:
        lines.append(
            f"{len(failed)} of {len(results)} claims FAILED — the published "
            "numbers no longer match their artifacts."
        )
    else:
        total_checks = sum(len(r.checks) for r in results)
        lines.append(
            f"All {len(results)} claims verified ({total_checks} metrics) "
            "against committed per-case results."
        )
    return "\n".join(lines)
