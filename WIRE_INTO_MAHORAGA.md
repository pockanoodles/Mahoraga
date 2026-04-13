# Wire Brain Auto-Logger into Mahoraga

**For:** Claude Code  
**What:** After every task completion, Mahoraga auto-logs to `~/Brain/brain/journal/`.  
**Time:** ~10 minutes. Three changes.

---

## File to add

Copy `brain_logger.py` into `backend/orchestrator/`:

```bash
cp ~/brain-tools/mahoraga/brain_logger.py backend/orchestrator/brain_logger.py
```

## Change 1: Import in gateway

In `backend/orchestrator/gateway.py`, add at the top:

```python
from .brain_logger import log_task_completion
```

## Change 2: Call after task completion

Find where a task's final output is assembled and returned to the user (this is the same code path you just fixed for the response assembler). After the response is sent, add:

```python
try:
    log_task_completion(
        task_title=task.title or mission.title,
        task_goal=task.goal or "",
        agent_used=worker_id or "unknown",
        output_preview=final_output[:500] if final_output else "",
        cost=0.0,  # Update with actual cost if tracked
        quality_score=None,  # Update with score if available
    )
except Exception:
    pass  # Never let logging break the main flow
```

The `try/except` is critical — the brain logger should never crash the orchestrator. If `~/Brain/` doesn't exist, it returns `None` silently.

## Change 3: Session summary on shutdown (optional)

In `backend/orchestrator/service/app.py`, in the lifespan or shutdown hook:

```python
from ..brain_logger import log_session_summary

# On app shutdown:
log_session_summary(
    tasks_completed=session_task_count,
    total_cost=session_total_cost,
    agents_used=session_agents_used,
)
```

## What it produces

After a few tasks, `~/Brain/brain/journal/2026-04-12-mahoraga-session.md` will look like:

```markdown
# Mahoraga session — 2026-04-12

Auto-logged by Mahoraga.

---

### 04:04 — whats 2+2

- **Agent:** ollama
- **Cost:** $0.0000
- **Quality:** 9.0/10
- **Duration:** 1.2s

**Output preview:**
\```
2+2=4
\```

---

### 04:05 — write a function for mean median mode

- **Agent:** ollama
- **Cost:** $0.0000
- **Duration:** 8.4s

**Output preview:**
\```
from statistics import mean, median, mode

def stats(numbers):
    return {
        "mean": mean(numbers),
        "median": median(numbers),
        "mode": mode(numbers),
    }
\```
```

Open Obsidian, go to brain/journal/ — every Mahoraga session is there, searchable, linked. No manual logging. It just happens.
