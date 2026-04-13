"""
Generate tasks.jsonl with 200+ tasks across 6 categories for benchmark use.

Run:
    python -m backend.orchestrator.routing.benchmark.generate_tasks
"""
from __future__ import annotations
import json
from pathlib import Path

TASKS: list[dict] = []
_id = 0


def task(goal: str, task_type: str, difficulty: int) -> dict:
    global _id
    _id += 1
    return {"id": f"t{_id:03d}", "goal": goal, "type": task_type, "difficulty": difficulty}


# ── simple_qa (40) ────────────────────────────────────────────────────────────
SIMPLE_QA = [
    ("What does API stand for?", 1),
    ("What does HTTP stand for?", 1),
    ("What does JSON stand for?", 1),
    ("What does SQL stand for?", 1),
    ("What does ORM stand for?", 1),
    ("What does REST stand for?", 1),
    ("What does CLI stand for?", 1),
    ("What does IDE stand for?", 1),
    ("What does CI/CD stand for?", 1),
    ("What does DNS stand for?", 1),
    ("What is a Python list comprehension?", 1),
    ("What is a decorator in Python?", 2),
    ("What is a closure?", 2),
    ("What is a generator function in Python?", 2),
    ("What does the 'yield' keyword do in Python?", 2),
    ("What is the difference between == and is in Python?", 1),
    ("What does pip install -e . do?", 1),
    ("What is the purpose of __init__.py?", 1),
    ("What is the difference between a list and a tuple?", 1),
    ("What is a virtual environment?", 1),
    ("whats 2+2", 1),
    ("What is the capital of France?", 1),
    ("How many bytes in a megabyte?", 1),
    ("What year was Python created?", 1),
    ("What is the default port for HTTP?", 1),
    ("What is the default port for HTTPS?", 1),
    ("What is the default port for PostgreSQL?", 1),
    ("What does CRUD stand for?", 1),
    ("What does SOLID stand for?", 2),
    ("What is a primary key in a database?", 1),
    ("What is the difference between GET and POST?", 1),
    ("What is an index in a database?", 1),
    ("What is UTF-8?", 1),
    ("What does the 'self' parameter represent in Python?", 1),
    ("What is a Dockerfile?", 1),
    ("What does docker-compose do?", 2),
    ("What is a container vs a virtual machine?", 2),
    ("What is Git stash used for?", 1),
    ("What does git rebase do?", 2),
    ("What is the difference between merge and rebase in Git?", 2),
]

for goal, diff in SIMPLE_QA:
    TASKS.append(task(goal, "simple_qa", diff))

# ── code_generation (50) ──────────────────────────────────────────────────────
CODE_GEN = [
    ("Write a Python function that reverses a string", 1),
    ("Write a Python function to check if a number is prime", 1),
    ("Write a Python function that flattens a nested list", 2),
    ("Write a Python function to binary search a sorted list", 2),
    ("Write a Python class for a stack data structure", 2),
    ("Write a Python class for a queue using two stacks", 2),
    ("Write a Python function to compute Fibonacci numbers using memoization", 2),
    ("Create a REST API endpoint in FastAPI that returns a list of users", 2),
    ("Create a FastAPI endpoint that accepts a JSON body and validates it with Pydantic", 2),
    ("Write a Python context manager for timing code blocks", 2),
    ("Implement a simple LRU cache in Python", 3),
    ("Write a Python decorator that retries a function on exception", 2),
    ("Write a Python decorator that logs function calls with arguments", 2),
    ("Create a Python async function that fetches multiple URLs concurrently with httpx", 2),
    ("Write a Python function to parse a CSV file into a list of dicts", 1),
    ("Generate a Python script that walks a directory tree and counts files by extension", 2),
    ("Write a Python function that groups a list of dicts by a given key", 2),
    ("Implement merge sort in Python", 2),
    ("Implement quicksort in Python", 2),
    ("Write a Python function to validate an email address with regex", 1),
    ("Write a Python function to validate a URL", 2),
    ("Create a simple SQLite schema migration script in Python", 2),
    ("Write a Python function that deep-merges two dicts", 2),
    ("Write a Python generator that yields chunks of a list", 1),
    ("Create a Python script to rename all files in a folder to snake_case", 2),
    ("Write a Python function to compute the edit distance between two strings", 3),
    ("Implement a trie data structure in Python", 3),
    ("Write a Python function that serializes a dataclass to JSON", 2),
    ("Create a Python class implementing the observer pattern", 3),
    ("Write a Python function that computes the mode of a list", 1),
    ("Write a Python CLI tool using argparse that echoes its arguments", 1),
    ("Create a FastAPI app with CORS middleware configured", 2),
    ("Write a Python function that paginates a list into pages of N items", 2),
    ("Implement a simple rate limiter in Python", 3),
    ("Write a Python function to compute rolling averages over a list", 2),
    ("Create a Python dataclass for a User with name, email, and age fields", 1),
    ("Write a Python async generator that streams lines from a file", 2),
    ("Implement a basic HTTP client using Python's urllib", 2),
    ("Write a Python function that converts a camelCase string to snake_case", 1),
    ("Create a Python script that monitors a file for changes using watchdog", 2),
    ("Write a Python function that computes the intersection of two lists", 1),
    ("Implement a circular buffer in Python", 3),
    ("Write a Python function that parses a .env file into a dict", 1),
    ("Create a Python script that generates a QR code from a URL", 2),
    ("Write a Python function that validates JSON against a schema", 2),
    ("Implement a simple pub/sub system in Python using asyncio", 3),
    ("Write a Python function that converts a hex color to RGB", 1),
    ("Create a Python class for a directed graph with BFS and DFS traversal", 3),
    ("Write a Python function to read and parse a TOML config file", 1),
    ("Generate a complete SQLAlchemy model for a blog post with tags", 3),
]

for goal, diff in CODE_GEN:
    TASKS.append(task(goal, "code_generation", diff))

# ── code_editing (30) ─────────────────────────────────────────────────────────
CODE_EDIT = [
    ("Refactor this function to use list comprehensions instead of for loops", 1),
    ("Add type hints to all function signatures in this module", 1),
    ("Refactor these if-elif chains into a dispatch dict", 2),
    ("Extract duplicate code into a shared helper function", 2),
    ("Add docstrings to all public functions in this module", 1),
    ("Rename all variables to follow snake_case convention", 1),
    ("Refactor this class to use dataclasses", 2),
    ("Split this 200-line function into smaller focused functions", 3),
    ("Add input validation to all public API methods", 2),
    ("Replace magic numbers with named constants", 1),
    ("Add error handling to the file reading function", 2),
    ("Wrap all database calls in try/except with proper rollback", 2),
    ("Add logging to every function entry and exit point", 2),
    ("Convert synchronous functions to async in the service layer", 3),
    ("Optimize this O(n^2) loop to O(n log n)", 3),
    ("Add caching to the expensive computation in the analytics module", 2),
    ("Replace global variables with dependency injection", 3),
    ("Migrate from os.path to pathlib throughout the codebase", 2),
    ("Refactor inline SQL queries to use parameterized statements", 2),
    ("Add __slots__ to the high-frequency dataclass to reduce memory", 2),
    ("Extract the config loading logic into its own module", 2),
    ("Replace bare except clauses with specific exception types", 1),
    ("Add context managers to replace manual open/close calls", 2),
    ("Refactor the nested callbacks into async/await", 3),
    ("Add retry logic to the external API client", 2),
    ("Optimize the repeated dict lookups with a local variable", 1),
    ("Consolidate the three config files into a single Pydantic settings class", 3),
    ("Replace print statements with proper logging calls", 1),
    ("Refactor the authentication middleware to be reusable", 2),
    ("Add pagination to the list endpoint that currently returns all records", 2),
]

for goal, diff in CODE_EDIT:
    TASKS.append(task(goal, "code_editing", diff))

# ── debugging (30) ────────────────────────────────────────────────────────────
DEBUG = [
    ("Fix the failing test for the user authentication module", 2),
    ("The API returns 500 on POST /users — debug and fix", 2),
    ("Memory leak in the background worker — find and fix it", 3),
    ("The database connection pool is exhausted after a few hours — debug", 3),
    ("Fix the off-by-one error in the pagination logic", 1),
    ("The test suite hangs on teardown — find the cause", 2),
    ("Fix the race condition in the concurrent file writer", 3),
    ("The JSON serializer throws on datetime objects — fix it", 2),
    ("The async task queue stops processing after the first error — debug", 2),
    ("Fix the broken import that causes ModuleNotFoundError on startup", 1),
    ("The regex pattern matches too greedily — fix it", 2),
    ("Fix the SQL query that returns duplicate rows", 2),
    ("The environment variable is not being read — debug", 1),
    ("Fix the AttributeError caused by a None check missing in the handler", 1),
    ("The HTTP client times out on large responses — fix the stream handling", 2),
    ("Fix the circular import between two modules", 2),
    ("The sorted list function returns wrong order for Unicode strings — debug", 2),
    ("Fix the flaky test that randomly fails due to timing", 2),
    ("The webhook handler ignores requests with missing headers — debug", 2),
    ("Fix the KeyError when accessing a nested dict with missing keys", 1),
    ("The background scheduler fires tasks twice — investigate and fix", 3),
    ("Fix the TypeError when passing None to a function expecting a string", 1),
    ("The file upload endpoint silently truncates files over 10MB — debug", 2),
    ("Fix the incorrect hash function that causes cache collisions", 3),
    ("The pagination cursor is wrong after deleting records — debug", 3),
    ("Fix the unhandled exception that crashes the worker process", 2),
    ("The logging handler drops messages under high load — debug", 3),
    ("Fix the broken URL routing that matches paths in the wrong order", 2),
    ("The config parser fails silently on invalid YAML — add proper error handling", 2),
    ("Fix the test that passes locally but fails in CI due to timezone differences", 2),
]

for goal, diff in DEBUG:
    TASKS.append(task(goal, "debugging", diff))

# ── research (30) ─────────────────────────────────────────────────────────────
RESEARCH = [
    ("Explain how the CPython GIL works and when it matters", 2),
    ("Compare REST vs GraphQL — what are the tradeoffs?", 2),
    ("Explain the CAP theorem and how it applies to distributed databases", 3),
    ("What are the tradeoffs of using asyncio vs threading in Python?", 2),
    ("Explain how B-tree indexes work in PostgreSQL", 3),
    ("Compare SQLAlchemy ORM vs raw SQL — when to use each?", 2),
    ("Explain how JWT authentication works end to end", 2),
    ("What are the tradeoffs of microservices vs monoliths?", 2),
    ("Explain how consistent hashing works in distributed systems", 3),
    ("What is the difference between optimistic and pessimistic locking?", 2),
    ("How does Python's garbage collector handle reference cycles?", 2),
    ("Explain the event loop in asyncio — how does it schedule coroutines?", 2),
    ("What are the SOLID principles? Give a Python example for each.", 2),
    ("Compare PostgreSQL vs MongoDB for a document-heavy workload", 2),
    ("Explain how HTTPS TLS handshake works step by step", 2),
    ("What is the difference between process, thread, and coroutine?", 2),
    ("How does connection pooling work and why is it important?", 2),
    ("Explain how Python decorators work under the hood", 1),
    ("What is the difference between INNER JOIN and LEFT JOIN?", 1),
    ("Explain how Python's import system resolves modules", 2),
    ("What are the tradeoffs of using Redis vs memcached for caching?", 2),
    ("Explain how WebSockets differ from HTTP long polling", 2),
    ("What are the main differences between OAuth 2.0 and OpenID Connect?", 3),
    ("How does Python's dataclass compare to attrs and Pydantic?", 2),
    ("Explain the two-phase commit protocol in distributed transactions", 3),
    ("What are the differences between CDN edge caching and origin caching?", 2),
    ("How does Kubernetes handle pod scheduling and resource limits?", 3),
    ("Explain the actor model of concurrency — how does it compare to shared memory?", 3),
    ("What are the tradeoffs of event sourcing vs traditional CRUD?", 3),
    ("Explain how Python's asyncio event loop interacts with threads", 2),
]

for goal, diff in RESEARCH:
    TASKS.append(task(goal, "research", diff))

# ── terminal_operations (20) ──────────────────────────────────────────────────
TERMINAL = [
    ("List all Python files recursively in the current directory", 1),
    ("Set up a Python virtual environment and install dependencies from requirements.txt", 1),
    ("Run the test suite with pytest and show only failures", 1),
    ("Find all files larger than 100MB in the home directory", 1),
    ("Show disk usage for each subdirectory, sorted by size", 1),
    ("Kill all processes listening on port 8080", 2),
    ("Show the last 100 lines of the application log file", 1),
    ("Create a tar archive of the src directory excluding __pycache__", 2),
    ("Set environment variables from a .env file for the current shell session", 1),
    ("Watch a log file in real time and filter for ERROR lines", 1),
    ("Find all files modified in the last 24 hours", 1),
    ("Check which process is using a specific port", 1),
    ("Run a Python script with a specific environment variable set", 1),
    ("Create a cron job that runs a Python script every hour", 2),
    ("Show CPU and memory usage of all running Python processes", 1),
    ("Batch rename all .txt files to .md in the current directory", 2),
    ("Generate a requirements.txt from the current virtualenv", 1),
    ("Run pytest in watch mode, re-running on file changes", 2),
    ("Show git log as a one-line graph for the last 20 commits", 1),
    ("Tail the system log and filter for lines containing a specific service name", 1),
]

for goal, diff in TERMINAL:
    TASKS.append(task(goal, "terminal_operations", diff))


def main() -> None:
    out_path = Path(__file__).parent / "tasks.jsonl"
    with out_path.open("w") as f:
        for t in TASKS:
            f.write(json.dumps(t) + "\n")
    print(f"Wrote {len(TASKS)} tasks to {out_path}")

    # Print category breakdown
    from collections import Counter
    counts = Counter(t["type"] for t in TASKS)
    for category, count in sorted(counts.items()):
        print(f"  {category}: {count}")


if __name__ == "__main__":
    main()
