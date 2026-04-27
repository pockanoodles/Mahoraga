PROMPT_SETS: dict[str, dict[str, list[str]]] = {
    "builder": {
        "easy": [
            "Write a Python function that reverses a string without using slicing.",
            "Write a bash one-liner to find all `.py` files modified in the last 7 days.",
        ],
        "medium": [
            "Implement a Python LRU cache class using only a dict and a doubly linked list. Include get and put methods.",
            "Write a FastAPI endpoint that accepts a JSON body with `user_id` and `amount`, validates both fields, and returns a receipt object.",
        ],
        "hard": [
            "Implement a thread-safe Python task queue with a worker pool. Workers pull tasks, execute them, and report results. Include shutdown logic.",
            (
                "Refactor this code to be async using aiohttp:\n"
                "```python\nimport requests, time\n"
                "def poll(url, retries=5):\n"
                "    for i in range(retries):\n"
                "        r = requests.get(url)\n"
                "        if r.ok: return r.json()\n"
                "        time.sleep(2**i)\n"
                "    raise RuntimeError('failed')\n```"
            ),
        ],
    },
    "security": {
        "easy": [
            "List the top 5 OWASP vulnerabilities and give one-line mitigations for each.",
            "What is the difference between authentication and authorization? Give a concrete example of each being bypassed.",
        ],
        "medium": [
            "Review this Python code for security vulnerabilities and explain each one:\n```python\nquery = f'SELECT * FROM users WHERE id = {user_input}'\n```",
            "Explain how a timing attack works against a password comparison function, then write a constant-time comparison in Python.",
        ],
        "hard": [
            "Design a threat model for a FastAPI service that handles JWT auth, user file uploads, and third-party OAuth. List assets, threats, and mitigations per STRIDE category.",
            "Write a Python script that scans a directory of Python files and flags: hardcoded secrets, SQL string formatting, and shell injection risks. Output structured findings.",
        ],
    },
    "research": {
        "easy": [
            "Summarize the key differences between RAG and fine-tuning for LLM adaptation in 3 bullet points.",
            "What is the transformer attention mechanism? Explain it as if to a software engineer who has never read a paper.",
        ],
        "medium": [
            "Compare LinUCB and Thompson Sampling for contextual bandits: when does each outperform the other, and why?",
            (
                "Given this abstract: 'We propose a reward shaping method that adds potential-based auxiliary "
                "rewards derived from a learned value function. Experiments on sparse-reward MuJoCo tasks show "
                "40% faster convergence vs. baseline PPO, with no reduction in final policy quality. Limitations "
                "include sensitivity to the quality of the learned potential and additional compute overhead.' "
                "— What are the core claims, limitations, and open questions?"
            ),
        ],
        "hard": [
            "Synthesize: what are the main failure modes of multi-agent LLM systems in production? Cover coordination, trust, cost, and quality. Cite reasoning, not sources.",
            "A user reports that their bandit router converges too quickly to one agent and stops exploring. Walk through possible causes, diagnostic steps, and fixes.",
        ],
    },
    "general": {
        "easy": [
            (
                "Parse this markdown task list and return only incomplete items as a Python list:\n"
                "- [x] Set up repo\n- [ ] Write tests\n- [x] Deploy to staging\n"
                "- [ ] Update docs\n- [x] Code review\n- [ ] Fix linting\n"
                "- [x] Merge PR\n- [ ] Notify team"
            ),
            (
                "You need to store user sessions. Option A: in-memory dict (fast, lost on restart). "
                "Option B: Redis (fast, persistent, extra infra). Option C: SQLite (slow, persistent, no infra). "
                "Your app restarts daily and has 500 concurrent users. Pick the best option and explain why."
            ),
        ],
        "medium": [
            (
                "Extract all rules that affect how code should be written from this CLAUDE.md and format them as a checklist:\n"
                "## Efficiency Rules\n- Read targeted — use offset/limit on large files.\n"
                "- Subagents for research.\n- Tight globs — never **/*.\n"
                "## Code Style\n- No comments unless WHY is non-obvious.\n"
                "- No error handling for impossible scenarios.\n"
                "- Default to no abstractions beyond what the task requires.\n"
                "- Don't add features beyond what was asked.\n"
                "## Testing\n- Run pytest from project root.\n"
                "- Don't mock the database in integration tests."
            ),
            "A service is slow. You have CPU at 20%, memory at 80%, and p99 latency spiking every 5 minutes. What are the most likely causes? How would you investigate each?",
        ],
        "hard": [
            "Write a project plan for migrating a monolithic FastAPI app to a microservices architecture. Include phases, risks, and rollback strategy. Output as structured markdown.",
            (
                "Read the following system design requirements and identify ambiguities, unstated assumptions, "
                "and missing constraints: 'Build a service that lets users upload files and share them with others. "
                "Files should be processed quickly. The system must be secure and handle many users. Admins can "
                "delete any file. Users should get notified when their file is ready. The service should not go down.'"
            ),
        ],
    },
}

ROLES: list[str] = list(PROMPT_SETS.keys())
TIERS: list[str] = ["easy", "medium", "hard"]
