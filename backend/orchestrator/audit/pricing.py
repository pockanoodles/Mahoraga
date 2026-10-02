"""Cloud plan prices, read from the dated observations in pricing.json.

Prices are data with a source and a date, not constants. A plan whose price
wasn't read from the vendor's own page is refused unless the caller supplies
the price, because a verdict built on a remembered price would be the kind of
number this project has already had to retract.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).with_name("pricing.json")


@dataclass(frozen=True)
class Plan:
    id: str
    vendor: str
    name: str
    kind: str  # "subscription"
    usd_per_month: float | None
    capacity_vs_pro: float | None  # the vendor's stated multiple of its base tier
    as_of: str
    source: str
    verified: bool
    note: str = ""


class UnverifiedPrice(ValueError):
    """The plan's price wasn't read from the vendor; the message says what to pass."""


def load(path: Path = DATA) -> dict[str, Plan]:
    rows = json.loads(path.read_text())["plans"]
    fields = Plan.__dataclass_fields__
    return {r["id"]: Plan(**{k: v for k, v in r.items() if k in fields}) for r in rows}


def plan(plan_id: str, price_override: float | None = None, path: Path = DATA) -> Plan:
    plans = load(path)
    if plan_id not in plans:
        raise KeyError(f"unknown plan {plan_id!r}; known: {', '.join(sorted(plans))}")
    p = plans[plan_id]
    if price_override is not None:
        return Plan(**{**p.__dict__, "usd_per_month": price_override, "verified": False,
                       "note": f"price supplied by the user ({price_override}); {p.note}"})
    if p.usd_per_month is None or not p.verified:
        raise UnverifiedPrice(f"{p.name}: price not verified ({p.note}) — pass --plan-price")
    return p


def lower_tier(p: Plan, path: Path = DATA) -> Plan | None:
    """The same vendor's next tier down by capacity, verified price only."""
    below = [q for q in load(path).values()
             if q.vendor == p.vendor and q.capacity_vs_pro is not None
             and p.capacity_vs_pro is not None and q.capacity_vs_pro < p.capacity_vs_pro
             and q.verified and q.usd_per_month is not None]
    return max(below, key=lambda q: q.capacity_vs_pro) if below else None
