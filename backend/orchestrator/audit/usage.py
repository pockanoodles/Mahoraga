"""How you use cloud agents, saved once so the verdict doesn't ask again.

The verdict needs two things nothing on this machine can measure: how many
coding tasks you hand cloud agents a month, and how you pay for them. They're
asked once (by `mahoraga audit tonight`, or passed to `verdict`) and kept in
usage.json under the bench root. The file never leaves the machine.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.orchestrator.agentbench.bank import from_record

from . import pricing
from .verdict import Billing


@dataclass(frozen=True)
class Usage:
    tasks_per_month: int
    plan: str | None = None  # a plan id from pricing.json
    plan_price: float | None = None  # for a plan whose price isn't verified
    api_spend: float | None = None  # USD a month, for API billing

    def billing(self) -> Billing:
        """Raises ValueError (or KeyError for an unknown plan) with what to fix."""
        if self.tasks_per_month < 1:
            raise ValueError("tasks per month must be at least 1")
        if (self.api_spend is None) == (self.plan is None):
            raise ValueError("give exactly one of a plan or an API spend")
        if self.plan:
            p = pricing.plan(self.plan, self.plan_price)
            return Billing.subscription(p, pricing.lower_tier(p))
        return Billing.api(self.api_spend)

    def describe(self) -> str:
        how = (f"{self.plan}" + (f" at ${self.plan_price:g}" if self.plan_price is not None else "")
               if self.plan else f"${self.api_spend:g} a month of API")
        return f"{self.tasks_per_month} tasks a month, {how}"


def path(root: Path) -> Path:
    return Path(root) / "usage.json"


def load(root: Path) -> Usage | None:
    p = path(root)
    return from_record(Usage, json.loads(p.read_text())) if p.exists() else None


def save(root: Path, usage: Usage) -> Path:
    usage.billing()  # refuse to save what the verdict can't use
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(usage), indent=1) + "\n")
    return p


def parse_payment(answer: str) -> dict:
    """An answer to "how do you pay?": a plan id, a plan id and its price
    ("claude-max-20x 200"), or a monthly API spend ("150")."""
    words = answer.replace("$", "").split()
    if not words:
        raise ValueError("give a plan id or a monthly API spend")
    try:
        spend = float(words[0])
    except ValueError:
        spend = None
    if spend is not None:
        if len(words) > 1:
            raise ValueError("an API spend is one number, USD a month")
        return {"api_spend": spend}
    if len(words) > 2:
        raise ValueError("a plan id, optionally followed by its price")
    try:
        price = float(words[1]) if len(words) == 2 else None
    except ValueError:
        raise ValueError(f"{words[1]!r} isn't a price")
    return {"plan": words[0], "plan_price": price}


def judge(benches: list, arm: str, cond: str, usage: Usage, **assumed):
    """The verdict for one arm, from its attempts and how you use the cloud.
    `assumed` overrides the Assumptions defaults (deferrable share, rates)."""
    from .evidence import from_benches
    from .verdict import Assumptions, decide

    ev = from_benches(benches, arm, cond)
    return decide(ev, Assumptions(tasks_per_month=usage.tasks_per_month, **assumed),
                  usage.billing())
