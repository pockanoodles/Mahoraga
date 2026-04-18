# Mahoraga ↔ Claude Code Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Mahoraga's MCP server into Claude Code with a skill-based toggle and PostToolUse hook so subtasks route to open source models on demand, and rewrite the README to lead with research engine + routing math.

**Architecture:** A flag file (`~/.claude/mahoraga-active`) is the source of truth for toggle state. A rewritten `/mahoraga` skill flips it and loads routing rules into context. A PostToolUse hook on the Skill tool fires after every skill load and reinjects routing reminders when the flag is active. MCP registration makes the 7 Mahoraga tools available in every Claude Code session.

**Tech Stack:** bash, JSON (settings.json), Markdown (skills + README)

---

## File Map

| File | Action | Task |
|------|--------|------|
| `~/.claude/settings.json` | Add `mcpServers` block | Task 1 |
| `~/.claude/scripts/mahoraga-routing.sh` | Create hook script | Task 2 |
| `~/.claude/settings.json` | Add PostToolUse Skill hook to existing array | Task 3 |
| `~/.claude/skills/mahoraga.md` | Rewrite from reference → active toggle skill | Task 4 |
| `README.md` | Rewrite tagline, reorder sections, add research framing | Task 5 |

**Parallel opportunities:**
- Tasks 2, 4, 5 are fully independent — dispatch together
- Task 1 must precede Task 3 (both touch settings.json)

---

## Task 1: MCP Registration in settings.json

**Files:**
- Modify: `~/.claude/settings.json`

- [ ] **Step 1: Verify current settings.json structure**

```bash
python3 -m json.tool ~/.claude/settings.json | head -20
```

Expected: valid JSON with keys `permissions`, `hooks`, `effortLevel` etc. No `mcpServers` key.

- [ ] **Step 2: Add mcpServers block**

Read `~/.claude/settings.json` fully, then add `mcpServers` as a new top-level key. The complete addition:

```json
"mcpServers": {
  "mahoraga": {
    "command": "python",
    "args": ["-m", "backend.mcp.server"],
    "cwd": "/Users/kaitosoeno/Projects/Mahoraga"
  }
}
```

Add it before the closing `}` of the root object, after `"effortLevel": "max"`.

- [ ] **Step 3: Validate JSON**

```bash
python3 -m json.tool ~/.claude/settings.json > /dev/null && echo "VALID" || echo "INVALID — fix before continuing"
```

Expected: `VALID`

- [ ] **Step 4: Verify mcpServers key exists**

```bash
python3 -c "import json; d=json.load(open('/Users/kaitosoeno/.claude/settings.json')); print(d['mcpServers'])"
```

Expected: `{'mahoraga': {'command': 'python', 'args': ['-m', 'backend.mcp.server'], 'cwd': '/Users/kaitosoeno/Projects/Mahoraga'}}`

---

## Task 2: Hook Script

**Files:**
- Create: `~/.claude/scripts/mahoraga-routing.sh`

- [ ] **Step 1: Create the script**

Write `~/.claude/scripts/mahoraga-routing.sh` with this exact content:

```bash
#!/bin/bash
# Fires after every Skill tool call. If Mahoraga routing is active, injects
# routing tier reminder into Claude Code context.
FLAG="$HOME/.claude/mahoraga-active"
[ -f "$FLAG" ] || exit 0

cat <<'ROUTING'
[Mahoraga ACTIVE] Route subtasks through Mahoraga, not the Agent tool:
  - File creation / boilerplate / formatting  → mcp__mahoraga__run_task  (Ollama/Qwen, $0)
  - Structured code with a clear spec         → mcp__mahoraga__run_task  (OpenCode/Goose)
  - Research / search / summarization         → mcp__mahoraga__run_task  (Gemini CLI)
  - 3+ independent subtasks                   → mcp__mahoraga__run_batch
  - Architecture / review / complex reasoning → handle directly in Claude Code

Every task prompt sent to Mahoraga must be self-contained: include file paths,
relevant existing code, and exact goal — open source workers have no conversation context.
ROUTING
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x ~/.claude/scripts/mahoraga-routing.sh
```

- [ ] **Step 3: Test — flag absent (silent)**

```bash
rm -f ~/.claude/mahoraga-active
bash ~/.claude/scripts/mahoraga-routing.sh
echo "exit: $?"
```

Expected: no output, `exit: 0`

- [ ] **Step 4: Test — flag present (routing reminder printed)**

```bash
touch ~/.claude/mahoraga-active
bash ~/.claude/scripts/mahoraga-routing.sh
```

Expected: prints the full `[Mahoraga ACTIVE]` block.

- [ ] **Step 5: Clean up test flag**

```bash
rm -f ~/.claude/mahoraga-active
```

---

## Task 3: Hook Registration in settings.json

**Files:**
- Modify: `~/.claude/settings.json`

Must run after Task 1 (same file).

- [ ] **Step 1: Read current PostToolUse array**

```bash
python3 -c "
import json
d = json.load(open('/Users/kaitosoeno/.claude/settings.json'))
import pprint
pprint.pprint(d['hooks']['PostToolUse'])
"
```

Expected: two existing entries — one matching `Edit|Write|Bash` and one matching `Agent`.

- [ ] **Step 2: Add Skill hook entry**

Read `~/.claude/settings.json`, then append a third entry to `hooks.PostToolUse`:

```json
{
  "matcher": "Skill",
  "hooks": [
    {
      "type": "command",
      "command": "bash /Users/kaitosoeno/.claude/scripts/mahoraga-routing.sh",
      "async": false
    }
  ]
}
```

The `PostToolUse` array should now have 3 entries: `Edit|Write|Bash`, `Agent`, and `Skill`.

- [ ] **Step 3: Validate JSON**

```bash
python3 -m json.tool ~/.claude/settings.json > /dev/null && echo "VALID" || echo "INVALID"
```

Expected: `VALID`

- [ ] **Step 4: Verify hook entry**

```bash
python3 -c "
import json
d = json.load(open('/Users/kaitosoeno/.claude/settings.json'))
skill_hooks = [h for h in d['hooks']['PostToolUse'] if h.get('matcher') == 'Skill']
print('Skill hooks:', len(skill_hooks))
print(skill_hooks)
"
```

Expected: `Skill hooks: 1` followed by the hook object.

---

## Task 4: Rewrite Toggle Skill

**Files:**
- Modify: `~/.claude/skills/mahoraga.md`

Current file is a passive reference. Rewrite it to be an active toggle with routing rules embedded.

- [ ] **Step 1: Read current file**

Read `~/.claude/skills/mahoraga.md` to confirm current content before overwriting.

- [ ] **Step 2: Overwrite with toggle skill**

Write `~/.claude/skills/mahoraga.md` with this exact content:

```markdown
---
name: mahoraga
description: Toggle Mahoraga routing on/off. When on, subtasks route through Mahoraga to open source models (Qwen, OpenCode, Gemini CLI, Goose) instead of Claude Code subagents, saving API credits. Use when budgeting or testing Mahoraga's routing.
type: skill
---

# /mahoraga — Routing Toggle

## Step 1: Flip the flag

Run this bash command to read and flip the toggle:

\`\`\`bash
FLAG="$HOME/.claude/mahoraga-active"
if [ -f "$FLAG" ]; then
  rm "$FLAG"
  echo "Mahoraga routing: OFF"
else
  touch "$FLAG"
  echo "Mahoraga routing: ON"
fi
\`\`\`

Report the new state to the user in one line: **Mahoraga routing: ON** or **Mahoraga routing: OFF**.

---

## When routing is ON

### Routing tiers

| Task type | Route to | MCP call |
|-----------|----------|----------|
| File creation, boilerplate, formatting | Ollama (Qwen) — $0 | `mcp__mahoraga__run_task` |
| Structured code with clear spec | OpenCode / Goose | `mcp__mahoraga__run_task` |
| Research, search, summarization | Gemini CLI | `mcp__mahoraga__run_task` |
| 3+ independent subtasks | Best-fit per task | `mcp__mahoraga__run_batch` |
| Architecture, review, complex reasoning | Claude Code | — |

### Task prompt rules

Open source workers have no conversation context. Every task prompt must be self-contained:
- Include all relevant file paths and function names
- State the exact goal — no pronouns, no "as we discussed"
- For code tasks: paste in the relevant existing code if the worker needs to read or modify it

### After Mahoraga tasks complete

1. Read the resulting files to verify quality
2. If output is wrong, fix it directly or retry with a more specific prompt
3. Run `mcp__mahoraga__routing_stats` after 5+ tasks to check if the bandit is converging

### Checking Mahoraga status

```bash
# Is the backend running?
curl -s http://localhost:8000/health | python3 -m json.tool

# Or use the MCP tool:
# mcp__mahoraga__health_check
```

If Mahoraga is not running: `cd ~/Projects/Mahoraga && python -m backend.main`
```

- [ ] **Step 3: Verify the file was written**

```bash
head -5 ~/.claude/skills/mahoraga.md
```

Expected: frontmatter with `name: mahoraga` and updated description.

- [ ] **Step 4: Test the toggle logic manually**

```bash
# Should create flag (ON)
FLAG="$HOME/.claude/mahoraga-active"
rm -f "$FLAG"
[ -f "$FLAG" ] && rm "$FLAG" && echo "OFF" || (touch "$FLAG" && echo "ON")
ls -la "$FLAG"

# Should remove flag (OFF)
[ -f "$FLAG" ] && rm "$FLAG" && echo "OFF" || (touch "$FLAG" && echo "ON")
ls "$FLAG" 2>/dev/null || echo "Flag removed — OFF confirmed"
```

Expected: first run prints `ON`, creates the file. Second run prints `OFF`, file is gone.

---

## Task 5: README Rewrite

**Files:**
- Modify: `README.md` in `/Users/kaitosoeno/Projects/Mahoraga/`

**Changes:**
1. Replace tagline in blockquote
2. Rename and reframe "What It Does" → "What It Does" (keep heading, update subtitle framing)
3. Add a "Research Engine" callout after What It Does
4. Move "How It Works" (the math section) immediately after the new Research Engine callout — before Architecture
5. Move Architecture diagram after How It Works
6. Cut the subtitle line "Any agent plugs in through the `AgentAdapter` interface." from What It Does
7. Keep everything else (benchmarks, quick start, motivation, related work, adapter interface, agent roster, roadmap, references) in their current relative order, just shifted down

- [ ] **Step 1: Read the full README**

Read `/Users/kaitosoeno/Projects/Mahoraga/README.md` in full.

- [ ] **Step 2: Replace tagline blockquote**

Find:
```
> Agent-agnostic LLM orchestration framework with online bandit routing. Unifies any AI coding agent (local or cloud) into an intelligent workflow with learned routing, quality evaluation, and real-time visual feedback.
```

Replace with:
```
An online bandit routing engine for heterogeneous AI agents. Local-first, research-capable, learns from every task.
```

(Remove the `>` blockquote syntax — this is now a plain paragraph, not a subtitle quote.)

- [ ] **Step 3: Cut ease-of-use line from What It Does**

Find and remove this line from the "What It Does" section:
```
Any agent plugs in through the `AgentAdapter` interface.
```

- [ ] **Step 4: Add Research Engine callout after What It Does**

After the "What It Does" section (before the Architecture `---` divider), insert:

```markdown
---

## Research Engine

Mahoraga routes research tasks to agents built for retrieval and synthesis — Gemini CLI for broad search and summarization, Qwen for reasoning-heavy questions, and escalation to Claude only when the task genuinely requires it. The bandit learns which agent performs best per task bucket from real routing decisions, not offline training data. No configuration needed — it improves with use.
```

- [ ] **Step 5: Move How It Works before Architecture**

The "How It Works" section (Task Classification, Adaptive Routing, Quality Evaluation, Warm Start subsections) currently appears after Quick Start. Move the entire "How It Works" section to appear immediately after the new "Research Engine" section, before Architecture.

The new section order in the file should be:
1. Header + tagline + badges + demo gif
2. What It Does
3. Research Engine (new)
4. How It Works (moved up)
5. Architecture
6. Where It Sits
7. Benchmark Results
8. Quick Start
9. Adapter Interface
10. Agent Roster
11. Run the Benchmark
12. Motivation
13. Related Work
14. Roadmap
15. References
16. License

- [ ] **Step 6: Verify file is valid Markdown and section order is correct**

```bash
grep "^## " /Users/kaitosoeno/Projects/Mahoraga/README.md
```

Expected output (in order — `## References` may or may not appear depending on whether the header is explicit):
```
## What It Does
## Research Engine
## How It Works
## Architecture
## Where It Sits
## Benchmark Results
## Quick Start
## Adapter Interface
## Agent Roster
## Run the Benchmark
## Motivation
## Related Work
## Roadmap
```

- [ ] **Step 7: Commit README changes**

```bash
cd /Users/kaitosoeno/Projects/Mahoraga
git add README.md
git commit -m "docs: rewrite README — lead with routing math and research engine, cut ease-of-use framing"
```

---

## Final Verification

After all tasks complete:

- [ ] **MCP registration present**

```bash
python3 -c "import json; d=json.load(open('/Users/kaitosoeno/.claude/settings.json')); print('mcpServers' in d)"
```

Expected: `True`

- [ ] **Hook script exists and is executable**

```bash
ls -la ~/.claude/scripts/mahoraga-routing.sh
```

Expected: file exists with `x` permission.

- [ ] **Hook registered**

```bash
python3 -c "
import json; d=json.load(open('/Users/kaitosoeno/.claude/settings.json'))
matchers = [h['matcher'] for h in d['hooks']['PostToolUse']]
print(matchers)
"
```

Expected: `['Edit|Write|Bash', 'Agent', 'Skill']` (order may vary)

- [ ] **Toggle skill updated**

```bash
grep "type: skill" ~/.claude/skills/mahoraga.md
```

Expected: `type: skill`

- [ ] **README tagline correct**

```bash
grep "online bandit routing engine" /Users/kaitosoeno/Projects/Mahoraga/README.md
```

Expected: the new tagline line.

- [ ] **Section order correct**

```bash
grep "^## " /Users/kaitosoeno/Projects/Mahoraga/README.md
```

Expected: Research Engine and How It Works appear before Architecture.
