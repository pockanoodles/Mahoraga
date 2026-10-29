"""The verdict: stay, split, or switch, the reason, and what would change it.

This is the break-even framework (ADR 2026-09-10, decision 4) as code. It
reads measured evidence (a task-weighted resolve rate with its interval) and
the user's situation, and answers the product question for them: is local
worth it for this work, on this machine?

What one task routed to local is worth, per month, for an API user:

    E(p) = p · cloud_cost_per_task  −  local_cost_per_attempt
                                    −  (1 − p) · triage_cost

Every local attempt costs electricity. A failed one also costs the
developer's time to notice it and hand the task to cloud. Review of a
*successful* diff is the same cost whichever side wrote it, so it cancels.
Only work that can wait for an overnight queue is eligible (the deferrable
share), because a local attempt takes minutes and the alternative takes
seconds.

A subscription user pays nothing per task, so per-task savings are zero.
What local can buy them is a lower tier (if the offloaded work brings their
usage under it) or headroom under the cap they're hitting.

The verdict is evaluated at the interval's ends as well as its point. It
calls "split" confidently only when the pessimistic end still pays, and
"stay" confidently only when the optimistic end doesn't. Anything in between
is reported as not yet decided, with the reason. A tool whose selling point
is honesty has to be able to say "you're not wasting money", so the tests
pin that it does.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.orchestrator.agentbench.grade import TEST_VERIFIED
from backend.orchestrator.agentbench.stats import Rate, time_bound

from .pricing import Plan

STAY, SPLIT, SWITCH = "stay", "split", "switch"
MIN_TASKS = 5  # below this an interval is too wide to decide anything
MIN_SAVING = 5.0  # $/mo: the smallest saving an API verdict calls a split, however small the bill
SAVING_SHARE = 0.05  # and at least this share of the monthly cloud bill


@dataclass(frozen=True)
class Evidence:
    rate: Rate
    minutes_per_attempt: float
    tier: str
    label: str  # what was measured, e.g. "aider:qwen3.5:latest / feedback, 3 repos"
    hardware: str = "unknown hardware"
    failed: int = 0  # attempts that didn't resolve
    timed_out: int = 0  # of those, the ones that ran out of time


@dataclass(frozen=True)
class Assumptions:
    """Everything the verdict assumes that wasn't measured. All of it is
    printed with the verdict, so a reader can disagree with a number rather
    than with a conclusion."""

    tasks_per_month: int
    deferrable_share: float = 0.25  # share of cloud tasks that can wait for an overnight queue
    triage_minutes: float = 3.0  # to see a local attempt failed and hand the task over
    hourly_usd: float = 50.0
    watts: float = 40.0  # machine draw during an attempt (resource meter can measure it)
    usd_per_kwh: float = 0.30
    usage_of_cap: float = 1.0  # subscription: share of the plan's cap you actually use

    def lines(self) -> list[str]:
        return [
            f"{self.tasks_per_month} cloud coding tasks a month",
            f"{self.deferrable_share:.0%} of them can wait for an overnight queue",
            f"{self.triage_minutes:g} min to triage a failed local attempt, at ${self.hourly_usd:g}/h",
            f"{self.watts:g} W while an attempt runs, at ${self.usd_per_kwh:g}/kWh",
        ]


@dataclass(frozen=True)
class Billing:
    """How the user pays for cloud agents now."""

    kind: str  # "api" or "subscription"
    monthly_usd: float
    plan: Plan | None = None
    lower: Plan | None = None  # the next tier down, for a subscription

    @classmethod
    def api(cls, monthly_usd: float) -> Billing:
        return cls("api", monthly_usd)

    @classmethod
    def subscription(cls, plan: Plan, lower: Plan | None) -> Billing:
        if plan.usd_per_month is None:
            raise ValueError(f"{plan.name} has no price")
        return cls("subscription", plan.usd_per_month, plan, lower)


@dataclass
class Verdict:
    call: str
    confident: bool
    reason: str
    evidence: Evidence
    assumptions: Assumptions
    billing: Billing
    monthly_usd: tuple[float, float, float] | None = None  # net at (lo, point, hi)
    sensitivity: list[str] = field(default_factory=list)


def attempt_cost(ev: Evidence, a: Assumptions) -> float:
    """The electricity one local attempt burns, in dollars."""
    return a.watts / 1000 * (ev.minutes_per_attempt / 60) * a.usd_per_kwh


def triage_cost(a: Assumptions) -> float:
    """The developer's time to see a local attempt failed and hand the task over."""
    return a.triage_minutes / 60 * a.hourly_usd


def decide(ev: Evidence, a: Assumptions, billing: Billing) -> Verdict:
    if ev.tier != TEST_VERIFIED:
        raise ValueError(f"v1 decides on test-verified evidence only, not {ev.tier!r}")
    if not 0 < a.deferrable_share <= 1 or a.tasks_per_month <= 0:
        raise ValueError("tasks_per_month must be > 0 and deferrable_share in (0, 1]")
    hw = (f"Measured on {ev.hardware}. Other hardware needs its own run, because the "
          "tier map's figures for it are projections from vendor numbers.")
    if ev.rate.tasks < MIN_TASKS:
        return Verdict(STAY, False,
                       f"Only {ev.rate.tasks} tasks were measured, which is too few to decide, "
                       f"so mine more tasks before trusting any call.",
                       ev, a, billing, sensitivity=[hw])
    if a.deferrable_share >= 0.9 and ev.rate.lo >= 0.8:
        return Verdict(SWITCH, True,
                       f"Local resolves at least {ev.rate.lo:.0%} of tasks and nearly all of "
                       "your work can wait for it.", ev, a, billing, sensitivity=[hw])
    if billing.kind == "api":
        v = _decide_api(ev, a, billing)
    else:
        v = _decide_subscription(ev, a, billing)
    if time_bound(ev.timed_out, ev.failed):
        # A timeout only lowers the rate, so a split or switch read off it
        # stands, while a stay may be the machine's speed talking.
        v.sensitivity.insert(0, f"{ev.timed_out} of {ev.failed} failed attempts ran out of "
                                "time; a longer --timeout or a smaller context measures the model")
        if v.call == STAY:
            v.confident = False
            v.reason = (f"{ev.timed_out} of {ev.failed} failed attempts ran out of time, so "
                        "the resolve rate is a floor set by this machine's speed. " + v.reason)
    v.sensitivity.append(hw)
    return v


def breakeven_rate(ev: Evidence, a: Assumptions, cloud_per_task: float) -> float:
    """The resolve rate at which a queued task pays to go local: E(p) = 0 in the
    module's formula, so p* = (local + triage) / (cloud + triage).

    Above 1 when a cloud task costs less than a local attempt's electricity,
    which reads as "no resolve rate pays". One formula in one place: the
    verdict's sensitivity line, the API and the site export all call this."""
    return (attempt_cost(ev, a) + triage_cost(a)) / (cloud_per_task + triage_cost(a))


def breakeven_cloud_cost(ev: Evidence, a: Assumptions) -> float | None:
    """The cloud price per task at which local pays at the measured rate:
    E(p) = 0 solved for the cloud cost. None when local solves nothing, since
    then no price pays."""
    p = ev.rate.point
    if p <= 0:
        return None
    return (attempt_cost(ev, a) + (1 - p) * triage_cost(a)) / p


def materiality(b: Billing) -> float:
    """The smallest monthly saving an API verdict calls a split: 5% of the bill,
    and never under $5. A saving below it isn't worth changing how you work, so
    the verdict stays and its reason names the figure. The site export reads it
    from here, as it reads the break-even."""
    return max(MIN_SAVING, SAVING_SHARE * b.monthly_usd)


def _decide_api(ev: Evidence, a: Assumptions, b: Billing) -> Verdict:
    cloud = b.monthly_usd / a.tasks_per_month
    local, triage = attempt_cost(ev, a), triage_cost(a)
    queued = a.tasks_per_month * a.deferrable_share

    def net(p: float) -> float:
        return queued * (p * cloud - local - (1 - p) * triage)

    lo, mid, hi = net(ev.rate.lo), net(ev.rate.point), net(ev.rate.hi)
    material = materiality(b)
    p_star = breakeven_rate(ev, a, cloud)
    sens = [f"Pays once local resolves ≥ {p_star:.0%} of queued tasks "
            f"(measured {ev.rate.fmt()})."]
    c_star = breakeven_cloud_cost(ev, a)
    if c_star is not None:
        sens.append(f"At the measured rate it pays once a cloud task costs ≥ ${c_star:.2f} "
                    f"(yours: ${cloud:.2f}).")
    per = f"${cloud:.2f}/task"
    # A saving under the bar isn't worth changing how you work, so it reads as a
    # stay. Each reason the bar decides says what the bar is, and a loss is said
    # as what local costs more, never as a negative saving.
    bar = (f"5% of your cloud bill (${material:.0f}/mo)" if material > MIN_SAVING
           else f"${material:.0f}/mo")
    nets = (lo, mid, hi)
    if lo >= material:
        return Verdict(SPLIT, True, f"Even at the low end of the interval, sending the work that "
                       f"can wait to local saves at least ${lo:.0f}/mo at {per}.", ev, a, b, nets, sens)
    if mid >= material:
        low = (f"At the low end of the interval it loses ${-lo:.0f}/mo" if lo < 0 else
               f"The low end of the interval saves only ${lo:.0f}/mo, under {bar}")
        return Verdict(SPLIT, False, f"Local likely saves about ${mid:.0f}/mo at {per}. {low}, so "
                       "measure more before relying on it.", ev, a, b, nets, sens)
    under = f"That is under {bar}, the smallest saving that calls for a split."
    if hi < material:
        top = (f"local costs ${-hi:.0f}/mo more than cloud at {per}." if hi < 0 else
               f"local saves only ${hi:.0f}/mo at {per}. {under}")
        return Verdict(STAY, True, f"You're not wasting money. Even at the optimistic end of the "
                       f"interval, {top}", ev, a, b, nets, sens)
    now = (f"At the measured rate local costs ${-mid:.0f}/mo more than cloud at {per}." if mid < 0
           else f"At the measured rate local saves about ${mid:.0f}/mo at {per}. {under}")
    return Verdict(STAY, False, f"{now} The optimistic end of the interval reaches "
                   f"${hi:.0f}/mo.", ev, a, b, nets, sens)


def _decide_subscription(ev: Evidence, a: Assumptions, b: Billing) -> Verdict:
    plan, lower = b.plan, b.lower
    queued = a.tasks_per_month * a.deferrable_share
    run_cost = queued * attempt_cost(ev, a)

    def failed_cost(p: float) -> float:
        return queued * (1 - p) * triage_cost(a)

    sens: list[str] = []
    headroom = a.usage_of_cap >= 1 and a.deferrable_share * ev.rate.lo >= 0.10
    freed = f"{a.deferrable_share * ev.rate.point:.0%}"
    if lower is not None and plan is not None and plan.capacity_vs_pro and lower.capacity_vs_pro:
        used = a.usage_of_cap * plan.capacity_vs_pro
        p_down = (1 - lower.capacity_vs_pro / used) / a.deferrable_share
        saving = plan.usd_per_month - lower.usd_per_month

        def net(p: float) -> float:
            return (saving if p >= p_down else 0.0) - run_cost - failed_cost(p)

        nets = (net(ev.rate.lo), net(ev.rate.point), net(ev.rate.hi))
        if p_down <= 0:
            return Verdict(STAY, True, f"Your usage already fits {lower.name}, so you can drop a "
                           f"tier without local at all and save ${saving:.0f}/mo.", ev, a, b, None,
                           [f"Based on using {a.usage_of_cap:.0%} of {plan.name}'s cap."])
        sens.append(f"Dropping to {lower.name} (−${saving:.0f}/mo) needs local to take "
                    f"≥ {min(p_down, 1):.0%} of your queueable work (measured {ev.rate.fmt()}).")
        if p_down > 1:
            sens[-1] += " That's more than the queueable share allows, whatever the model."
        if ev.rate.lo >= p_down and nets[0] > 0:
            return Verdict(SPLIT, True, f"Local can take enough work to move you to {lower.name}, "
                           f"which saves about ${nets[1]:.0f}/mo.", ev, a, b, nets, sens)
        if ev.rate.point >= p_down and nets[1] > 0:
            return Verdict(SPLIT, False, f"At the measured rate you could move to {lower.name} and "
                           f"save about ${nets[1]:.0f}/mo, but the interval's low end can't, so "
                           "measure more first.", ev, a, b, nets, sens)
    if headroom:
        return Verdict(SPLIT, True, f"You hit your cap, and local would take about {freed} of your "
                       "tasks off it. That buys headroom under the cap and saves no money.",
                       ev, a, b, None, sens)
    reason = ("You're not wasting money, because on a flat plan each extra cloud task is free. "
              f"Local would free only about {freed} of your usage")
    reason += (", which matters only if you hit the cap." if a.usage_of_cap < 1
               else ", too little to relieve the cap.")
    return Verdict(STAY, True, reason, ev, a, b, None, sens)


def usd(x: float) -> str:
    return f"−${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def render(v: Verdict) -> str:
    sure = "" if v.confident else " (not yet decided, because the interval spans the threshold)"
    out = [f"VERDICT: {v.call.upper()}{sure}", "", v.reason, "",
           f"evidence: {v.evidence.label}, resolve {v.evidence.rate.fmt()} over "
           f"{v.evidence.rate.tasks} tasks ({v.evidence.rate.attempts} attempts), "
           f"{v.evidence.tier}, ~{v.evidence.minutes_per_attempt:.0f} min/attempt"]
    if v.billing.kind == "api":
        out.append(f"billing: API, ${v.billing.monthly_usd:.0f}/mo")
    else:
        p = v.billing.plan
        out.append(f"billing: {p.name} ${p.usd_per_month:.0f}/mo "
                   f"({'verified ' + p.as_of if p.verified else 'price supplied by you'})")
    if v.monthly_usd:
        lo, mid, hi = v.monthly_usd
        out.append(f"net per month: {usd(mid)} (interval {usd(lo)} to {usd(hi)})")
    out += ["", "what would change it:"] + [f"  - {s}" for s in v.sensitivity]
    out += ["", "assumed, not measured:"] + [f"  - {s}" for s in v.assumptions.lines()]
    if v.billing.kind == "subscription":
        out.append(f"  - you use {v.assumptions.usage_of_cap:.0%} of your plan's cap")
    return "\n".join(out)
