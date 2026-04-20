# Model Benchmark Suite — Design Spec
**Date:** 2026-04-19  
**Status:** Approved

---

## Overview

A standalone Python benchmark suite that measures real hardware throughput (tokens/second) and task completion times for local Ollama models across role-stratified prompt sets. Results accumulate in `brain/benchmarks/hardware_log.md` for longitudinal tracking as new models are released.

This is a **reporting tool only** — it does not feed back into the routing oracle or bandit state.

---

## File Structure

```
benchmark/
  prompts.py       # Fixed prompt sets keyed by role and difficulty
  model_bench.py   # Runner: discover → test → measure → write
brain/benchmarks/
  hardware_log.md  # Append-only, one timestamped section per run
```

---

## Prompt Sets

Four role-stratified sets. Each set has 3 difficulty tiers (Easy / Medium / Hard) with 2 prompts per tier (6 prompts per role, 24 total). Results per tier are averaged across the 2 prompts.

### Builder (executor/implementation)
| Tier | Prompt |
|------|--------|
| Easy | "Write a Python function that reverses a string without using slicing." |
| Easy | "Write a bash one-liner to find all `.py` files modified in the last 7 days." |
| Medium | "Implement a Python LRU cache class using only a dict and a doubly linked list. Include get and put methods." |
| Medium | "Write a FastAPI endpoint that accepts a JSON body with `user_id` and `amount`, validates both fields, and returns a receipt object." |
| Hard | "Implement a thread-safe Python task queue with a worker pool. Workers pull tasks, execute them, and report results. Include shutdown logic." |
| Hard | "Refactor this code to be async using aiohttp: `import requests, time\ndef poll(url, retries=5):\n    for i in range(retries):\n        r = requests.get(url)\n        if r.ok: return r.json()\n        time.sleep(2**i)\n    raise RuntimeError('failed')`" |

### Security
| Tier | Prompt |
|------|--------|
| Easy | "List the top 5 OWASP vulnerabilities and give one-line mitigations for each." |
| Easy | "What is the difference between authentication and authorization? Give a concrete example of each being bypassed." |
| Medium | "Review this Python code for security vulnerabilities and explain each one: `query = f'SELECT * FROM users WHERE id = {user_input}'`" |
| Medium | "Explain how a timing attack works against a password comparison function, then write a constant-time comparison in Python." |
| Hard | "Design a threat model for a FastAPI service that handles JWT auth, user file uploads, and third-party OAuth. List assets, threats, and mitigations per STRIDE category." |
| Hard | "Write a Python script that scans a directory of Python files and flags: hardcoded secrets, SQL string formatting, and shell injection risks. Output structured findings." |

### Research
| Tier | Prompt |
|------|--------|
| Easy | "Summarize the key differences between RAG and fine-tuning for LLM adaptation in 3 bullet points." |
| Easy | "What is the transformer attention mechanism? Explain it as if to a software engineer who has never read a paper." |
| Medium | "Compare LinUCB and Thompson Sampling for contextual bandits: when does each outperform the other, and why?" |
| Medium | "Given this abstract: 'We propose a reward shaping method that adds potential-based auxiliary rewards derived from a learned value function. Experiments on sparse-reward MuJoCo tasks show 40% faster convergence vs. baseline PPO, with no reduction in final policy quality. Limitations include sensitivity to the quality of the learned potential and additional compute overhead.' — What are the core claims, limitations, and open questions?" |
| Hard | "Synthesize: what are the main failure modes of multi-agent LLM systems in production? Cover coordination, trust, cost, and quality. Cite reasoning, not sources." |
| Hard | "A user reports that their bandit router converges too quickly to one agent and stops exploring. Walk through possible causes, diagnostic steps, and fixes." |

### General
| Tier | Prompt |
|------|--------|
| Easy | "Parse this markdown task list and return only incomplete items as a Python list:\n- [x] Set up repo\n- [ ] Write tests\n- [x] Deploy to staging\n- [ ] Update docs\n- [x] Code review\n- [ ] Fix linting\n- [x] Merge PR\n- [ ] Notify team" |
| Easy | "You need to store user sessions. Option A: in-memory dict (fast, lost on restart). Option B: Redis (fast, persistent, extra infra). Option C: SQLite (slow, persistent, no infra). Your app restarts daily and has 500 concurrent users. Pick the best option and explain why." |
| Medium | "Extract all rules that affect how code should be written from this CLAUDE.md and format them as a checklist:\n## Efficiency Rules\n- Read targeted — use offset/limit on large files.\n- Subagents for research.\n- Tight globs — never **/*.\n## Code Style\n- No comments unless WHY is non-obvious.\n- No error handling for impossible scenarios.\n- Default to no abstractions beyond what the task requires.\n- Don't add features beyond what was asked.\n## Testing\n- Run pytest from project root.\n- Don't mock the database in integration tests." |
| Medium | "A service is slow. You have CPU at 20%, memory at 80%, and p99 latency spiking every 5 minutes. What are the most likely causes? How would you investigate each?" |
| Hard | "Write a project plan for migrating a monolithic FastAPI app to a microservices architecture. Include phases, risks, and rollback strategy. Output as structured markdown." |
| Hard | "Read the following system design requirements and identify ambiguities, unstated assumptions, and missing constraints: 'Build a service that lets users upload files and share them with others. Files should be processed quickly. The system must be secure and handle many users. Admins can delete any file. Users should get notified when their file is ready. The service should not go down.'" |

---

## Measurement Methodology

- **Transport:** Direct HTTP to `http://localhost:11434/api/generate` (streaming)
- **Tokens/second:** Computed from Ollama's response fields: `eval_count / (eval_duration / 1e9)`
- **Task time:** Wall-clock from request send to stream complete
- **Per tier:** Average task time across the 2 prompts in that tier
- **Throughput:** Average t/s across all prompts for that model in that role set
- **Timeout:** 120s per prompt — recorded as `—` on timeout or error

---

## CLI Interface

```bash
# Full suite, all discovered models (ollama list)
python benchmark/model_bench.py

# Specific models only
python benchmark/model_bench.py qwen3:4b qwen3:8b

# Single role set (faster spot-check)
python benchmark/model_bench.py --role builder

# Combined
python benchmark/model_bench.py qwen3:4b --role security
```

---

## Output Format

Appended to `brain/benchmarks/hardware_log.md` after each run:

```markdown
## 2026-04-19 14:32 — Full Suite
**Hardware:** MacBook Pro M-series, 16 GB unified memory

### Builder
| Model | Throughput | Easy | Medium | Hard |
|-------|-----------|------|--------|------|
| qwen3:4b | 21 t/s | 11s | 34s | 47s |

### Security
| Model | Throughput | Easy | Medium | Hard |
| ...

---
```

Terminal also prints the same table live as each model completes.

---

## Error Handling

- Model not running / Ollama down → print warning, skip model, continue
- Prompt timeout (>120s) → record `—` for that tier, continue
- Partial response (stream error) → record `—`, log error to stderr
- No models found → exit with clear message

---

## Out of Scope

- Feeding results back to `oracle.py` or bandit state
- Quality scoring (this measures speed only)
- Remote/cloud model benchmarking
