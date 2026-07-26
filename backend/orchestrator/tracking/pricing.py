# Prices per 1M tokens (USD) as of PRICING_AS_OF (methodology frozen for Phase 4 bench;
# claude-sonnet-5 has intro pricing of $2/$10 through 2026-08-31 — standard rates used here)
import logging

logger = logging.getLogger(__name__)

PRICING_AS_OF = "2026-07-26"

PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00, "cache_read": 0.10},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30},
    "claude-opus-4-6": {"input": 5.00, "output": 25.00, "cache_read": 0.50},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00, "cache_read": 0.50},
}


# Cache writes bill at a premium over the input rate: 1.25× for the 5-minute
# TTL, 2× for the 1-hour TTL. The CLI uses the 5-minute cache, and 1.25× is
# the conservative choice when the TTL is unknown.
CACHE_CREATION_MULTIPLIER = 1.25


def _lookup_prices(model: str) -> dict[str, float]:
    """Resolve a model ID to its price row: exact → longest prefix → sonnet.

    Dated IDs like `claude-sonnet-5-20260203` must resolve to `claude-sonnet-5`;
    a genuinely unknown model warns instead of silently pricing at sonnet rates.
    """
    prices = PRICING.get(model)
    if prices is not None:
        return prices
    prefix = max(
        (known for known in PRICING if model.startswith(known)),
        key=len,
        default=None,
    )
    if prefix is not None:
        return PRICING[prefix]
    logger.warning("unknown model %r — pricing at claude-sonnet-4-6 rates", model)
    return PRICING["claude-sonnet-4-6"]


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Calculate cost in USD for a single API call."""
    prices = _lookup_prices(model)
    cost = (
        (input_tokens / 1_000_000) * prices["input"]
        + (output_tokens / 1_000_000) * prices["output"]
        + (cache_read_tokens / 1_000_000) * prices["cache_read"]
        + (cache_creation_tokens / 1_000_000) * prices["input"] * CACHE_CREATION_MULTIPLIER
    )
    return round(cost, 6)


def resolve_cost(metrics_payload: dict) -> float:
    """Resolve real task cost from a worker `metrics` event payload.

    Prefers the worker-reported `cost_usd` (authoritative when the CLI/API
    returns it); falls back to computing from token counts when the payload
    names a model; 0.0 otherwise (local arms report tokens but no model).
    """
    cost = metrics_payload.get("cost_usd")
    if cost is not None:
        return float(cost)
    model = metrics_payload.get("model")
    input_tokens = int(metrics_payload.get("prompt_tokens") or 0)
    output_tokens = int(metrics_payload.get("tokens") or 0)
    if model and (input_tokens or output_tokens):
        return calculate_cost(
            model, input_tokens, output_tokens,
            int(metrics_payload.get("cache_read_tokens") or 0),
        )
    return 0.0


def format_cost(cost_usd: float, model_breakdown: dict[str, int] | None = None) -> str:
    """Format cost for display in response footer."""
    parts = [f"${cost_usd:.4f}"]
    if model_breakdown:
        detail = " | ".join(f"{m}: {t:,} tok" for m, t in model_breakdown.items() if t > 0)
        if detail:
            parts.append(f"({detail})")
    return " ".join(parts)
