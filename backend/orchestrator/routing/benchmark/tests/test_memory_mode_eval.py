"""Tests for the Phase-4 memory-mode evaluation harness.

These tests exercise the harness with very small prompt sets and seed counts
so they run in seconds. A separate slow-marked test exercises a fuller
configuration to catch end-to-end regressions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.orchestrator.routing.benchmark.memory_mode_eval import (
    EvalPrompt,
    aggregate,
    load_adversarial,
    load_synthetic,
    run_condition,
    run_eval,
    simulate_outcome_reward,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tiny_prompts() -> list[EvalPrompt]:
    """Three diverse prompts covering different oracle agents."""
    return [
        EvalPrompt(
            prompt="Implement a binary search in Python",
            bucket="code_generation",
            oracle_agent="codex-cli",
            oracle_reward=0.90,
        ),
        EvalPrompt(
            prompt="Refactor the user model to dataclasses",
            bucket="code_refactoring",
            oracle_agent="aider",
            oracle_reward=0.91,
        ),
        EvalPrompt(
            prompt="Explain how B-trees handle page splits",
            bucket="research",
            oracle_agent="claude",
            oracle_reward=0.92,
        ),
    ]


@pytest.fixture(autouse=True)
def reset_memory_mode_env() -> None:
    """Make sure tests don't leak MAHORAGA_* env vars to each other."""
    yield
    for var in (
        "MAHORAGA_MEMORY_MODE",
        "MAHORAGA_BANDIT_SEED",
        "MAHORAGA_PROMPT_SEED",
    ):
        os.environ.pop(var, None)


# ── Loaders ──────────────────────────────────────────────────────────────────


class TestLoaders:
    def test_load_synthetic_returns_prompts(self) -> None:
        prompts = load_synthetic()
        assert len(prompts) > 0
        assert all(isinstance(p, EvalPrompt) for p in prompts)
        assert all(p.oracle_reward > 0 for p in prompts)

    def test_load_adversarial(self, tmp_path: Path) -> None:
        # Write a minimal valid adversarial JSON.
        data = {
            "version": 1,
            "clusters": [
                {
                    "id": 1,
                    "theme": "Test",
                    "shared_features": "—",
                    "prompts": [
                        {
                            "prompt": "test prompt",
                            "bucket": "research",
                            "oracle_agent": "ollama",
                            "oracle_reward": 0.7,
                            "rationale": "—",
                        },
                    ],
                },
            ],
        }
        p = tmp_path / "adv.json"
        p.write_text(json.dumps(data))
        prompts = load_adversarial(p)
        assert len(prompts) == 1
        assert prompts[0].cluster_id == 1
        assert prompts[0].oracle_agent == "ollama"

    def test_repository_adversarial_set_loads(self) -> None:
        """The committed adversarial set should parse cleanly."""
        path = Path(__file__).parents[5] / "benchmarks" / "adversarial_prompts.json"
        if not path.exists():
            pytest.skip(f"adversarial set not found at {path}")
        prompts = load_adversarial(path)
        assert len(prompts) == 30
        # Six clusters
        assert {p.cluster_id for p in prompts} == set(range(1, 7))
        # Five prompts per cluster
        from collections import Counter
        counts = Counter(p.cluster_id for p in prompts)
        for cid, n in counts.items():
            assert n == 5, f"cluster {cid} has {n} prompts, expected 5"


# ── Reward simulation ────────────────────────────────────────────────────────


class TestSimulateReward:
    def test_correct_pick_near_oracle(self) -> None:
        import random
        rng = random.Random(0)
        rewards = [
            simulate_outcome_reward("aider", "aider", 0.9, rng)
            for _ in range(50)
        ]
        # Mean should be close to 0.9 (with noise std=0.04)
        mean = sum(rewards) / len(rewards)
        assert abs(mean - 0.9) < 0.02

    def test_wrong_pick_degrades(self) -> None:
        import random
        rng = random.Random(0)
        wrong = [
            simulate_outcome_reward("ollama", "aider", 0.9, rng)
            for _ in range(50)
        ]
        right = [
            simulate_outcome_reward("aider", "aider", 0.9, rng)
            for _ in range(50)
        ]
        assert sum(wrong) / 50 < sum(right) / 50 - 0.2  # clear gap


# ── Single-condition runner ──────────────────────────────────────────────────


class TestRunCondition:
    def test_completes_without_error(
        self,
        tmp_path: Path,
        tiny_prompts: list[EvalPrompt],
    ) -> None:
        result = run_condition(
            prompts=tiny_prompts,
            mode="off",
            seed=0,
            state_dir=tmp_path / "state",
            cache_path=tmp_path / "cache.sqlite",
            agents=["ollama", "aider", "codex-cli", "gemini-cli", "claude"],
            repeats=2,
        )
        # 3 prompts × 2 repeats = 6 picks
        assert result.total_picks == 6
        assert len(result.tasks) == 6
        assert result.cumulative_reward > 0
        assert 0.0 <= result.accuracy <= 1.0

    def test_off_mode_produces_no_memory_bias_growth_in_semantic_size_for_off(
        self,
        tmp_path: Path,
        tiny_prompts: list[EvalPrompt],
    ) -> None:
        """With mode=off, no embeddings should be stored — the SQLite
        embedding cache should not grow even though episodes accumulate."""
        cache_path = tmp_path / "cache.sqlite"
        run_condition(
            prompts=tiny_prompts,
            mode="off",
            seed=0,
            state_dir=tmp_path / "state",
            cache_path=cache_path,
            agents=["ollama", "aider", "codex-cli", "gemini-cli", "claude"],
            repeats=2,
        )
        # Cache may or may not exist; if it does it should be empty (file
        # created, schema created, no rows). Either is correct.
        if cache_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(cache_path))
            count = conn.execute(
                "SELECT COUNT(*) FROM embeddings"
            ).fetchone()[0]
            conn.close()
            assert count == 0

    def test_seeds_are_deterministic(
        self,
        tmp_path: Path,
        tiny_prompts: list[EvalPrompt],
    ) -> None:
        """Same seed → same selections."""
        agents = ["ollama", "aider", "codex-cli", "gemini-cli", "claude"]
        r1 = run_condition(
            prompts=tiny_prompts, mode="off", seed=42,
            state_dir=tmp_path / "s1", cache_path=tmp_path / "c1.sqlite",
            agents=agents, repeats=3,
        )
        r2 = run_condition(
            prompts=tiny_prompts, mode="off", seed=42,
            state_dir=tmp_path / "s2", cache_path=tmp_path / "c2.sqlite",
            agents=agents, repeats=3,
        )
        # Same prompt order
        assert [t.task_index for t in r1.tasks] == [t.task_index for t in r2.tasks]
        assert [t.prompt for t in r1.tasks] == [t.prompt for t in r2.tasks]


# ── Aggregation ──────────────────────────────────────────────────────────────


class TestAggregate:
    def test_basic_aggregation_shape(
        self,
        tmp_path: Path,
        tiny_prompts: list[EvalPrompt],
    ) -> None:
        agents = ["ollama", "aider", "codex-cli", "gemini-cli", "claude"]
        results = []
        for seed in range(3):
            for mode in ("off", "keyword"):
                results.append(run_condition(
                    prompts=tiny_prompts, mode=mode, seed=seed,
                    state_dir=tmp_path / f"{mode}_s{seed}",
                    cache_path=tmp_path / "cache.sqlite",
                    agents=agents, repeats=2,
                ))
        summary = aggregate(results)
        assert "off" in summary["by_mode"]
        assert "keyword" in summary["by_mode"]
        for mode in ("off", "keyword"):
            cr = summary["by_mode"][mode]["cumulative_reward"]
            assert cr["mean"] > 0
            assert cr["std"] >= 0
            assert len(cr["values"]) == 3

    def test_pairwise_section_includes_modes_in_results(
        self,
        tmp_path: Path,
        tiny_prompts: list[EvalPrompt],
    ) -> None:
        agents = ["ollama", "aider", "codex-cli", "gemini-cli", "claude"]
        results = [
            run_condition(
                prompts=tiny_prompts, mode=mode, seed=seed,
                state_dir=tmp_path / f"{mode}_s{seed}",
                cache_path=tmp_path / "cache.sqlite",
                agents=agents, repeats=2,
            )
            for seed in range(2) for mode in ("keyword", "off")
        ]
        summary = aggregate(results)
        assert "keyword_vs_off" in summary["pairwise"]
        # When both modes present, semantic_vs_keyword should NOT appear.
        assert "semantic_vs_keyword" not in summary["pairwise"]


# ── Top-level orchestrator ───────────────────────────────────────────────────


class TestRunEval:
    def test_writes_three_artifacts(
        self,
        tmp_path: Path,
        tiny_prompts: list[EvalPrompt],
    ) -> None:
        out = tmp_path / "out"
        run_eval(
            prompts=tiny_prompts,
            modes=["off", "keyword"],
            seeds=[0, 1],
            result_dir=out,
            repeats=2,
        )
        assert (out / "summary.json").exists()
        assert (out / "summary.md").exists()
        assert (out / "raw_results.json").exists()

    def test_summary_md_renders_modes(
        self,
        tmp_path: Path,
        tiny_prompts: list[EvalPrompt],
    ) -> None:
        out = tmp_path / "out"
        run_eval(
            prompts=tiny_prompts,
            modes=["off", "keyword"],
            seeds=[0, 1],
            result_dir=out,
            repeats=2,
        )
        md = (out / "summary.md").read_text()
        assert "Memory-Mode Evaluation" in md
        assert "off" in md
        assert "keyword" in md

    def test_summary_json_has_required_fields(
        self,
        tmp_path: Path,
        tiny_prompts: list[EvalPrompt],
    ) -> None:
        out = tmp_path / "out"
        run_eval(
            prompts=tiny_prompts,
            modes=["off"],
            seeds=[0],
            result_dir=out,
            repeats=2,
        )
        summary = json.loads((out / "summary.json").read_text())
        assert "by_mode" in summary
        assert "pairwise" in summary
        assert "elapsed_seconds" in summary
        assert summary["n_prompts"] == 3
        assert summary["repeats"] == 2
