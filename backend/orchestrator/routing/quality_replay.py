"""
quality_replay.py — offline quality-scorer discriminability experiment.

Motivating question (2026-07-09, difficulty-tier diagnostic): the heuristic
quality scorer showed no widening gap between qwen3.5 / granite4.1-8b /
qwen3-14b on hard vs. easy tasks, even though the raw outputs clearly differ
in depth (67-330 tokens of real technical content on hard tasks vs. 5-23 on
easy ones). Two explanations: (a) the models really are that similar, or
(b) the scorer's caps/plateaus compress genuinely different outputs into
similar scores regardless of source model.

This tool answers (b) directly: re-score already-captured (prompt, output,
bucket, agent) rows from a bench JSONL under several *variant* scorer
configs — same real text, different heuristic knobs (higher length
plateaus, uncapped keyword bonuses, continuous instead of binary
not-plan) — and report whether the per-bucket agent gap widens. If a
generous variant can't separate the same text that "baseline" couldn't,
the scorer isn't the bottleneck; the models (or the signal available to
any heuristic) are.

Mirrors reweight_replay.py's shape (load real logged data, recompute under
alternates, report per-bucket gap) but for the quality layer instead of
the reward-weight layer. No new inference — this re-scores text that was
already generated.
"""
from __future__ import annotations

import ast
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from .quality import (
    _CVE_RE,
    _CWE_RE,
    _MITIGATION_KEYWORDS,
    _NUMBERED_LINE_RE,
    _SECURITY_HEADER_RE,
    _THREAT_KEYWORDS,
    _LENGTH_RATIO_TARGETS,
    _MIN_WORDS,
    _novelty_ratio,
)
from .vocab import CODE_LIKE_BUCKETS, DEBUG_BUCKETS, PLAN_LIKE_BUCKETS, SECURITY_BUCKETS


@dataclass
class ScorerConfig:
    """Tunable knobs for the variant scorer. Defaults reproduce the
    production formula in quality.py exactly — `BASELINE` below."""

    name: str
    # Prose structural (_score_prose_structural equivalent)
    prose_length_plateau_words: float = 300.0
    # "plateau" = production behavior (linear then flat at
    # prose_length_plateau_words). "diminishing" = smooth diminishing
    # returns (1 - exp(-words/prose_length_diminishing_scale)) — additional
    # length always earns *something* but with sharply shrinking marginal
    # value, instead of a hard cliff.
    prose_length_curve: str = "plateau"
    prose_length_diminishing_scale: float = 150.0
    # Flat structure bonus (+0.10 in production) for any list/header match,
    # regardless of how much structure or whether it reflects new content.
    structure_bonus_weight: float = 0.10
    # Length-ratio layer: multiplies _LENGTH_RATIO_TARGETS (higher = harder
    # to saturate, i.e. requires a longer response relative to the prompt).
    length_target_multiplier: float = 1.0
    # Security keyword bonuses
    security_cwe_cap: float = 0.10
    security_cve_cap: float = 0.10
    security_mitigation_cap: float = 0.20
    security_mitigation_per_hit: float = 0.04
    security_threat_cap: float = 0.10
    security_threat_per_hit: float = 0.025
    # Code structural: word-count sweet-spot upper bound before the bonus
    # drops from +0.1 to +0.05 (production: 500).
    code_length_bonus_ceiling_words: float = 500.0
    # not_plan: binary (production) or continuous, scaled by how far over
    # the 60% numbered-line threshold and how short the average line is.
    not_plan_continuous: bool = False
    # If False, report the raw (possibly >1.0) sum instead of clamping —
    # useful for seeing true separation before it's compressed to [0,1].
    clamp_to_one: bool = True

    def label(self) -> str:
        return self.name


BASELINE = ScorerConfig(name="baseline")

# Generous variants, each isolating one suspected compression point.
VARIANTS: list[ScorerConfig] = [
    BASELINE,
    ScorerConfig(
        name="higher_length_plateau",
        prose_length_plateau_words=800.0,
        code_length_bonus_ceiling_words=1200.0,
    ),
    ScorerConfig(
        name="harder_length_target",
        length_target_multiplier=2.5,
    ),
    ScorerConfig(
        name="uncapped_security_keywords",
        security_cwe_cap=10.0,
        security_cve_cap=10.0,
        security_mitigation_cap=10.0,
        security_threat_cap=10.0,
        clamp_to_one=False,
    ),
    ScorerConfig(
        name="continuous_not_plan",
        not_plan_continuous=True,
    ),
    ScorerConfig(
        name="everything_generous",
        prose_length_plateau_words=800.0,
        length_target_multiplier=2.5,
        security_cwe_cap=10.0,
        security_cve_cap=10.0,
        security_mitigation_cap=10.0,
        security_threat_cap=10.0,
        code_length_bonus_ceiling_words=1200.0,
        not_plan_continuous=True,
        clamp_to_one=False,
    ),
]


# ── Variant scoring functions (parameterized reimplementations) ────────────
# These mirror quality.py's private functions but take a ScorerConfig. They
# intentionally do NOT import or mutate quality.py's module-level constants —
# this is a read-only offline experiment, not a change to production scoring.


def _score_code_variant(output: str, cfg: ScorerConfig) -> float:
    if not output.strip():
        return 0.0
    score = 0.5
    try:
        tree = ast.parse(output)
        score += 0.2
        has_func = any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            for n in ast.walk(tree)
        )
        has_class = any(isinstance(n, ast.ClassDef) for n in ast.walk(tree))
        if has_func or has_class:
            score += 0.1
    except SyntaxError:
        score += 0.05
    words = output.split()
    if 10 <= len(words) <= cfg.code_length_bonus_ceiling_words:
        score += 0.1
    elif len(words) > cfg.code_length_bonus_ceiling_words:
        score += 0.05
    if "```" in output or re.search(r"^\s{4}", output, re.MULTILINE):
        score += 0.1
    return score if not cfg.clamp_to_one else min(score, 1.0)


def _score_security_variant(output: str, cfg: ScorerConfig) -> float:
    text = output.strip()
    if not text:
        return 0.0
    word_count = len(text.split())
    if word_count < _MIN_WORDS:
        return 0.10

    score = 0.40
    if word_count < 30:
        score = 0.30

    cwes = {m.group(0).upper() for m in _CWE_RE.finditer(text)}
    cves = {m.group(0).upper() for m in _CVE_RE.finditer(text)}
    score += min(cfg.security_cwe_cap, 0.05 * len(cwes))
    score += min(cfg.security_cve_cap, 0.05 * len(cves))

    lower = text.lower()
    mitigation_hits = sum(1 for kw in _MITIGATION_KEYWORDS if kw in lower)
    score += min(cfg.security_mitigation_cap, cfg.security_mitigation_per_hit * mitigation_hits)

    threat_hits = sum(1 for kw in _THREAT_KEYWORDS if kw in lower)
    score += min(cfg.security_threat_cap, cfg.security_threat_per_hit * threat_hits)

    if _SECURITY_HEADER_RE.search(text):
        score += 0.10

    return round(score if not cfg.clamp_to_one else min(score, 1.0), 4)


def _score_prose_structural_variant(output: str, cfg: ScorerConfig) -> float:
    if not output.strip():
        return 0.0
    words = output.split()
    if len(words) < _MIN_WORDS:
        return 0.1
    score = 0.4
    if cfg.prose_length_curve == "diminishing":
        length_credit = 1.0 - math.exp(-len(words) / cfg.prose_length_diminishing_scale)
    else:
        length_credit = min(len(words) / cfg.prose_length_plateau_words, 1.0)
    score += 0.25 * length_credit
    sentences = re.split(r"[.!?]+", output.strip())
    valid_sentences = [s.strip() for s in sentences if len(s.strip().split()) >= 3]
    if valid_sentences:
        score += 0.15
    vocab_diversity = len(set(w.lower() for w in words)) / max(len(words), 1)
    score += 0.10 * min(vocab_diversity * 2, 1.0)
    if re.search(r"^[-*•]\s|\d+\.\s|^#{1,3}\s", output, re.MULTILINE):
        score += cfg.structure_bonus_weight
    return score if not cfg.clamp_to_one else min(score, 1.0)


def _not_plan_variant(output: str, bucket: str, cfg: ScorerConfig) -> float:
    """Binary (production) or continuous not-plan signal.

    Continuous mode: 1.0 minus a penalty proportional to (numbered-line
    ratio above 0.6) and (shortness of average numbered-line, relative to
    the 18-word threshold) — so a borderline plan-shaped answer is graded
    on a gradient instead of falling off a cliff at exactly 60%/18 words.
    """
    if bucket in PLAN_LIKE_BUCKETS:
        return 1.0
    lines = [ln.strip() for ln in output.split("\n") if ln.strip()]
    if len(lines) < 3:
        return 1.0
    numbered = [ln for ln in lines if _NUMBERED_LINE_RE.match(ln)]
    ratio = len(numbered) / len(lines)
    if ratio < 0.6:
        return 1.0
    avg_words = statistics.mean(len(ln.split()) for ln in numbered)

    if not cfg.not_plan_continuous:
        return 0.0 if avg_words < 18 else 1.0

    # Continuous: penalty scales with how far ratio exceeds 0.6 and how far
    # avg_words falls short of 18; clamps to [0, 1].
    ratio_penalty = min(1.0, (ratio - 0.6) / 0.4)
    length_penalty = min(1.0, max(0.0, (18.0 - avg_words) / 18.0))
    penalty = 0.5 * ratio_penalty + 0.5 * length_penalty
    return max(0.0, 1.0 - penalty)


def _length_score_variant(prompt: str, output: str, bucket: str, cfg: ScorerConfig) -> float:
    p_words = len(prompt.split())
    r_words = len(output.split())
    if p_words == 0:
        return 0.5
    target = _LENGTH_RATIO_TARGETS.get(bucket, 5.0) * cfg.length_target_multiplier
    actual = r_words / p_words
    return min(actual / target, 1.0)


_W_STRUCTURAL = 0.35
_W_NOVELTY = 0.25
_W_NOT_PLAN = 0.20
_W_LENGTH = 0.10
_W_EMBED = 0.10  # redistributed into structural — no embed call in this offline tool


def score_variant(prompt: str, output: str, bucket: str, cfg: ScorerConfig) -> float:
    """Score one (prompt, output, bucket) under cfg. No network calls —
    the embedding layer is intentionally excluded (offline, no inference)."""
    if bucket in CODE_LIKE_BUCKETS:
        return _score_code_variant(output, cfg)
    if bucket in DEBUG_BUCKETS:
        code_score = _score_code_variant(output, cfg)
        if code_score >= 0.7:
            return code_score
        prose = _score_prose_structural_variant(output, cfg)
        novelty = _novelty_ratio(prompt, output) if prompt else 0.0
        diag_keywords = (
            "issue", "cause", "root cause", "because", "bug",
            "fix", "fixed", "patch", "error", "exception",
            "traceback", "stack trace", "null", "race", "deadlock",
            "leak", "off-by-one", "regression",
        )
        lower = output.lower()
        diag_hits = sum(1 for kw in diag_keywords if kw in lower)
        diag_bonus = min(0.15, 0.04 * diag_hits)
        prose_combined = 0.55 * prose + 0.25 * novelty + diag_bonus
        if cfg.clamp_to_one:
            prose_combined = min(1.0, prose_combined)
        return max(code_score, prose_combined)
    if bucket in SECURITY_BUCKETS:
        return _score_security_variant(output, cfg)

    if not output.strip():
        return 0.0
    structural = _score_prose_structural_variant(output, cfg)
    novelty = _novelty_ratio(prompt, output)
    length = _length_score_variant(prompt, output, bucket, cfg)
    not_plan = _not_plan_variant(output, bucket, cfg)
    score = (
        (_W_STRUCTURAL + _W_EMBED) * structural
        + _W_NOVELTY * novelty
        + _W_NOT_PLAN * not_plan
        + _W_LENGTH * length
    )
    return round(score, 4) if not cfg.clamp_to_one else round(min(score, 1.0), 4)


# ── Loading real captured rows ──────────────────────────────────────────────


def load_rows(path: Path) -> list[dict]:
    """Load (prompt, output, bucket, agent) rows from a bench --output JSONL.

    Requires `prompt_full` and `output_full` fields (added to bench.py's
    `_run_one` alongside the pre-existing truncated `prompt`/`output_preview`
    — older bench JSONLs without these fields will yield an empty list, not
    a crash, so this fails loud-but-safe on stale data.
    """
    rows: list[dict] = []
    if not Path(path).exists():
        return rows
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "prompt_full" not in r or "output_full" not in r:
                continue
            if not r.get("success"):
                continue
            agent = r.get("actual_agent") or r.get("requested_agent")
            bucket = r.get("bucket") or "general"
            if not agent:
                continue
            rows.append({
                "agent": agent,
                "bucket": bucket,
                "prompt": r["prompt_full"],
                "output": r["output_full"],
                "tier": r.get("tier"),
                "tokens": r.get("tokens"),
            })
    return rows


def summarize(rows: list[dict], configs: list[ScorerConfig]) -> dict[str, dict]:
    """Score every row under every config; report per-bucket, per-agent
    mean score and the max-min gap across agents, for each config.

    Also reports, per config, the Pearson correlation between score and
    token count — a scorer that tracks depth should correlate positively;
    ~0 correlation under every config (not just baseline) means no
    heuristic reweighting fixes it and the bottleneck is elsewhere.
    """
    result: dict[str, dict] = {}
    for cfg in configs:
        scored = []
        for r in rows:
            s = score_variant(r["prompt"], r["output"], r["bucket"], cfg)
            scored.append({**r, "score": s})

        buckets = sorted({r["bucket"] for r in scored})
        per_bucket: dict[str, dict] = {}
        for bucket in buckets:
            in_bucket = [r for r in scored if r["bucket"] == bucket]
            agents = sorted({r["agent"] for r in in_bucket})
            avg = {
                a: round(
                    sum(r["score"] for r in in_bucket if r["agent"] == a)
                    / max(1, sum(1 for r in in_bucket if r["agent"] == a)),
                    4,
                )
                for a in agents
            }
            gap = (max(avg.values()) - min(avg.values())) if len(avg) > 1 else 0.0
            per_bucket[bucket] = {"n": len(in_bucket), "avg_by_agent": avg, "gap": round(gap, 4)}

        scores = [r["score"] for r in scored]
        tokens = [r["tokens"] for r in scored if r.get("tokens") is not None]
        corr = None
        if len(tokens) == len(scores) and len(scores) >= 2 and len(set(tokens)) > 1 and len(set(scores)) > 1:
            corr = round(statistics.correlation(scores, tokens), 4)

        result[cfg.name] = {
            "per_bucket": per_bucket,
            "overall_gap_avg": round(
                statistics.mean(b["gap"] for b in per_bucket.values()) if per_bucket else 0.0, 4
            ),
            "score_vs_tokens_corr": corr,
        }
    return result
