# Prices per 1M tokens (USD) as of 2026-04
PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00, "cache_read": 0.08},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00, "cache_read": 1.50},
}


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """Calculate cost in USD for a single API call."""
    prices = PRICING.get(model, PRICING["claude-sonnet-4-6"])
    cost = (
        (input_tokens / 1_000_000) * prices["input"]
        + (output_tokens / 1_000_000) * prices["output"]
        + (cache_read_tokens / 1_000_000) * prices["cache_read"]
    )
    return round(cost, 6)


def format_cost(cost_usd: float, model_breakdown: dict[str, int] | None = None) -> str:
    """Format cost for display in response footer."""
    parts = [f"${cost_usd:.4f}"]
    if model_breakdown:
        detail = " | ".join(f"{m}: {t:,} tok" for m, t in model_breakdown.items() if t > 0)
        if detail:
            parts.append(f"({detail})")
    return " ".join(parts)
