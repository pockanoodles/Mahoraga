"""
verify_replay.py — offline execution-based ("verifiable") reward scoring.

Reads a gold benchmark bank (experiments/prompts_verifiable.jsonl, each row
carrying `tests`) plus a bench results JSONL (prompt_full + output_full +
actual_agent, as written by `orch bench run --output`), extracts the code from
each model output, runs it against the hidden tests in a subprocess, and
reports pass@1 per (bucket, agent). Zero new inference — it re-scores outputs
that already exist, the same offline pattern as reweight_replay / quality_replay.

Motivating question (2026-07-15): the composite reward can't separate the local
arms because success+cost are ~constant on a free, mostly-succeeding roster and
the heuristic quality scorer rewards elaboration, not correctness (blind-ranking
agreement 3/7; a separate LLM judge did worse, both length-biased — see
brain/state/findings.md Era 7). Execution-based scoring gives code/debug an
objective ground-truth axis: does the produced code actually pass hidden tests?
The comparison this tool renders — heuristic quality rank vs pass@1 rank, with a
deliberately-weak canary arm — is the evidence for whether that axis separates
arms the heuristic collapses together.

SECURITY NOTE: this executes model-generated code with only a wall-clock timeout
for isolation, the same posture as tools/code_exec.py. Intended for trusted local
model outputs on the curated bank, not arbitrary untrusted input.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..workers.postprocess import extract_code

_TIMEOUT_SECONDS = 30


@dataclass
class CaseResult:
    agent: str
    bucket: str
    prompt_id: str
    passed: bool
    empty_output: bool
    error: str  # first line of stderr on failure, "" on pass


def load_bank(path: Path) -> dict[str, dict]:
    """Map exact prompt text -> {bucket, tier, entrypoint, tests}.

    Keyed by prompt string because that is what a bench results row carries
    (`prompt_full`), letting us join outputs back to their hidden tests with
    no id plumbing through the serving path. `#` comment lines are skipped.
    """
    bank: dict[str, dict] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            if not row.get("prompt") or not row.get("tests"):
                continue
            bank[row["prompt"]] = {
                "bucket": row.get("bucket", "general"),
                "tier": row.get("tier"),
                "entrypoint": row.get("entrypoint"),
                "tests": row["tests"],
            }
    return bank


def load_results(path: Path) -> list[dict]:
    """Read a bench results JSONL into {prompt, agent, output, bucket} rows.

    Uses `prompt_full`/`output_full` (added to bench.py so full text is
    available offline); rows without a full prompt or an agent are skipped.
    A row whose task failed simply has an empty `output_full` and will score
    as a (correct) verifiable failure downstream.
    """
    rows: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            prompt = r.get("prompt_full") or r.get("prompt")
            agent = r.get("actual_agent") or r.get("requested_agent") or r.get("agent")
            if not prompt or not agent:
                continue
            rows.append({
                "prompt": prompt,
                "agent": agent,
                "output": r.get("output_full") or "",
                "bucket": r.get("bucket") or "general",
            })
    return rows


def run_case(output: str, tests: str, timeout: int = _TIMEOUT_SECONDS) -> tuple[bool, str]:
    """Extract code from `output`, append `tests`, run under python3.

    Passes iff the combined script exits 0 (every assert held). Returns
    (passed, first_stderr_line). Mirrors tools/code_exec.py's `python3 -c`
    semantics so this measures what a live execution-gate would see.
    """
    code = extract_code(output)
    script = code + "\n\n" + tests
    try:
        proc = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)
    if proc.returncode == 0:
        return True, ""
    err = (proc.stderr or "").strip().splitlines()
    return False, (err[-1] if err else "nonzero exit")


def evaluate(bank: dict[str, dict], results: list[dict]) -> tuple[list[CaseResult], int]:
    """Run every matched result against its hidden tests.

    Returns (case_results, unmatched_count). `unmatched_count` is results
    whose prompt isn't in the bank — a join-health signal, not a score.
    """
    cases: list[CaseResult] = []
    unmatched = 0
    for r in results:
        spec = bank.get(r["prompt"])
        if spec is None:
            unmatched += 1
            continue
        empty = not r["output"].strip()
        passed, err = run_case(r["output"], spec["tests"])
        cases.append(CaseResult(
            agent=r["agent"],
            bucket=spec["bucket"],
            prompt_id=spec.get("entrypoint") or r["prompt"][:32],
            passed=passed,
            empty_output=empty,
            error=err,
        ))
    return cases, unmatched


def summarize(cases: list[CaseResult], results: list[dict]) -> dict:
    """Aggregate pass@1 per (bucket, agent) and per agent, plus a mean
    heuristic quality per (bucket, agent) for side-by-side comparison.

    The heuristic column is computed from the raw outputs via the live
    `score_heuristic` scorer (lazy-imported so the execution path doesn't
    hard-depend on the embeddings stack). That is the whole point of this
    report: does pass@1 rank the arms differently than the heuristic does?
    """
    # pass@1 aggregation
    per: dict[tuple[str, str], dict] = {}
    for c in cases:
        d = per.setdefault((c.bucket, c.agent), {"n": 0, "passed": 0, "empty": 0})
        d["n"] += 1
        d["passed"] += int(c.passed)
        d["empty"] += int(c.empty_output)

    # heuristic quality per (bucket, agent) from raw outputs
    heuristic: dict[tuple[str, str], list[float]] = {}
    try:
        from .quality import score_heuristic
        for r in results:
            try:
                q = score_heuristic(r["prompt"], r["output"], r["bucket"])
            except Exception:
                continue
            heuristic.setdefault((r["bucket"], r["agent"]), []).append(q)
    except Exception:
        heuristic = {}

    buckets = sorted({b for b, _a in per})
    agents = sorted({a for _b, a in per})

    by_bucket: dict[str, dict] = {}
    for b in buckets:
        by_bucket[b] = {}
        for a in agents:
            d = per.get((b, a))
            if not d:
                continue
            hq = heuristic.get((b, a), [])
            by_bucket[b][a] = {
                "n": d["n"],
                "passed": d["passed"],
                "empty": d["empty"],
                "pass_rate": round(d["passed"] / d["n"], 4) if d["n"] else 0.0,
                "heuristic_quality": round(sum(hq) / len(hq), 4) if hq else None,
            }

    # per-agent overall
    overall: dict[str, dict] = {}
    for a in agents:
        rows = [c for c in cases if c.agent == a]
        n = len(rows)
        passed = sum(int(c.passed) for c in rows)
        hq_all = [q for (b, ag), qs in heuristic.items() if ag == a for q in qs]
        overall[a] = {
            "n": n,
            "passed": passed,
            "pass_rate": round(passed / n, 4) if n else 0.0,
            "heuristic_quality": round(sum(hq_all) / len(hq_all), 4) if hq_all else None,
        }

    rank_cmp = _rank_comparison(overall)
    return {
        "by_bucket": by_bucket, "overall": overall,
        "buckets": buckets, "agents": agents, "rank_comparison": rank_cmp,
    }


def _spearman(a: list[float], b: list[float]) -> Optional[float]:
    """Spearman rank correlation between two equal-length score lists.

    Returns None for fewer than 3 points (undefined / degenerate). Ties are
    handled by average ranks. This is the headline: does execution rank the
    arms the same way the heuristic quality score does?
    """
    if len(a) != len(b) or len(a) < 3:
        return None

    def _ranks(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        ranks = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1  # 1-based average rank for the tie group
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    ra, rb = _ranks(a), _ranks(b)
    n = len(a)
    d2 = sum((x - y) ** 2 for x, y in zip(ra, rb))
    return round(1 - (6 * d2) / (n * (n * n - 1)), 4)


def _rank_comparison(overall: dict[str, dict]) -> dict:
    """Compare the execution (pass@1) ranking to the heuristic-quality ranking.

    Surfaces Spearman rho and, more concretely, the *inversion* that matters:
    the most-correct arm's rank under the heuristic. A near-perfect arm that the
    heuristic buries is the sharpest evidence the heuristic doesn't track
    correctness. (The old single-'worst arm' check missed this — the divergence
    lives at the top of the ranking, not the bottom.)
    """
    arms = [a for a, v in overall.items() if v["heuristic_quality"] is not None and v["n"]]
    if len(arms) < 2:
        return {"rho": None, "note": "need >=2 arms with both metrics"}
    passes = [overall[a]["pass_rate"] for a in arms]
    heur = [overall[a]["heuristic_quality"] for a in arms]
    rho = _spearman(passes, heur)

    best_exec = max(arms, key=lambda a: overall[a]["pass_rate"])
    # heuristic rank of the most-correct arm (1 = highest heuristic quality)
    by_heur = sorted(arms, key=lambda a: overall[a]["heuristic_quality"], reverse=True)
    best_exec_heur_rank = by_heur.index(best_exec) + 1
    return {
        "rho": rho,
        "n_arms": len(arms),
        "best_by_exec": best_exec,
        "best_by_exec_pass_rate": overall[best_exec]["pass_rate"],
        "best_by_exec_heuristic_rank": best_exec_heur_rank,  # of len(arms)
        "best_by_heuristic": by_heur[0],
        "inverted": best_exec_heur_rank > 1,
    }
