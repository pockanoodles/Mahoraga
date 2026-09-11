"""
cascade.py — the local→judge→escalate cascade, on the LIVE serving path.

`live_route.py` proved the cascade end to end on benchmarks: a free local arm
answers, a free local judge votes on the answer from (prompt, output) alone, and
only judged failures reach the cloud arm — 0.921 pass@1 at 23.5% of an
always-cloud policy's cost on HumanEval+ (findings.md Era 19). But that module
is bench-shaped: it takes the bank's hidden tests, grades every step against
them, and runs the cloud arm on kept-local prompts to measure a baseline. None
of that exists for organic traffic.

The transportable half is the gate — and the gate was *already running* in
production. `reward_judge.judge_correctness` grades every successful code-bucket
task on the serving path, and its verdict became the reward's correctness
coefficient (Era 23). What was missing is that a rejected answer was still the
answer the caller got. This module spends that verdict a second time: on a
reject, re-run the prompt on an escalation arm and serve *that* instead.

Two design constraints, both load-bearing:

1. **The escalation arm is outside the bandit's action space.** Enabling the
   cloud arm in agents.yaml would make it a policy arm — and an unexplored arm
   inflates its own UCB, so the bandit would start spending real money to
   explore it. The arm is built here, reachable only by a judge rejection, and
   stays `enabled: false` for the router.
2. **Escalation never re-attributes the outcome.** The bandit still observes the
   *local* arm's own output. Crediting the local arm with the escalation arm's
   answer would re-break the reward signal Era 23 fixed.

Recall is not 1.0 (0.688 reading-only, 0.784 with the generated-test check), so
some true local failures are still served, and some correct answers escalate
needlessly — the latter costs money, never quality. That asymmetry is the whole
reason this is safe to ship.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from .execution_gate import EXEC_GATE_BUCKETS

logger = logging.getLogger(__name__)

# Only code-like buckets escalate. The judge's prose profile is a permissive
# ref-accept 1.000 (findings Era 15/16) — it essentially never rejects a prose
# answer, so extending the cascade there would add latency and buy nothing.
# Mirrors the buckets the judge is actually measured on.
CASCADE_BUCKETS: frozenset[str] = EXEC_GATE_BUCKETS

_DEFAULT_CONFIG = Path(__file__).parents[3] / "agents.yaml"
_DEFAULT_DAILY_CAP = 25


def cascade_enabled() -> bool:
    """On by default; MAHORAGA_CASCADE=off|0|false|no disables it."""
    raw = os.getenv("MAHORAGA_CASCADE", "on").strip().lower()
    return raw not in ("0", "off", "false", "no")


def escalation_arm() -> str:
    """agents.yaml key for the escalation arm. Default the audited claude CLI.

    `claude-cli` bills through an interactive Claude subscription — the same
    quota pool as the session this cascade exists to relieve. `claude` bills an
    API key instead, which separates the two pools and is the right choice on a
    machine with no subscription. Both run the same model through the same
    prompt framing; a build failure (no key, no binary) degrades to serving the
    local answer rather than raising.
    """
    return os.getenv("MAHORAGA_ESCALATE_TO", "claude-cli").strip() or "claude-cli"


def daily_cap() -> int:
    """Max escalations per UTC day. <= 0 disables the cap.

    The judge is the only thing standing between organic traffic and a paid
    arm, and its precision is not perfect. The cap bounds what a bad judge day
    can spend before a human notices.
    """
    try:
        return int(os.getenv("MAHORAGA_ESCALATE_MAX_PER_DAY", str(_DEFAULT_DAILY_CAP)))
    except ValueError:
        return _DEFAULT_DAILY_CAP


class _DailyBudget:
    """Process-local escalation counter, keyed by UTC day.

    Deliberately in-memory: a daemon restart resets it. Persisting would mean a
    second source of truth for spend alongside the cost ledger, and the cap is a
    blast-radius guard, not an accounting record.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day = ""
        self._count = 0

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def try_spend(self, cap: int) -> bool:
        """Reserve one escalation; False when the day's cap is already spent."""
        if cap <= 0:
            return True
        with self._lock:
            today = self._today()
            if today != self._day:
                self._day, self._count = today, 0
            if self._count >= cap:
                return False
            self._count += 1
            return True

    def refund(self) -> None:
        """Give back a reservation whose escalation never produced an answer."""
        with self._lock:
            if self._count > 0:
                self._count -= 1

    def spent_today(self) -> int:
        with self._lock:
            return self._count if self._today() == self._day else 0


_budget = _DailyBudget()
_worker: Any = None
_worker_arm: str = ""
_worker_failed = False


def _get_escalation_worker():
    """Lazily build the escalation arm from agents.yaml; None if unavailable.

    Cached per arm id so an env change between calls rebuilds rather than
    silently serving the old arm. A construction failure is remembered so a
    misconfigured roster logs once instead of on every rejected task.
    """
    global _worker, _worker_arm, _worker_failed
    arm = escalation_arm()
    if _worker is not None and _worker_arm == arm:
        return _worker
    if _worker_failed and _worker_arm == arm:
        return None
    try:
        from .live_route import build_cloud_worker

        cfg_path = Path(os.getenv("MAHORAGA_AGENTS_YAML", str(_DEFAULT_CONFIG)))
        cfg: dict[str, Any] = yaml.safe_load(cfg_path.read_text()) or {}
        if arm not in cfg:
            raise ValueError(f"escalation arm {arm!r} has no block in {cfg_path}")
        _worker = build_cloud_worker(cfg, arm)
        _worker_arm, _worker_failed = arm, False
        return _worker
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cascade: escalation arm %r unavailable (%s) — rejected answers will "
            "be served as-is", arm, exc,
        )
        _worker, _worker_arm, _worker_failed = None, arm, True
        return None


def should_escalate(
    correctness: Optional[float], bucket: str, *, exec_failed: bool = False
) -> bool:
    """Whether a task warrants the escalation arm.

    Two independent triggers, both meaning "the local answer is known bad":

    `exec_failed` — the execution gate could not run the output at all. This is
    the *harder* signal: code that does not compile is wrong deterministically,
    with none of the judge's false-positive risk. It also arrives on a path the
    judge never sees, because the gate flips the task to failed and the reward
    judge only runs on successes — so before this trigger existed, the answers
    most certain to need escalation were exactly the ones that skipped it.

    `correctness == 0.0` — the judge read the output and rejected it. Only an
    explicit reject counts: `None` is the judge abstaining (off, unavailable,
    or unparseable) and must stay a no-op, the same contract the reward path
    gives it, so turning the judge off can never start spending.
    """
    if not cascade_enabled() or bucket not in CASCADE_BUCKETS:
        return False
    return exec_failed or correctness == 0.0


async def escalate(prompt: str) -> tuple[Optional[str], float, str]:
    """Re-run `prompt` on the escalation arm; return (output, cost_usd, detail).

    A None output means the escalation did not happen or produced nothing, and
    the caller must serve the local answer unchanged. NEVER raises: a failure
    here degrades to the pre-cascade behaviour rather than failing the request.
    """
    cap = daily_cap()
    if not _budget.try_spend(cap):
        return None, 0.0, f"escalation skipped: daily cap {cap} reached"

    worker = _get_escalation_worker()
    if worker is None:
        _budget.refund()
        return None, 0.0, f"escalation skipped: arm {escalation_arm()!r} unavailable"

    try:
        from .live_route import run_worker

        output, cost, error = await run_worker(worker, prompt)
    except Exception as exc:  # noqa: BLE001
        _budget.refund()
        logger.warning("cascade: escalation call raised: %r", exc)
        return None, 0.0, f"escalation failed: {exc!r}"

    if error or not output.strip():
        _budget.refund()
        return None, float(cost or 0.0), f"escalation produced nothing: {error or 'empty output'}"

    return output, float(cost or 0.0), f"escalated to {escalation_arm()}"


def escalations_today() -> int:
    """How many escalations this process has spent on the current UTC day."""
    return _budget.spent_today()


def reset_budget_for_tests() -> None:
    """Test hook — clear the day counter and the cached arm."""
    global _worker, _worker_arm, _worker_failed
    _budget.__init__()  # type: ignore[misc]
    _worker, _worker_arm, _worker_failed = None, "", False
