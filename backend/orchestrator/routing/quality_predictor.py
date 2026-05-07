"""
A3 — Learned quality scoring (offline trainer + scorer).

Spec: docs/semantic-routing.md §15 — "What A1 Unlocks: A3".

Today's quality score is a heuristic blend of structural / novelty /
length / not-plan / embed components (see `verifier/quality.py`). After
A1 lands the embedding infrastructure, we can train a tiny supervised
model that predicts task success from context + agent identity, using
the historical decisions DB as labelled data.

This module is intentionally minimal:

  - Trainer: hand-rolled logistic regression on (handcraft_9 ⊕ agent
    one-hot). No sklearn dependency. Numpy is already required.
  - Optional: append a few cosine-similarity features against
    bucket-centroid embeddings when they're cached. (Disabled by
    default to keep training dependency-free.)
  - Scorer: read-only inference, returns probability of success in
    [0, 1]. Exposed for callers (reward calc, escalation, dashboards)
    but NOT wired into the production reward path this session — that's
    a calibration question best answered with held-out data.

Persistence: ~/.mahoraga-v2/quality_predictor.json (a tiny JSON model).

CLI:
  orch quality train   — fit a model from the decisions DB
  orch quality eval    — held-out AUC + Spearman vs. heuristic
  orch quality predict — score (handcraft, agent) for a given task
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

import numpy as np

_log = logging.getLogger(__name__)

QUALITY_PREDICTOR_PATH = Path.home() / ".mahoraga-v2" / "quality_predictor.json"
DECISION_DB_PATH = Path.home() / ".mahoraga-v2" / "routing_decisions.db"

DEFAULT_ACCEPT_THRESHOLD = 0.7
DEFAULT_L2 = 0.5
DEFAULT_LR = 0.1
DEFAULT_ITERS = 400
HANDCRAFT_DIM = 9


# ── Data extraction ───────────────────────────────────────────────────────────


@dataclass
class TrainingRow:
    handcraft: np.ndarray  # length 9
    agent: str
    label: int  # 0/1
    raw_quality: Optional[float]
    raw_success: Optional[int]


def load_training_rows(
    db_path: Path = DECISION_DB_PATH,
    accept_threshold: float = DEFAULT_ACCEPT_THRESHOLD,
    require_outcome: bool = True,
) -> list[TrainingRow]:
    """Load labelled rows from the decisions DB.

    Label rule:
      - If quality_score is non-null: y = 1 iff quality_score >= accept_threshold.
      - Else if success is non-null: y = success (cast to int).
      - Else: skipped (no label).

    The decisions DB stores `context_vector` as JSON list of length 9
    (the handcraft features). Rows with malformed vectors are skipped.
    """
    if not Path(db_path).exists():
        raise FileNotFoundError(f"decisions DB not found at {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT context_vector, selected_agent, success, quality_score "
            "FROM decisions "
            "WHERE context_vector IS NOT NULL AND selected_agent IS NOT NULL"
        )
        rows: list[TrainingRow] = []
        for ctx_json, agent, success, quality in cur.fetchall():
            try:
                ctx_list = json.loads(ctx_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(ctx_list, list) or len(ctx_list) != HANDCRAFT_DIM:
                continue
            if quality is not None:
                label = 1 if float(quality) >= accept_threshold else 0
            elif success is not None:
                label = int(success)
            elif require_outcome:
                continue
            else:
                continue
            rows.append(
                TrainingRow(
                    handcraft=np.array(ctx_list, dtype=np.float32),
                    agent=str(agent),
                    label=int(label),
                    raw_quality=(float(quality) if quality is not None else None),
                    raw_success=(int(success) if success is not None else None),
                )
            )
        return rows
    finally:
        conn.close()


# ── Feature engineering ───────────────────────────────────────────────────────


def _onehot(agent: str, vocab: list[str]) -> np.ndarray:
    out = np.zeros(len(vocab), dtype=np.float32)
    if agent in vocab:
        out[vocab.index(agent)] = 1.0
    return out


def featurise(handcraft: np.ndarray, agent: str, agents: list[str]) -> np.ndarray:
    """Build the input feature vector: handcraft_9 ⊕ agent_onehot."""
    return np.concatenate([handcraft.astype(np.float32), _onehot(agent, agents)])


# ── Model ─────────────────────────────────────────────────────────────────────


@dataclass
class QualityModel:
    """A tiny logistic-regression predictor of P(success | task, agent).

    Persisted as JSON so future-Claude can poke at the weights directly.
    """
    weights: list[float]
    bias: float
    feature_names: list[str]
    agents: list[str]
    n_train: int
    n_features: int
    accept_threshold: float
    train_auc: float
    train_pos_rate: float
    train_loss: float
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "QualityModel":
        return cls(
            weights=list(d["weights"]),
            bias=float(d["bias"]),
            feature_names=list(d["feature_names"]),
            agents=list(d["agents"]),
            n_train=int(d["n_train"]),
            n_features=int(d["n_features"]),
            accept_threshold=float(d["accept_threshold"]),
            train_auc=float(d["train_auc"]),
            train_pos_rate=float(d["train_pos_rate"]),
            train_loss=float(d["train_loss"]),
            extra=dict(d.get("extra", {})),
        )

    def save(self, path: Path = QUALITY_PREDICTOR_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path = QUALITY_PREDICTOR_PATH) -> "QualityModel":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def predict_proba(self, handcraft: np.ndarray, agent: str) -> float:
        x = featurise(handcraft, agent, self.agents)
        z = float(np.dot(x, np.array(self.weights, dtype=np.float64))) + self.bias
        return _sigmoid(z)


def _sigmoid(z: float) -> float:
    z = max(min(z, 30.0), -30.0)
    return 1.0 / (1.0 + math.exp(-z))


def _sigmoid_arr(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def _binary_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Mann-Whitney AUC. Returns 0.5 if degenerate (single-class y)."""
    pos = p[y == 1]
    neg = p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    # Vectorised pairwise comparison.
    diff = pos[:, None] - neg[None, :]
    ties = (diff == 0).sum()
    wins = (diff > 0).sum()
    return float((wins + 0.5 * ties) / (len(pos) * len(neg)))


def _bce_loss(y: np.ndarray, p: np.ndarray) -> float:
    eps = 1e-9
    return float(-np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)))


# ── Trainer ───────────────────────────────────────────────────────────────────


def fit(
    rows: list[TrainingRow],
    *,
    l2: float = DEFAULT_L2,
    lr: float = DEFAULT_LR,
    iters: int = DEFAULT_ITERS,
    accept_threshold: float = DEFAULT_ACCEPT_THRESHOLD,
) -> QualityModel:
    """Fit a logistic regression on (handcraft ⊕ agent-onehot) → label.

    Returns a `QualityModel` with diagnostic stats. Throws if `rows` is
    empty or all-one-class (no signal to learn).
    """
    if not rows:
        raise ValueError("No training rows")

    agents = sorted({r.agent for r in rows})
    X = np.stack([featurise(r.handcraft, r.agent, agents) for r in rows]).astype(np.float64)
    y = np.array([r.label for r in rows], dtype=np.float64)

    if len(np.unique(y)) < 2:
        raise ValueError(
            f"Training data is all-one-class (label={y[0]:.0f}); cannot fit"
        )

    n, d = X.shape
    # Add bias column.
    Xb = np.hstack([X, np.ones((n, 1))])
    w = np.zeros(d + 1)

    for _ in range(iters):
        p = _sigmoid_arr(Xb @ w)
        grad = Xb.T @ (p - y) / n
        # L2 on weights (not bias).
        reg = l2 * np.concatenate([w[:-1], [0.0]]) / n
        w -= lr * (grad + reg)

    weights = w[:-1].tolist()
    bias = float(w[-1])

    p_train = _sigmoid_arr(Xb @ w)
    train_auc = _binary_auc(y, p_train)
    train_loss = _bce_loss(y, p_train)
    train_pos_rate = float(np.mean(y))

    feature_names = [f"handcraft_{i}" for i in range(HANDCRAFT_DIM)] + [
        f"agent::{a}" for a in agents
    ]

    return QualityModel(
        weights=weights,
        bias=bias,
        feature_names=feature_names,
        agents=agents,
        n_train=int(n),
        n_features=int(d),
        accept_threshold=accept_threshold,
        train_auc=train_auc,
        train_pos_rate=train_pos_rate,
        train_loss=train_loss,
        extra={
            "l2": l2,
            "lr": lr,
            "iters": iters,
        },
    )


# ── Eval ──────────────────────────────────────────────────────────────────────


@dataclass
class EvalReport:
    n_total: int
    n_train: int
    n_test: int
    train_auc: float
    test_auc: float
    test_loss: float
    test_pos_rate: float
    spearman_vs_quality: Optional[float]


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman rank correlation. Returns 0.0 if degenerate."""
    if len(a) < 2:
        return 0.0
    ra = _rank(a)
    rb = _rank(b)
    if np.std(ra) == 0 or np.std(rb) == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def _rank(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x)
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(len(x), dtype=np.float64)
    return ranks


def evaluate(
    rows: list[TrainingRow],
    *,
    test_frac: float = 0.25,
    seed: int = 42,
    accept_threshold: float = DEFAULT_ACCEPT_THRESHOLD,
    l2: float = DEFAULT_L2,
    lr: float = DEFAULT_LR,
    iters: int = DEFAULT_ITERS,
) -> tuple[QualityModel, EvalReport]:
    """Train/test split + fit + report. Used by `orch quality eval`."""
    rng = np.random.default_rng(seed)
    n = len(rows)
    if n < 4:
        raise ValueError(f"Need at least 4 labelled rows; got {n}")
    idx = np.arange(n)
    rng.shuffle(idx)
    n_test = max(1, int(round(n * test_frac)))
    test_idx = set(idx[:n_test].tolist())
    train_rows = [r for i, r in enumerate(rows) if i not in test_idx]
    test_rows = [r for i, r in enumerate(rows) if i in test_idx]

    model = fit(
        train_rows, l2=l2, lr=lr, iters=iters, accept_threshold=accept_threshold,
    )

    # Test set scoring.
    Xt = np.stack(
        [featurise(r.handcraft, r.agent, model.agents) for r in test_rows]
    ).astype(np.float64)
    yt = np.array([r.label for r in test_rows], dtype=np.float64)
    Xtb = np.hstack([Xt, np.ones((len(Xt), 1))])
    w = np.array(model.weights + [model.bias], dtype=np.float64)
    pt = _sigmoid_arr(Xtb @ w)

    test_auc = _binary_auc(yt, pt)
    test_loss = _bce_loss(yt, pt)
    test_pos_rate = float(np.mean(yt)) if len(yt) else 0.0

    quality_obs = np.array(
        [r.raw_quality for r in test_rows if r.raw_quality is not None],
        dtype=np.float64,
    )
    quality_pred = np.array(
        [
            float(np.dot(featurise(r.handcraft, r.agent, model.agents),
                         np.array(model.weights, dtype=np.float64)) + model.bias)
            for r in test_rows
            if r.raw_quality is not None
        ],
        dtype=np.float64,
    )
    spearman = (
        _spearman(quality_pred, quality_obs)
        if len(quality_obs) >= 4
        else None
    )

    report = EvalReport(
        n_total=n,
        n_train=len(train_rows),
        n_test=len(test_rows),
        train_auc=model.train_auc,
        test_auc=test_auc,
        test_loss=test_loss,
        test_pos_rate=test_pos_rate,
        spearman_vs_quality=spearman,
    )
    return model, report


# ── Convenience scorer ────────────────────────────────────────────────────────


_loaded_model: Optional[QualityModel] = None


def get_model(path: Path = QUALITY_PREDICTOR_PATH) -> Optional[QualityModel]:
    """Lazy-load the persisted predictor. Returns None if absent."""
    global _loaded_model
    if _loaded_model is not None:
        return _loaded_model
    if not Path(path).exists():
        return None
    try:
        _loaded_model = QualityModel.load(path)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        _log.warning("quality_predictor: failed to load %s (%s)", path, exc)
        _loaded_model = None
    return _loaded_model


def reset_loaded_model() -> None:
    """Clear the in-process cache (used after a fresh `orch quality train`)."""
    global _loaded_model
    _loaded_model = None


def predict_proba(handcraft: np.ndarray, agent: str) -> Optional[float]:
    model = get_model()
    if model is None:
        return None
    return model.predict_proba(handcraft, agent)
