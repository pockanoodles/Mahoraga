"""
Quality scoring for task outputs — Approach A (heuristics) + B (embedding similarity).

Approach A: structural heuristics — length, code validity, coherence.
Approach B: prompt-output cosine similarity via nomic-embed-text (Ollama).

Combined score: 0.6 * heuristic + 0.4 * similarity (when embeddings available).
Falls back to heuristic-only when Ollama embeddings are unavailable.
"""
from __future__ import annotations
import ast
import math
import re
import statistics

import httpx


_OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
_EMBED_MODEL = "nomic-embed-text"

# Minimum output length to be considered non-trivial
_MIN_WORDS = 5


# ── Approach A — heuristic scoring ───────────────────────────────────────────

def _score_code(output: str) -> float:
    """Structural quality score for code outputs."""
    if not output.strip():
        return 0.0

    score = 0.5  # baseline for non-empty output

    # Syntax validity (Python only — skip gracefully for other languages)
    try:
        tree = ast.parse(output)
        score += 0.2
        # Reward structural completeness: has functions/classes
        has_func = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree))
        has_class = any(isinstance(n, ast.ClassDef) for n in ast.walk(tree))
        if has_func or has_class:
            score += 0.1
    except SyntaxError:
        # Not valid Python — could be JS/TS/Go etc, don't penalise
        score += 0.05

    # Length sanity: too short is suspicious, too long is verbose
    words = output.split()
    if 10 <= len(words) <= 500:
        score += 0.1
    elif len(words) > 500:
        score += 0.05  # verbose but present

    # Has at least one code block marker or indentation
    if "```" in output or re.search(r"^\s{4}", output, re.MULTILINE):
        score += 0.1

    return min(score, 1.0)


def _score_text(output: str) -> float:
    """Structural quality score for chat/research/plan outputs."""
    if not output.strip():
        return 0.0

    words = output.split()
    if len(words) < _MIN_WORDS:
        return 0.1

    score = 0.4

    # Length reward: substantive responses score higher up to ~300 words
    word_count = len(words)
    length_score = min(word_count / 300.0, 1.0)
    score += 0.25 * length_score

    # Sentence structure: real sentences end in punctuation
    sentences = re.split(r'[.!?]+', output.strip())
    valid_sentences = [s.strip() for s in sentences if len(s.strip().split()) >= 3]
    if valid_sentences:
        score += 0.15

    # Vocabulary diversity: unique words / total words
    vocab_diversity = len(set(w.lower() for w in words)) / max(len(words), 1)
    score += 0.10 * min(vocab_diversity * 2, 1.0)  # scale up, plateau at 0.5 diversity

    # Presence of structure (lists, headers)
    if re.search(r'^[-*•]\s|\d+\.\s|^#{1,3}\s', output, re.MULTILINE):
        score += 0.10

    return min(score, 1.0)


def score_heuristic(prompt: str, output: str, bucket: str = "general") -> float:
    """Compute structural quality score for an output.

    Uses code-specific heuristics for code/test/refactor/debug buckets,
    text heuristics for everything else.
    """
    code_buckets = {"code", "test", "refactor", "debug", "security"}
    if bucket in code_buckets:
        return _score_code(output)
    return _score_text(output)


# ── Approach B — embedding similarity ────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


async def _embed(text: str) -> list[float] | None:
    """Get embedding vector from Ollama nomic-embed-text. Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                _OLLAMA_EMBED_URL,
                json={"model": _EMBED_MODEL, "input": text[:2000]},
            )
            if resp.status_code == 200:
                data = resp.json()
                embeddings = data.get("embeddings")
                if embeddings and embeddings[0]:
                    return embeddings[0]
    except Exception:
        pass
    return None


async def score_similarity(prompt: str, output: str) -> float | None:
    """Cosine similarity between prompt and output embeddings.

    Returns None if embeddings are unavailable (Ollama unreachable or model not pulled).
    """
    prompt_emb = await _embed(prompt)
    if prompt_emb is None:
        return None
    output_emb = await _embed(output)
    if output_emb is None:
        return None
    sim = _cosine(prompt_emb, output_emb)
    # Cosine similarity in [−1, 1] → rescale to [0, 1]
    return (sim + 1.0) / 2.0


# ── Combined scorer ──────────────────────────────────────────────────────────

async def score_quality(prompt: str, output: str, bucket: str = "general") -> float:
    """Combined quality score: 0.6 * heuristic + 0.4 * similarity.

    Falls back to heuristic-only (weight 1.0) when embeddings unavailable.
    """
    h = score_heuristic(prompt, output, bucket)
    s = await score_similarity(prompt, output)
    if s is None:
        return round(h, 4)
    return round(0.6 * h + 0.4 * s, 4)
