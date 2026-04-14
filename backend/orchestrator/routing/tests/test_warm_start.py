import numpy as np
from backend.orchestrator.routing.strategies.linucb import LinUCBRouter
from backend.orchestrator.routing.warm_start import warm_start_from_matrix, bucket_context_vector, COMPATIBILITY_MATRIX_PATH


def test_bandit_router_auto_warm_starts_when_matrix_exists(tmp_path):
    """BanditRouter warm-starts from compatibility_matrix.json when bandit state is fresh."""
    import json
    from unittest.mock import patch
    matrix = {
        "ollama": {"code": 0.30, "general": 0.75},
        "aider":  {"code": 0.88, "general": 0.50},
    }
    matrix_path = tmp_path / "compatibility_matrix.json"
    matrix_path.write_text(json.dumps(matrix))

    import backend.orchestrator.routing.warm_start as ws_mod
    with patch.object(ws_mod, "COMPATIBILITY_MATRIX_PATH", matrix_path):
        from backend.orchestrator.routing.bandit_router import BanditRouter
        router = BanditRouter(strategy="linucb", state_path=tmp_path / "state.json")

    # After warm-start, aider should have higher code score than ollama
    from backend.orchestrator.routing.warm_start import bucket_context_vector
    x_code = bucket_context_vector("code")
    theta_aider  = np.linalg.solve(router.strategy.A["aider"],  router.strategy.b["aider"]).flatten()
    theta_ollama = np.linalg.solve(router.strategy.A["ollama"], router.strategy.b["ollama"]).flatten()
    assert float(x_code @ theta_aider) > float(x_code @ theta_ollama), \
        "aider should score higher than ollama on code bucket after warm-start"

def test_bandit_router_skips_warm_start_when_no_matrix(tmp_path):
    """BanditRouter does not crash when no compatibility_matrix.json exists."""
    import backend.orchestrator.routing.warm_start as ws_mod
    from unittest.mock import patch
    missing_path = tmp_path / "nonexistent.json"
    with patch.object(ws_mod, "COMPATIBILITY_MATRIX_PATH", missing_path):
        from backend.orchestrator.routing.bandit_router import BanditRouter
        router = BanditRouter(strategy="linucb", state_path=tmp_path / "state.json")
    # Router initialized normally, no arms warmed
    assert router.strategy.A == {}

def test_warm_start_shifts_theta():
    """After warm-start, θ̂ for aider in 'code' bucket should be higher than for ollama."""
    router = LinUCBRouter(d=9, alpha=1.0)
    matrix = {
        "aider":  {"code": 0.88, "research": 0.40, "general": 0.50},
        "ollama": {"code": 0.30, "research": 0.80, "general": 0.75},
    }
    warm_start_from_matrix(router, matrix, lambda_prior=1.0)

    x_code = bucket_context_vector("code")
    theta_aider  = np.linalg.solve(router.A["aider"],  router.b["aider"]).flatten()
    theta_ollama = np.linalg.solve(router.A["ollama"], router.b["ollama"]).flatten()
    score_aider  = float(x_code @ theta_aider)
    score_ollama = float(x_code @ theta_ollama)
    assert score_aider > score_ollama, (
        f"aider code score {score_aider:.4f} should exceed ollama {score_ollama:.4f} after warm-start"
    )

def test_warm_start_is_noop_when_matrix_empty():
    router = LinUCBRouter(d=9)
    warm_start_from_matrix(router, {})
    assert router.A == {} and router.b == {}

def test_inject_pseudo_obs_updates_A_and_b():
    router = LinUCBRouter(d=9)
    x = np.ones(9) * 0.5
    router.inject_pseudo_obs("ollama", x, reward=0.8, lambda_prior=1.0)
    # A should be identity + outer(x,x)
    expected_A = np.eye(9) + np.outer(x, x)
    # b includes the prior from _init_agent (prior * ones) + lambda * reward * x
    prior = router.priors.get("ollama", 0.5)
    x_col = x.reshape(-1, 1)
    expected_b = (prior * np.ones((9, 1))) + 1.0 * 0.8 * x_col
    np.testing.assert_allclose(router.A["ollama"], expected_A, rtol=1e-6)
    np.testing.assert_allclose(router.b["ollama"], expected_b, rtol=1e-6)
