"""
Quality scoring for task outputs.

Additive score across four layers. Each layer contributes a [0, 1] signal
weighted per bucket. A response can score well because it's long, coherent,
novel relative to the prompt, and sized appropriately. It scores poorly when
any layer trips — the same output doesn't need to fail on all of them.

Layer 1 — Novelty ratio
    Fraction of response tokens that don't appear in the prompt, after
    stopword removal. Catches "plan-restates-prompt" responses where the
    model just rephrases the question as steps. A real answer introduces
    new vocabulary (e.g. "sparse gating", "top-k", "load balancing" for
    an MoE question), while a plan recycles the prompt's words.

Layer 2 — Bucket-specific structural checks
    Code bucket: existing syntax-aware score. Plan bucket: numbered lists
    are the answer, no penalty. Research / general / review: penalise
    output that's >60% numbered lines with short bullets — that's a plan
    masquerading as an answer.

Layer 3 — Embedding similarity
    Cosine similarity between prompt and response embeddings via Ollama's
    nomic-embed-text. Treated as a band (~0.35–0.80 ideal) rather than
    "higher is better" — extreme similarity usually means the response
    paraphrases the prompt rather than answering it. Falls back silently
    to the structural-only score when embeddings are unavailable.

Layer 4 — Length ratio vs bucket expectation
    A research prompt expects a substantive answer; a plan can be concise.
    Expected response-to-prompt word ratio varies by bucket. Short answers
    to research prompts get dragged down; the dial isn't sensitive enough
    to hurt naturally-concise code responses.

LLM judging (Layer 5 from the design doc) is intentionally not included —
the user prefers zero-API-cost signal.
"""
from __future__ import annotations
import ast
import math
import re
import statistics

import httpx


# ── Infrastructure ──────────────────────────────────────────────────────────

_OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
_EMBED_MODEL = "nomic-embed-text"

# Buckets whose "answer is a plan" — no structural penalty applied here.
_PLAN_LIKE_BUCKETS: frozenset[str] = frozenset({"plan"})

# Buckets that expect code; structural check uses syntax/blocks instead of prose.
_CODE_BUCKETS: frozenset[str] = frozenset({"code", "test", "refactor", "debug", "security"})

# Expected response-to-prompt word ratio by bucket. Tunable; these are
# conservative starting points that shouldn't hurt reasonable answers.
_LENGTH_RATIO_TARGETS: dict[str, float] = {
    "research": 10.0,
    "general":   8.0,
    "review":    5.0,
    "code":      3.0,
    "plan":      2.0,
    "test":      3.0,
    "refactor":  3.0,
    "debug":     4.0,
    "security":  5.0,
}

# Stopwords — stripped before computing novelty so "the / and / of" don't
# dominate the set intersection.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "of", "to", "in", "on", "at", "for", "with", "by",
    "from", "as", "it", "this", "that", "these", "those", "how", "what",
    "why", "when", "where", "which", "who", "whom", "whose", "can", "could",
    "should", "would", "will", "may", "might", "must", "do", "does", "did",
    "has", "have", "had", "i", "you", "we", "they", "he", "she",
    "not", "no", "yes", "if", "then", "else", "so", "than",
    "please", "using", "use", "like", "about", "into", "over", "out", "off",
})

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_NUMBERED_LINE_RE = re.compile(r"^\s*\d+[.)]\s")

_MIN_WORDS = 5


# ── Layer 1 — novelty ratio ─────────────────────────────────────────────────

def _content_tokens(text: str) -> set[str]:
    return {
        w.lower()
        for w in _WORD_RE.findall(text)
        if w.lower() not in _STOPWORDS
    }


def _novelty_ratio(prompt: str, output: str) -> float:
    """Fraction of output content tokens that aren't already in the prompt."""
    p = _content_tokens(prompt)
    r = _content_tokens(output)
    if not r:
        return 0.0
    novel = r - p
    return len(novel) / len(r)


# ── Layer 2 — structural checks ─────────────────────────────────────────────

def _score_code(output: str) -> float:
    """Structural quality score for code outputs. Unchanged from v1."""
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
    if 10 <= len(words) <= 500:
        score += 0.1
    elif len(words) > 500:
        score += 0.05
    if "```" in output or re.search(r"^\s{4}", output, re.MULTILINE):
        score += 0.1
    return min(score, 1.0)


def _score_prose_structural(output: str) -> float:
    """Structural score for prose/text outputs: length, sentences, vocab,
    light structure bonus. Deliberately *doesn't* apply the plan-detection
    penalty — that's Layer 2's dedicated signal below."""
    if not output.strip():
        return 0.0
    words = output.split()
    if len(words) < _MIN_WORDS:
        return 0.1
    score = 0.4
    # Length reward, plateau at ~300 words
    score += 0.25 * min(len(words) / 300.0, 1.0)
    # Sentence structure
    sentences = re.split(r"[.!?]+", output.strip())
    valid_sentences = [s.strip() for s in sentences if len(s.strip().split()) >= 3]
    if valid_sentences:
        score += 0.15
    # Vocabulary diversity
    vocab_diversity = len(set(w.lower() for w in words)) / max(len(words), 1)
    score += 0.10 * min(vocab_diversity * 2, 1.0)
    # Light structure bonus (list/header)
    if re.search(r"^[-*•]\s|\d+\.\s|^#{1,3}\s", output, re.MULTILINE):
        score += 0.10
    return min(score, 1.0)


def _looks_like_plan(output: str) -> bool:
    """True if the output is dominated by short numbered lines — the shape
    of a plan/outline rather than a substantive answer."""
    lines = [ln.strip() for ln in output.split("\n") if ln.strip()]
    if len(lines) < 3:
        return False
    numbered = [ln for ln in lines if _NUMBERED_LINE_RE.match(ln)]
    if len(numbered) / len(lines) < 0.6:
        return False
    # Average words per numbered line — real structured answers have
    # longer explanatory bullets (e.g. "1. Sparse gating selects top-k
    # experts per token..."), plans have imperative stubs.
    avg_words = statistics.mean(len(ln.split()) for ln in numbered)
    return avg_words < 18


# ── Layer 4 — length ratio ──────────────────────────────────────────────────

def _length_score(prompt: str, output: str, bucket: str) -> float:
    """Response-to-prompt word ratio, compared against bucket expectation.

    Returns 1.0 if the response meets or exceeds the target ratio, scaled
    down linearly as it gets shorter. Doesn't punish long responses — a
    verbose-but-coherent answer still scores 1.0.
    """
    p_words = len(prompt.split())
    r_words = len(output.split())
    if p_words == 0:
        return 0.5
    target = _LENGTH_RATIO_TARGETS.get(bucket, 5.0)
    actual = r_words / p_words
    return min(actual / target, 1.0)


# ── Layer 3 — embedding similarity (banded) ────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


async def _embed(text: str) -> list[float] | None:
    """Single embedding via Ollama. Returns None on any failure (network,
    model not pulled, etc.) so the scorer degrades gracefully to the
    structural-only path."""
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


async def _similarity_band_score(prompt: str, output: str) -> float | None:
    """Band-scored similarity: 1.0 in the 0.35–0.80 sweet spot, falling off
    for paraphrases (too similar) and off-topic answers (too dissimilar).
    Returns None if embeddings are unavailable.
    """
    p = await _embed(prompt)
    if p is None:
        return None
    r = await _embed(output)
    if r is None:
        return None
    raw = _cosine(p, r)            # cosine in [-1, 1]
    sim = (raw + 1.0) / 2.0        # rescale to [0, 1]
    if 0.35 <= sim <= 0.80:
        return 1.0
    if sim < 0.35:
        return max(0.0, sim / 0.35)
    return max(0.0, (1.0 - sim) / 0.20)


# ── Combined scorer ──────────────────────────────────────────────────────────

# Component weights for prose buckets. Code bucket uses its own path.
#   - Structural (length, vocab, sentences)   0.35
#   - Novelty ratio                           0.25
#   - "Not plan" (binary: 0 if a plan, else 1) 0.20
#   - Length ratio vs bucket expectation      0.10
#   - Embedding band (if available)           0.10
# When embeddings aren't available the 0.10 redistributes to structural.
_W_STRUCTURAL = 0.35
_W_NOVELTY    = 0.25
_W_NOT_PLAN   = 0.20
_W_LENGTH     = 0.10
_W_EMBED      = 0.10


def _prose_components(prompt: str, output: str, bucket: str) -> dict[str, float]:
    """Compute each layer's contribution for prose buckets. Exposed so an
    offline validation script can show per-layer breakdowns."""
    structural = _score_prose_structural(output)
    novelty = _novelty_ratio(prompt, output)
    length = _length_score(prompt, output, bucket)
    not_plan = 1.0
    if bucket not in _PLAN_LIKE_BUCKETS and _looks_like_plan(output):
        not_plan = 0.0
    return {
        "structural": structural,
        "novelty": novelty,
        "length": length,
        "not_plan": not_plan,
    }


def score_heuristic(prompt: str, output: str, bucket: str = "general") -> float:
    """Synchronous heuristic score (no embedding call). Usable offline."""
    if bucket in _CODE_BUCKETS:
        return _score_code(output)

    if not output.strip():
        return 0.0

    c = _prose_components(prompt, output, bucket)
    score = (
        (_W_STRUCTURAL + _W_EMBED) * c["structural"]
        + _W_NOVELTY * c["novelty"]
        + _W_NOT_PLAN * c["not_plan"]
        + _W_LENGTH * c["length"]
    )
    return round(min(score, 1.0), 4)


async def score_quality(prompt: str, output: str, bucket: str = "general") -> float:
    """Full quality score including embedding band when available."""
    if bucket in _CODE_BUCKETS:
        return round(_score_code(output), 4)

    if not output.strip():
        return 0.0

    c = _prose_components(prompt, output, bucket)
    embed = await _similarity_band_score(prompt, output)
    if embed is None:
        score = (
            (_W_STRUCTURAL + _W_EMBED) * c["structural"]
            + _W_NOVELTY * c["novelty"]
            + _W_NOT_PLAN * c["not_plan"]
            + _W_LENGTH * c["length"]
        )
    else:
        score = (
            _W_STRUCTURAL * c["structural"]
            + _W_NOVELTY * c["novelty"]
            + _W_NOT_PLAN * c["not_plan"]
            + _W_LENGTH * c["length"]
            + _W_EMBED * embed
        )
    return round(min(score, 1.0), 4)
