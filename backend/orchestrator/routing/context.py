"""
Task context feature extraction for bandit routing.
Converts a task (object or dict) into a fixed-length numeric feature vector
that routing strategies use to make contextualised arm selections.
"""

import re
import numpy as np
from dataclasses import dataclass


CODE_KEYWORDS = frozenset({
    "function", "class", "def", "import", "return", "variable", "api",
    "endpoint", "database", "query", "sql", "html", "css", "javascript",
    "python", "react", "component", "module", "package", "async", "await",
    "decorator", "interface", "type", "enum", "struct", "method", "constructor",
    "inheritance", "polymorphism", "abstraction", "encapsulation",
})

ERROR_KEYWORDS = frozenset({
    "fix", "bug", "error", "crash", "fail", "broken", "wrong", "issue",
    "debug", "exception", "traceback", "stacktrace", "undefined", "null",
    "timeout", "hang", "freeze", "leak", "corrupt",
})

CREATION_KEYWORDS = frozenset({
    "write", "create", "build", "generate", "make", "implement", "add",
    "new", "scaffold", "setup", "initialize", "design", "draft", "compose",
})

RESEARCH_KEYWORDS = frozenset({
    "explain", "what", "how", "why", "research", "find", "describe",
    "compare", "difference", "between", "overview", "summary", "analyze",
    "evaluate", "review", "understand", "learn",
})

FILE_PATTERN = re.compile(r'\b[\w/\-]+\.\w{1,5}\b')


@dataclass
class TaskContext:
    word_count_norm: float
    code_keyword_density: float
    is_question: float
    complexity_tier: float
    file_count: float
    has_error_keywords: float
    has_creation_keywords: float
    has_research_keywords: float

    @property
    def d(self) -> int:
        return 8

    def to_vector(self) -> np.ndarray:
        return np.array([
            self.word_count_norm,
            self.code_keyword_density,
            self.is_question,
            self.complexity_tier,
            self.file_count,
            self.has_error_keywords,
            self.has_creation_keywords,
            self.has_research_keywords,
        ], dtype=np.float64)

    @classmethod
    def from_task(cls, task) -> "TaskContext":
        goal = task.goal if hasattr(task, 'goal') else task.get('goal', str(task))
        goal_lower = goal.lower()
        words = goal_lower.split()
        word_count = len(words)

        code_count = sum(1 for w in words if w in CODE_KEYWORDS)
        file_refs = len(FILE_PATTERN.findall(goal))

        is_q = (
            goal_lower.rstrip().endswith("?")
            or any(goal_lower.startswith(w + " ") for w in ["what", "how", "why", "explain", "describe", "who", "where", "when"])
        )

        tier = getattr(task, 'tier', None)
        if tier is not None:
            tier = max(1, min(3, int(tier)))
        else:
            if word_count < 10 and code_count == 0:
                tier = 1
            elif word_count > 50 or file_refs > 2:
                tier = 3
            else:
                tier = 2

        return cls(
            word_count_norm=min(word_count / 200.0, 1.0),
            code_keyword_density=code_count / max(word_count, 1),
            is_question=1.0 if is_q else 0.0,
            complexity_tier=tier / 3.0,
            file_count=min(file_refs / 10.0, 1.0),
            has_error_keywords=1.0 if any(w in words for w in ERROR_KEYWORDS) else 0.0,
            has_creation_keywords=1.0 if any(w in words for w in CREATION_KEYWORDS) else 0.0,
            has_research_keywords=1.0 if any(w in words for w in RESEARCH_KEYWORDS) else 0.0,
        )
