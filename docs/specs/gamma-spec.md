# Adaptive Per-Arm Discount Factor for dLinUCB

**Mahoraga v2 — Learning Dynamics Optimization**
**Status:** Spec / RFC
**Date:** 2026-05-15

---

## Problem

Mahoraga uses a global discount factor γ=0.98 across all arms in its dLinUCB bandit.
This is wrong for a heterogeneous agent pool:

- **Local models (ollama:qwen3-4b)** are pinned. Same weights, same quantization, same
  hardware. They don't drift. γ=0.98 is too aggressive — it erodes confidence in stable
  arms and wastes exploration budget re-checking things that haven't changed.

- **Cloud APIs (gemini-cli, codex-cli)** drift constantly. Provider-side model updates,
  rate limit changes, backend routing, capacity shifts. γ=0.98 might be too conservative —
  a degradation that happens over 10 tasks gets diluted by the previous 50 tasks of
  positive signal still in the matrix.

A global γ is a compromise that serves neither case well.

---

## Proposal: Per-Arm Adaptive γ via Prediction Error Tracking

Replace the global γ with a per-arm γ_a that adapts based on how well the bandit's
model of arm `a` matches observed rewards.

### Core Mechanism

For each arm `a`, maintain an exponential moving average (EMA) of squared prediction error:

```
ε_a,t = (r_t − x_t' θ_a)²                     # squared prediction error at time t
E_a,t = β · E_a,t-1 + (1 − β) · ε_a,t          # EMA of prediction error, β = 0.9
```

Map the error EMA to a discount factor:

```
γ_a,t = γ_min + (γ_max − γ_min) · exp(−E_a,t / τ)
```

Where:
- γ_min = 0.93  (fastest forgetting — arm is clearly drifting)
- γ_max = 0.995 (slowest forgetting — arm is stable, preserve confidence)
- τ = temperature controlling sensitivity (start with τ = 0.05, tune empirically)

**Behavior:**
- E_a ≈ 0 (predictions accurate) → γ_a → 0.995 → slow forgetting → exploit
- E_a large (predictions wrong) → γ_a → 0.93 → fast forgetting → re-explore

The bandit update becomes:

```
A_a ← γ_a · A_a + x_t · x_t'
b_a ← γ_a · b_a + r_t · x_t
```

Everything else is unchanged. The UCB computation, arm selection, exploration bonus
all work exactly as before — they just operate on A and b matrices that forget at
arm-specific rates.

---

## Implementation Surface

### New State Per Arm

```python
@dataclass
class ArmState:
    A: np.ndarray          # (d×d) — already exists
    b: np.ndarray          # (d×1) — already exists
    theta: np.ndarray      # (d×1) — already computed, cache it
    pred_error_ema: float  # new: EMA of squared prediction error
    gamma: float           # new: current discount factor for this arm
    pull_count: int        # already exists (or derivable)
```

### Update Path (pseudocode)

```python
def update_arm(arm: ArmState, x: np.ndarray, reward: float, config: GammaConfig):
    # 1. Prediction error
    predicted = x.T @ arm.theta
    error_sq = (reward - predicted) ** 2

    # 2. Update error EMA
    arm.pred_error_ema = config.beta * arm.pred_error_ema + (1 - config.beta) * error_sq

    # 3. Adapt gamma
    arm.gamma = config.gamma_min + (config.gamma_max - config.gamma_min) * \
                math.exp(-arm.pred_error_ema / config.tau)

    # 4. Discounted update (standard dLinUCB, now with per-arm gamma)
    arm.A = arm.gamma * arm.A + np.outer(x, x)
    arm.b = arm.gamma * arm.b + reward * x

    # 5. Recompute theta
    arm.theta = np.linalg.solve(arm.A, arm.b)
```

### Config

```python
@dataclass
class GammaConfig:
    gamma_min: float = 0.93
    gamma_max: float = 0.995
    beta: float = 0.9       # EMA smoothing for prediction error
    tau: float = 0.05        # temperature — how sensitive gamma is to error
    warmup: int = 10         # don't adapt gamma until arm has been pulled 10 times
```

---

## Where It Breaks

### 1. Cold Start / Low Pull Count

**Problem:** An arm pulled 3 times has a noisy θ estimate. Prediction error is high
not because the arm drifted, but because the model hasn't converged yet. Adaptive γ
would slam γ_a to 0.93 — fast forgetting — which makes convergence *even harder*.
A death spiral: bad estimate → high error → fast forgetting → lose the few observations
you had → worse estimate.

**Fix:** The `warmup` parameter. Don't adapt γ until the arm has been pulled at least
N times (default 10). During warmup, use γ_a = γ_default (0.98). This gives the
parametric model enough data to form a baseline estimate before drift detection kicks in.

**Residual risk:** 10 pulls might not be enough for a 9-dimensional context vector.
The A matrix needs ~d observations to become invertible in a meaningful sense. Monitor
the condition number of A during warmup — if it's still poorly conditioned at warmup
boundary, extend.

### 2. Reward Function Shift Looks Like Arm Drift

**Problem:** When the OLS reward learner updates the per-bucket weights (the w₁–w₄
in the composite reward), the reward function itself changes. An arm that was getting
r=0.8 under the old weights might get r=0.65 under the new weights for the *exact same
output quality*. The prediction error spikes — not because the arm drifted, but because
the reward changed.

Adaptive γ sees this as drift and starts forgetting. For every arm in that bucket
simultaneously. You get a system-wide exploration burst triggered by a reward
recalibration, not a real-world change.

**Fix:** Two options, not mutually exclusive:

**(a) Residualize against reward shift.** When OLS weights update, compute the expected
reward delta: Δr = x'(θ_new_weights − θ_old_weights). Subtract Δr from the prediction
error before feeding it into the EMA. This decomposes "the reward function changed"
from "the arm got worse."

**(b) Blended reward transition (from earlier discussion).** If OLS weights shift
gradually rather than discontinuously, prediction error from reward recalibration
is spread across many steps and stays below the drift-detection threshold.

Recommendation: implement (b) first because it solves the hard-cutoff problem
independently. Add (a) if empirical ablation shows reward shifts still cause
spurious exploration.

### 3. Correlated Drift Across Arms

**Problem:** If all cloud agents degrade simultaneously (e.g., shared API gateway issue,
network latency spike), every cloud arm's γ drops at once. The bandit mass-explores
local arms, which is actually correct behavior — but when the cloud recovers, every
cloud arm has been aggressively discounted. Recovery is slow because γ stays low
until prediction errors drop, but prediction errors can't drop until the bandit
pulls those arms again, and the bandit won't pull them because their θ estimates
are stale.

**Fix:** A recovery nudge. When an arm's γ has been at γ_min for more than K
consecutive updates (say K=20), force a single exploratory pull regardless of UCB
score. This breaks the stale-estimate loop. Alternatively, decay γ_a *back toward
γ_default* when the arm isn't being pulled — absence of evidence isn't evidence
of continued drift.

```python
# On every step, for arms NOT pulled this round:
arm.gamma = arm.gamma + recovery_rate * (gamma_default - arm.gamma)
# recovery_rate = 0.01 — slow drift back toward baseline
```

This way, an arm that hasn't been pulled in 50 steps has γ roughly back to default,
and its uncertainty (via A_a⁻¹) has grown enough to trigger natural exploration.

### 4. τ Sensitivity

**Problem:** The temperature τ in the gamma mapping function controls how much
prediction error it takes to move γ. Too low → γ whipsaws on normal variance.
Too high → γ barely responds to real drift. And the right τ depends on the
reward scale, which varies by bucket (because the OLS weights differ).

**Fix:** Normalize prediction error by the running variance of rewards for that arm
before feeding it into the gamma function:

```
ε_normalized = ε_a,t / (σ²_a + 1e-8)
```

where σ²_a is the running variance of observed rewards for arm a. Now τ is
scale-invariant — a prediction error of 2σ means the same thing regardless of
whether rewards live in [0.6, 0.9] or [0.3, 0.8].

### 5. Persistence Across Restarts

**Problem:** γ_a and pred_error_ema are runtime state. If the system restarts
(process crash, deploy, reboot), they reset to defaults. The bandit loses its
drift-detection memory. Post-restart, every arm looks stable (error EMA = 0),
γ goes to γ_max for everyone, and a mid-drift arm gets trusted again.

**Fix:** Serialize γ_a and pred_error_ema alongside A and b in the bandit's
persistence layer (you already persist A and b to `~/.mahoraga/`). On restart,
reload. If the state file is absent (fresh install), fall back to defaults —
the warm-start path from the compatibility matrix handles that case.

### 6. Interaction with Episodic Memory

**Problem (flagged in earlier discussion):** Episodic memory biases toward
historical winners at α=0.20 regardless of whether those episodes were scored
under a now-stale γ regime. An arm that adaptive γ is actively forgetting is
simultaneously being recommended by episodic memory.

**This spec doesn't fix that.** The episodic memory distance-weighting (α_effective
scaled by neighbor distance) is a separate optimization. But note that adaptive γ
makes the conflict *worse* — the bandit forgets faster for drifting arms, but episodic
memory doesn't know that. The two systems diverge more under adaptive γ than under
global γ.

**Dependency:** Implement distance-weighted episodic α (separate spec) before or
alongside adaptive γ to prevent the divergence from growing.

---

## Ablation Plan

Extend `orch benchmark ablation` with a synthetic drift scenario:

**Setup:** 200-task simulation. At task 80, degrade agent X's reward by 0.15
(simulates API degradation). At task 150, restore it (simulates recovery).

| Experiment | γ Strategy | Metric |
|------------|-----------|--------|
| baseline | global γ=0.98 | regret after changepoint, recovery time |
| adaptive-default | per-arm adaptive, τ=0.05 | same |
| adaptive-tuned | per-arm adaptive, τ from sweep | same |
| adaptive+recovery | per-arm adaptive + recovery nudge | same |
| adaptive+blended-reward | per-arm adaptive + OLS blending | same |

**Key metrics:**
- **Detection latency**: tasks between degradation and first re-route away from X
- **Regret-after-changepoint**: cumulative regret from task 80–200 vs oracle
- **Recovery time**: tasks between restoration and re-route back to X
- **Exploration overhead**: % of pulls on stable arms that were unnecessary

**Sweep grid for τ:**
```
τ ∈ [0.01, 0.02, 0.05, 0.10, 0.20]
γ_min ∈ [0.90, 0.93, 0.95]
γ_max ∈ [0.99, 0.995, 0.999]
warmup ∈ [5, 10, 20]
```

Save best config to `tuned_hyperparams.json` alongside existing bandit hyperparams.

---

## What's Novel vs. Known Technique

| Component | Status | Prior Art |
|-----------|--------|-----------|
| dLinUCB (discounted LinUCB) | Known | Garivier & Moulines 2011, Kocsis & Szepesvári 2006 |
| Per-arm adaptive γ | **Novel composition** | Restartable bandits (Besbes et al. 2014) use change detection but reset arms; we adapt γ continuously per arm |
| Prediction-error drift detection | Known concept | CUSUM, Page-Hinkley, ADWIN all do change detection; our EMA approach is simpler and fits the LinUCB update loop |
| γ mapping via error EMA | **Novel mechanism** | No prior work maps prediction error to a per-arm discount factor in contextual bandits (to our knowledge) |
| Reward-shift residualization | **Novel** | Addresses a problem unique to systems with learned reward functions inside bandits |
| Recovery nudge for correlated drift | Incremental | Related to forced exploration in mortal bandits (Chakrabarti et al.) |

The contribution isn't any single component — it's the composition applied to
heterogeneous agent routing where drift rates differ by arm type (local vs cloud).

---

## Open Questions

1. **Should γ adaptation be per-arm-per-bucket or just per-arm?** An agent might be
   stable on code tasks but drifting on research. Per-arm-per-bucket is more precise
   but multiplies state and slows convergence (fewer observations per cell).

2. **Is EMA the right error tracker?** ADWIN (Bifet & Gavalda 2007) detects
   distribution shifts more rigorously with statistical guarantees. EMA is simpler
   and cheaper. For a system running <1000 tasks/day, EMA is probably fine. Revisit
   if Mahoraga scales to high-throughput deployment.

3. **How does this interact with warm start?** The compatibility matrix injects
   pseudo-observations at startup. Those pseudo-observations set initial A and b.
   Should they also set initial pred_error_ema? Probably not — pseudo-observations
   don't have real prediction errors. Start error EMA at 0 (assume stable) and let
   it calibrate from real data.

---

## Implementation Order

1. Add `pred_error_ema` and `gamma` to arm state + persistence
2. Implement adaptive γ update with warmup guard
3. Add error variance normalization (§4 fix)
4. Wire into existing dLinUCB update path
5. Build drift-injection ablation scenario
6. Run sweep, write results
7. (Separate spec) Distance-weighted episodic α
8. (Separate spec) Blended OLS reward transition