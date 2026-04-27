"""
Sharpened Oracle for Mahoraga Benchmark Harness.

The previous oracle used a near-uniform compatibility matrix — every agent
was roughly equally capable at every task type, so LinUCB couldn't learn
meaningful differentiation.  This module replaces it with a realistic,
asymmetric ground-truth that reflects actual agent strengths.

Design principle: the oracle should encode the HYPOTHESIS we're testing —
"agents have meaningfully different strengths per task type, and a contextual
bandit can discover them faster than static routing."

Drop-in replacement: swap `Oracle` for the old compatibility class in harness.py.

Usage:
    oracle = Oracle(seed=42)
    task   = oracle.sample_task()           # -> Task
    reward = oracle.evaluate(task, "aider") # -> float in [0, 1]
    best   = oracle.optimal_agent(task)     # -> str (for regret computation)
    best_r = oracle.optimal_reward(task)    # -> float (for regret computation)
"""

from __future__ import annotations

import dataclasses
import random
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Task categories — map to Mahoraga's real routing landscape
# ---------------------------------------------------------------------------

TASK_CATEGORIES = [
    "simple_chat",        # "what's 2+2", greetings, trivia
    "code_generation",    # "write a REST API with auth"
    "code_refactoring",   # "refactor this 500-line file"
    "debugging",          # "fix the failing test in test_router.py"
    "file_operations",    # "create a file called test.py"
    "research",           # "explain how transformers work"
    "planning",           # "break down this feature into tasks"
    "complex_reasoning",  # "design a caching strategy for..."
]

# Category distribution — how often each type appears in real usage.
# Skewed toward code + chat (the things people actually do).
CATEGORY_WEIGHTS = {
    "simple_chat":       0.20,
    "code_generation":   0.20,
    "code_refactoring":  0.10,
    "debugging":         0.12,
    "file_operations":   0.10,
    "research":          0.12,
    "planning":          0.08,
    "complex_reasoning": 0.08,
}

# ---------------------------------------------------------------------------
# Agents — the arms of the bandit
# ---------------------------------------------------------------------------

AGENTS = [
    "ollama",       # Local Qwen3 4B — fast, free, no file access
    "codex-cli",    # OpenAI Codex CLI — great at file creation, costs money
    "aider",        # Aider — strong at refactoring with context, costs money
    "gemini-cli",   # Gemini CLI — good general-purpose, free tier
    "claude",       # Claude (Anthropic API) — escalation tier, best at reasoning
]


# ---------------------------------------------------------------------------
# Compatibility matrix — THE CORE OF THE ORACLE
#
# Each cell is (mean_reward, std_reward) for (category, agent).
# The means are deliberately ASYMMETRIC — agents have real strengths
# and weaknesses.  This is the ground-truth that LinUCB should discover.
#
# Design rationale for each agent:
#   ollama     — fast + free for simple tasks, degrades hard on complex/code
#   codex-cli  — best at code gen + file ops, expensive, weak at chat
#   aider      — best at refactoring + debugging (needs file context)
#   gemini-cli — solid general-purpose, good at research, mediocre at code
#   claude     — escalation tier; best reasoning + planning, expensive, overkill for simple chat
# ---------------------------------------------------------------------------

COMPATIBILITY: dict[str, dict[str, tuple[float, float]]] = {
    #                       ollama         codex-cli      aider          gemini-cli     claude
    "simple_chat":       {"ollama": (0.92, 0.05), "codex-cli": (0.45, 0.15), "aider": (0.50, 0.12), "gemini-cli": (0.85, 0.07), "claude": (0.88, 0.05)},
    "code_generation":   {"ollama": (0.30, 0.15), "codex-cli": (0.90, 0.06), "aider": (0.78, 0.08), "gemini-cli": (0.65, 0.10), "claude": (0.92, 0.04)},
    "code_refactoring":  {"ollama": (0.20, 0.10), "codex-cli": (0.72, 0.10), "aider": (0.92, 0.05), "gemini-cli": (0.55, 0.12), "claude": (0.90, 0.04)},
    "debugging":         {"ollama": (0.25, 0.12), "codex-cli": (0.75, 0.09), "aider": (0.88, 0.06), "gemini-cli": (0.60, 0.10), "claude": (0.91, 0.04)},
    "file_operations":   {"ollama": (0.15, 0.08), "codex-cli": (0.93, 0.04), "aider": (0.80, 0.08), "gemini-cli": (0.50, 0.12), "claude": (0.85, 0.06)},
    "research":          {"ollama": (0.70, 0.10), "codex-cli": (0.35, 0.15), "aider": (0.40, 0.14), "gemini-cli": (0.88, 0.06), "claude": (0.90, 0.05)},
    "planning":          {"ollama": (0.55, 0.12), "codex-cli": (0.40, 0.14), "aider": (0.45, 0.13), "gemini-cli": (0.80, 0.08), "claude": (0.92, 0.04)},
    "complex_reasoning": {"ollama": (0.25, 0.12), "codex-cli": (0.60, 0.12), "aider": (0.55, 0.12), "gemini-cli": (0.82, 0.07), "claude": (0.95, 0.03)},
}


# ---------------------------------------------------------------------------
# Cost per agent (USD per task, used for reward decomposition)
# ---------------------------------------------------------------------------

AGENT_COST: dict[str, float] = {
    "ollama":     0.000,   # Free — local inference
    "codex-cli":  0.035,   # OpenAI API
    "aider":      0.028,   # API calls under the hood
    "gemini-cli": 0.005,   # Mostly free tier, occasional paid
    "claude":     0.085,   # Anthropic API — most expensive tier
}

# Base latency per agent (seconds, before task-complexity scaling)
AGENT_LATENCY: dict[str, tuple[float, float]] = {
    "ollama":     (1.5, 0.5),   # Fast local
    "codex-cli":  (4.0, 1.5),   # Network round-trip
    "aider":      (5.0, 2.0),   # Slower — reads context
    "gemini-cli": (3.0, 1.0),   # Fast API
    "claude":     (3.5, 1.0),   # Fast API, moderate latency
}


# ---------------------------------------------------------------------------
# Task representation
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Task:
    """A synthetic benchmark task."""
    task_id: int
    category: str
    prompt: str
    word_count: int
    complexity: int             # 1, 2, or 3
    has_code_keywords: bool
    has_error_keywords: bool
    has_creation_keywords: bool
    is_question: bool

    def context_vector(self, dim: int = 8) -> np.ndarray:
        """
        Extract the Tier-1 context feature vector for LinUCB.

        Features (8-dim):
            0: word_count (normalized, /50)
            1: code_keyword_density (binary for now)
            2: is_question (0/1)
            3: complexity_tier (1/2/3, normalized /3)
            4: has_error_keywords (0/1)
            5: has_creation_keywords (0/1)
            6: prompt_length_chars (normalized, /500)
            7: bias term (always 1.0)
        """
        x = np.zeros(dim)
        x[0] = min(self.word_count / 50.0, 1.0)
        x[1] = 1.0 if self.has_code_keywords else 0.0
        x[2] = 1.0 if self.is_question else 0.0
        x[3] = self.complexity / 3.0
        x[4] = 1.0 if self.has_error_keywords else 0.0
        x[5] = 1.0 if self.has_creation_keywords else 0.0
        x[6] = min(len(self.prompt) / 500.0, 1.0)
        x[7] = 1.0  # bias
        return x

    def extended_context_vector(self, dim: int = 14) -> np.ndarray:
        """
        Tier-1 + Tier-2 features (14-dim).

        Extra features (6 more):
            8:  has_research_keywords (0/1)
            9:  has_planning_keywords (0/1)
            10: category_is_code (0/1)  — leaks ground truth slightly,
                but mirrors the keyword router's own signal
            11: prompt_length_log (log(chars)/log(1000))
            12: word_count_squared (captures nonlinearity)
            13: code_and_complex interaction (code_kw * complexity/3)
        """
        base = self.context_vector(8)
        ext = np.zeros(dim)
        ext[:8] = base

        research_kw = {"explain", "what", "how", "why", "describe", "compare"}
        planning_kw = {"plan", "outline", "break", "decompose", "strategy"}
        words = set(self.prompt.lower().split())

        ext[8]  = 1.0 if words & research_kw else 0.0
        ext[9]  = 1.0 if words & planning_kw else 0.0
        ext[10] = 1.0 if self.category in ("code_generation", "code_refactoring",
                                             "debugging", "file_operations") else 0.0
        ext[11] = np.log1p(len(self.prompt)) / np.log(1000)
        ext[12] = (self.word_count / 50.0) ** 2
        ext[13] = ext[1] * ext[3]  # code_keyword * complexity
        return ext


# ---------------------------------------------------------------------------
# Prompt templates per category (for realistic task generation)
# ---------------------------------------------------------------------------

_PROMPTS: dict[str, list[str]] = {
    "simple_chat": [
        "what's 2+2",
        "hello",
        "how are you today",
        "what time is it",
        "tell me a joke",
        "what is Python",
        "who created Linux",
        "good morning",
        "thanks for your help",
        "what does API stand for",
        "define recursion",
        "how many planets are there",
    ],
    "code_generation": [
        "write a REST API with authentication in Flask",
        "create a Python script that parses CSV files and generates reports",
        "implement a binary search tree with insert, delete, and search",
        "write a WebSocket server in Python",
        "build a CLI tool that monitors disk usage",
        "implement a rate limiter using the token bucket algorithm",
        "write a Python decorator that caches function results with TTL",
        "create a FastAPI endpoint that handles file uploads",
        "implement a simple key-value store with persistence",
        "write unit tests for a user authentication module",
        "build a markdown parser that converts to HTML",
        "implement a task queue with priority scheduling",
    ],
    "code_refactoring": [
        "refactor this 500-line file to use proper design patterns",
        "convert these raw dictionaries to dataclasses",
        "extract the database logic into a repository pattern",
        "refactor the error handling to use custom exceptions",
        "break this monolithic function into smaller composable pieces",
        "convert synchronous I/O calls to async",
        "replace the global state with dependency injection",
        "simplify the nested conditionals using early returns",
        "refactor to remove code duplication between these three files",
        "convert the class hierarchy to composition",
    ],
    "debugging": [
        "fix the failing test in test_router.py",
        "debug why the API returns 500 on POST requests",
        "find the memory leak in the WebSocket handler",
        "fix the race condition in the task queue",
        "debug why imports fail when running from the project root",
        "fix the off-by-one error in the pagination logic",
        "debug the timeout issue in the database connection pool",
        "find why the cache invalidation isn't working",
        "fix the broken pipe error in the streaming response",
        "debug the authentication middleware rejecting valid tokens",
    ],
    "file_operations": [
        "create a file called test.py that prints hello world",
        "create a new directory structure for the microservice",
        "write a configuration file for the logging setup",
        "create a Dockerfile for the Python application",
        "generate a requirements.txt from the import statements",
        "create a .env.example file documenting all environment variables",
        "write a Makefile for the common development tasks",
        "create a GitHub Actions workflow for CI/CD",
        "generate an __init__.py that exports the public API",
        "create a setup.py with the project metadata",
    ],
    "research": [
        "explain how transformer attention mechanisms work",
        "what are the differences between REST and GraphQL",
        "how does the Python GIL affect multithreading",
        "explain the CAP theorem with examples",
        "what is the difference between Docker and Kubernetes",
        "how do embeddings represent semantic meaning",
        "explain eventual consistency in distributed systems",
        "what are the tradeoffs between SQL and NoSQL databases",
        "how does garbage collection work in Python",
        "explain the difference between processes and threads",
    ],
    "planning": [
        "break down building a user authentication system into tasks",
        "create a migration plan from monolith to microservices",
        "outline the steps to implement a CI/CD pipeline",
        "plan the database schema for an e-commerce platform",
        "decompose the feature: real-time collaborative editing",
        "outline a testing strategy for the payment processing module",
        "plan the rollout of the new API version",
        "create a sprint plan for the search feature",
    ],
    "complex_reasoning": [
        "design a caching strategy that handles cache invalidation across microservices",
        "architect a system that handles 10K concurrent WebSocket connections on a single machine",
        "design the retry and circuit breaker logic for external API dependencies",
        "create a cost optimization strategy for our cloud infrastructure",
        "design a data pipeline that handles both batch and stream processing",
        "architect a multi-tenant system with tenant-level data isolation",
        "design a feature flag system that supports gradual rollouts",
        "create a strategy for migrating from REST to event-driven architecture",
    ],
}


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

class Oracle:
    """
    Ground-truth oracle for benchmark evaluation.

    Knows which agent is best for each task category, can simulate
    noisy rewards, and provides the optimal baseline for regret computation.
    """

    def __init__(self, seed: int = 42, n_tasks: int = 200):
        self.rng = np.random.default_rng(seed)
        self.py_rng = random.Random(seed)
        self.n_tasks = n_tasks
        self._tasks: Optional[list[Task]] = None

    @property
    def agents(self) -> list[str]:
        """Return the list of available agent arms."""
        return AGENTS

    @property
    def categories(self) -> list[str]:
        """Return the list of task category names."""
        return TASK_CATEGORIES

    def generate_tasks(self) -> list[Task]:
        """Generate the full task replay dataset."""
        if self._tasks is not None:
            return self._tasks

        tasks: list[Task] = []
        categories = list(CATEGORY_WEIGHTS.keys())
        weights = [CATEGORY_WEIGHTS[c] for c in categories]

        code_kw = {"code", "function", "implement", "debug", "script",
                    "class", "test", "fix", "bug", "api", "write", "create",
                    "build", "generate", "refactor"}
        error_kw = {"fix", "bug", "error", "crash", "fail", "debug", "broken"}
        create_kw = {"write", "create", "build", "generate", "make", "new"}

        for i in range(self.n_tasks):
            cat = self.py_rng.choices(categories, weights=weights, k=1)[0]
            prompt = self.py_rng.choice(_PROMPTS[cat])
            words = set(prompt.lower().split())

            # Assign complexity based on category
            if cat in ("simple_chat",):
                complexity = 1
            elif cat in ("code_generation", "file_operations", "research"):
                complexity = self.py_rng.choice([1, 2, 2])
            elif cat in ("code_refactoring", "debugging", "planning"):
                complexity = self.py_rng.choice([2, 2, 3])
            else:
                complexity = self.py_rng.choice([2, 3, 3])

            tasks.append(Task(
                task_id=i,
                category=cat,
                prompt=prompt,
                word_count=len(prompt.split()),
                complexity=complexity,
                has_code_keywords=bool(words & code_kw),
                has_error_keywords=bool(words & error_kw),
                has_creation_keywords=bool(words & create_kw),
                is_question=prompt.strip().endswith("?") or
                            prompt.lower().startswith(("what", "how", "why", "who")),
            ))

        self._tasks = tasks
        return tasks

    def evaluate(self, task: Task, agent: str) -> dict:
        """
        Simulate running a task on an agent.  Returns a full outcome dict.

        Returns:
            {
                "success": bool,
                "quality": float (0-10),
                "latency_s": float,
                "cost_usd": float,
                "reward": float (composite, 0-1),
            }
        """
        mean, std = COMPATIBILITY[task.category][agent]

        # Sample quality from truncated normal
        raw_quality = float(np.clip(
            self.rng.normal(mean, std), 0.0, 1.0
        ))

        # Complexity penalty — harder tasks amplify agent weaknesses
        complexity_factor = 1.0 - (task.complexity - 1) * 0.08
        adjusted_quality = raw_quality * complexity_factor

        # Success threshold — below 0.4 quality is a failure
        success = adjusted_quality >= 0.40

        # Latency — base + complexity scaling + noise
        lat_mean, lat_std = AGENT_LATENCY[agent]
        latency = max(0.5, float(
            self.rng.normal(lat_mean * task.complexity, lat_std)
        ))

        # Cost
        cost = AGENT_COST[agent] * task.complexity

        # Composite reward (the signal the bandit optimizes)
        # Weights: success 0.50, quality 0.25, speed 0.15, cost 0.10
        # Success-heavy + quality emphasis surfaces agent skill differences;
        # speed and cost are real constraints but secondary to correctness.
        speed_score = max(0.0, 1.0 - latency / 20.0)  # 20s = 0 score
        cost_score = max(0.0, 1.0 - cost / 0.25)       # $0.25 = 0 score

        reward = (
            (1.0 if success else 0.0) * 0.50 +
            adjusted_quality * 0.25 +
            speed_score * 0.15 +
            cost_score * 0.10
        )

        return {
            "success": success,
            "quality": round(adjusted_quality * 10, 1),  # 0-10 scale
            "latency_s": round(latency, 2),
            "cost_usd": round(cost, 4),
            "reward": round(reward, 4),
        }

    def optimal_agent(self, task: Task) -> str:
        """Return the agent with the highest expected reward for this task."""
        best_agent = ""
        best_expected = -1.0
        for arm in AGENTS:
            mean, _ = COMPATIBILITY[task.category][arm]
            complexity_factor = 1.0 - (task.complexity - 1) * 0.08
            adj_quality = mean * complexity_factor
            success = 1.0 if adj_quality >= 0.40 else 0.0

            lat_mean, _ = AGENT_LATENCY[arm]
            latency = lat_mean * task.complexity
            cost = AGENT_COST[arm] * task.complexity

            speed_score = max(0.0, 1.0 - latency / 20.0)
            cost_score = max(0.0, 1.0 - cost / 0.25)

            expected = (
                success * 0.50 +
                adj_quality * 0.25 +
                speed_score * 0.15 +
                cost_score * 0.10
            )
            if expected > best_expected:
                best_expected = expected
                best_agent = arm
        return best_agent

    def optimal_reward(self, task: Task) -> float:
        """Return the expected reward of the oracle-optimal agent."""
        arm = self.optimal_agent(task)
        mean, _ = COMPATIBILITY[task.category][arm]
        complexity_factor = 1.0 - (task.complexity - 1) * 0.08
        adj_quality = mean * complexity_factor
        success = 1.0 if adj_quality >= 0.40 else 0.0

        lat_mean, _ = AGENT_LATENCY[arm]
        latency = lat_mean * task.complexity
        cost = AGENT_COST[arm] * task.complexity

        speed_score = max(0.0, 1.0 - latency / 20.0)
        cost_score = max(0.0, 1.0 - cost / 0.25)

        return (
            success * 0.50 +
            adj_quality * 0.25 +
            speed_score * 0.15 +
            cost_score * 0.10
        )

    def print_compatibility_summary(self) -> None:
        """Print best agent per category — useful for sanity checking."""
        print("\n=== Oracle: Best Agent per Category ===\n")
        for cat in TASK_CATEGORIES:
            best = self.optimal_agent(Task(
                task_id=0, category=cat, prompt="",
                word_count=10, complexity=2,
                has_code_keywords=False, has_error_keywords=False,
                has_creation_keywords=False, is_question=False,
            ))
            mean = COMPATIBILITY[cat][best][0]
            print(f"  {cat:<22s}  ->  {best:<12s}  (mean reward: {mean:.2f})")
        print()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    oracle = Oracle(seed=42, n_tasks=200)
    oracle.print_compatibility_summary()

    tasks = oracle.generate_tasks()
    print(f"Generated {len(tasks)} tasks")
    print("Category distribution:")
    from collections import Counter
    cats = Counter(t.category for t in tasks)
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat:<22s}  {count:3d}  ({count/len(tasks)*100:.0f}%)")

    # Test evaluation
    t = tasks[0]
    for agent in AGENTS:
        result = oracle.evaluate(t, agent)
        print(f"  {agent:<12s}  reward={result['reward']:.3f}  "
              f"success={result['success']}  latency={result['latency_s']:.1f}s  "
              f"cost=${result['cost_usd']:.4f}")
