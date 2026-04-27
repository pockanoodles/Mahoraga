"""
Brain Auto-Logger for Mahoraga
==============================

Drop-in module for Mahoraga's backend. Import and call after
task completion to automatically log sessions to brain/journal/
in the Mahoraga repo.

Usage in gateway.py:
    from .brain_logger import log_task_completion

    # After a task completes successfully:
    log_task_completion(
        task_title=task.title,
        task_goal=task.goal,
        agent_used=adapter.name,
        output_preview=result[:500],
        cost=cost_estimate.estimated_cost_usd,
        quality_score=score,
    )

What it writes:
    - Appends to brain/journal/YYYY-MM-DD-mahoraga-session.md in the repo
    - One file per day, appends each task as a section
    - Includes: timestamp, agent used, cost, quality score, output preview

Override the default path with MAHORAGA_BRAIN_PATH env var.
No MCP needed. No dependencies. Just filesystem writes.
"""

from __future__ import annotations
import os
from pathlib import Path
from datetime import datetime, date

# Default: brain/ directory in the Mahoraga project root
# backend/orchestrator/brain_logger.py -> ../../.. -> project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent
BRAIN_PATH = Path(os.environ.get("MAHORAGA_BRAIN_PATH", _PROJECT_ROOT / "brain"))
JOURNAL_DIR = BRAIN_PATH / "journal"


def log_task_completion(
    task_title: str,
    task_goal: str = "",
    agent_used: str = "unknown",
    output_preview: str = "",
    cost: float = 0.0,
    quality_score: float | None = None,
    duration_seconds: float | None = None,
) -> str | None:
    """
    Log a completed task to the brain's daily session journal.

    Creates or appends to: brain/journal/YYYY-MM-DD-mahoraga-session.md

    Returns the file path written to, or None if brain/ doesn't exist.
    """
    if not BRAIN_PATH.exists():
        return None

    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    filepath = JOURNAL_DIR / f"{today}-mahoraga-session.md"

    if not filepath.exists():
        header = f"# Mahoraga session — {today}\n\nAuto-logged by Mahoraga.\n\n"
        filepath.write_text(header, encoding="utf-8")

    entry_parts = [
        "\n---\n",
        f"\n### {now} — {task_title}\n",
        f"\n- **Agent:** {agent_used}",
        f"\n- **Cost:** ${cost:.4f}",
    ]

    if quality_score is not None:
        entry_parts.append(f"\n- **Quality:** {quality_score}/10")

    if duration_seconds is not None:
        entry_parts.append(f"\n- **Duration:** {duration_seconds:.1f}s")

    if task_goal and task_goal != task_title:
        entry_parts.append(f"\n- **Goal:** {task_goal[:200]}")

    if output_preview:
        preview = output_preview[:500].strip()
        if len(output_preview) > 500:
            preview += "..."
        entry_parts.append(f"\n\n**Output preview:**\n```\n{preview}\n```")

    entry_parts.append("\n")

    with open(filepath, "a", encoding="utf-8") as f:
        f.write("".join(entry_parts))

    return str(filepath)


def log_session_summary(
    tasks_completed: int = 0,
    total_cost: float = 0.0,
    agents_used: list[str] | None = None,
    notes: str = "",
) -> str | None:
    """
    Log a session summary. Call this when the user closes the app
    or after a period of inactivity.
    """
    if not BRAIN_PATH.exists():
        return None

    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    filepath = JOURNAL_DIR / f"{today}-mahoraga-session.md"

    if not filepath.exists():
        filepath.write_text(f"# Mahoraga session — {today}\n\n", encoding="utf-8")

    summary = f"\n---\n\n### {now} — Session summary\n\n"
    summary += f"- **Tasks completed:** {tasks_completed}\n"
    summary += f"- **Total cost:** ${total_cost:.4f}\n"

    if agents_used:
        summary += f"- **Agents used:** {', '.join(set(agents_used))}\n"

    if notes:
        summary += f"\n{notes}\n"

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(summary)

    return str(filepath)


def log_decision(
    decision: str,
    reasoning: str = "",
    context: str = "mahoraga",
) -> str | None:
    """
    Log a routing or architecture decision to brain/decisions/log.md.
    Call this when the orchestrator makes a significant decision worth remembering.
    """
    if not BRAIN_PATH.exists():
        return None

    decisions_dir = BRAIN_PATH / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    decisions_log = decisions_dir / "log.md"

    today = date.today().isoformat()

    if not decisions_log.exists():
        decisions_log.write_text("# Decision Log\n\nAuto-appended by Mahoraga.\n", encoding="utf-8")

    entry = f"\n\n---\n\n## {today} — {decision}\n\n"
    if reasoning:
        entry += f"**Reasoning:** {reasoning}\n\n"
    entry += f"**Context:** {context}\n"

    with open(decisions_log, "a", encoding="utf-8") as f:
        f.write(entry)

    return str(decisions_log)


# Quick test
if __name__ == "__main__":
    result = log_task_completion(
        task_title="Test task",
        agent_used="ollama",
        output_preview="2+2=4",
        cost=0.0,
        quality_score=9.0,
        duration_seconds=1.2,
    )
    if result:
        print(f"Logged to: {result}")
        with open(result, encoding="utf-8") as f:
            print(f.read())
    else:
        print(f"Brain path not found at {BRAIN_PATH}")
