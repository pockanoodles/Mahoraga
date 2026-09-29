# Agent-edit probe

Can a free local model, driven by an existing coding agent, make real edits to
a real repository without breaking it?

The cascade's arms answer single prompts and never see a repo, which is why the
delegation funnel excluded 1,299 of 1,613 logged actions as `edit-in-place`.
This probe measures what repo access would buy before anything is built for it.

## Method

**Tasks come from Mahoraga's own history.** For a commit C that changed both
source and tests, the task tree is `parent(C)` plus C's test changes: the tests
are present, the fix is not. The agent gets C's commit message and the names of
the test files, never the list of source files to edit.

Two gates, applied when a task is built (`prepare.py`):

- **A.** At C, the commit's own test files pass, so the task is satisfiable.
- **B.** At the task base, at least one of them fails, so the task discriminates.

22 candidate commits in, 18 tasks out; the drops and their reasons are in
`dropped.json`. Tasks span 1 to 164 changed source lines, and 10 of the 18
touch two or three files.

**Scoring** (`run.py`). Test files are restored from the base before scoring,
so an agent cannot pass by editing them, and any attempt that touched them is
flagged. An attempt is *resolved* when:

- every fail-to-pass test passes, and
- no pass-to-pass test regresses, checked against the **full** non-slow suite
  (450 to 1,450 tests depending on the commit).

Each attempt also records the edited files, the diff, the time taken, and a
failure label: `no-edit`, `wrong-file`, `wrong-fix`, `partial`, `broke-other`,
or `partial+broke`.

**Arms.** Aider 0.86.2 with the `diff` (search/replace) edit format, 2,048-token
repo map, generation capped at 8,192 tokens per call, 20-minute limit per
attempt. Two conditions:

- `blind`: one shot from the task text.
- `feedback`: `--auto-test` on the task's test files, so Aider sees failing
  output and gets its built-in retries.

**Isolation.** Every task runs in a `git clone --shared` under
`~/.mahoraga-v2/agent_edit/work/`, and pytest runs with `HOME` pointed at a
scratch directory. This is not optional: tests at older commits write into
their repo's `brain/` and `~/.mahoraga`, and during the first run they did so,
harmlessly, inside the clones.

## Run it

```bash
.venv/bin/python experiments/agent_edit/prepare.py   # build + gate tasks (~25 min)
zsh experiments/agent_edit/chain.sh                  # all arms, resumable
.venv/bin/python experiments/agent_edit/report.py    # tables
```

`prepare.py` rewrites `tasks.jsonl`, because each task's base commit is created
locally. Needs Ollama with the arm models pulled, and `aider` on PATH.

## Results (2026-09-28, 16 GB M-series)

| Arm | Resolved | Broke other code | Median time |
| --- | --- | --- | --- |
| qwen3.5, blind | **5 / 18 (28%)** | 7 | 8.4 min |
| qwen3.5, feedback | **5 / 18 (28%)** | 5 | 13.1 min |
| granite4.1-8b, blind | pending | | |
| granite4.1-8b, feedback | pending | | |

- **About one in four real edits lands**, including two three-file changes
  solved end to end.
- **The test gate caught every failure.** No failing attempt would have been
  served. The common failure is *over-reach*: the model rewrites neighbouring
  code it had no reason to touch, and the full-suite check is what catches it.
- **Feedback does not raise the solve rate.** It trades one task for another
  and roughly halves regressions, at about 1.5× the time.
- **An attempt costs 8 to 13 minutes on 16 GB**, which rules out interactive
  use. The shape it supports is asynchronous: a free first attempt, tests
  decide, escalate the rest.
- **granite4.1-8b cannot use the search/replace format** (0/7 before the pause
  at 00:14). It writes search blocks for code that does not exist in the file,
  so the edits never apply.

## Limits

- n = 18, from one repository, whose tests were written alongside each fix.
- Commit messages are more detailed than a typical issue, which makes the task
  easier than a cold bug report.
- The repository is public, so contamination is possible though unlikely: it
  was created in March 2026.
- "Resolved" means the tests pass. One solved task (`a7d91e2`) skipped part of
  the original change that the tests do not cover.
- 4 of qwen's 13 blind failures are partly environmental rather than pure
  capability: two edit-format rejections, one context blow-up from a 30 MB
  tracked log at that commit, and one timeout.
