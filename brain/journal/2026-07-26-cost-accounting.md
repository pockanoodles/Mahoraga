# 2026-07-26 — Cost accounting: claude-cli arm, real cost capture, counterfactual report

**Branch:** `feat/cost-accounting` → PR #19. Tests 1292 → 1320 green.

## Why

The portfolio's one open claim is a `[cost/volume metric TBD]` placeholder. Kaito's framing settled this session: Mahoraga's job is **credit conservation, not beating Anthropic** — route locally when good enough, escalate when not. The resume number is cost saved *at acceptable quality*, which needs (a) real cost capture, (b) a counterfactual, (c) a quality-retention measurement. Constraint discovered up front: Kaito has Claude **Max, no API key** — so the cloud arm must be the `claude` CLI (apps/connectors only), which turns out to report `total_cost_usd` + full usage in `--output-format json` even under subscription auth.

## What shipped

- `ClaudeCliWorker` + adapter (`claude-cli`, disabled in agents.yaml) — subprocess `claude -p` with prompt on stdin, `--disallowedTools`, isolated cwd, kill/reap on abandonment, zero-cost fallback, cache-creation-aware fallback pricing.
- `resolve_cost()` in tracking: worker cost → `task_metrics.cost_usd` + `cost_ledger` + `TaskOutcome` → decision log (φ_cost live). Ledger writes best-effort everywhere.
- Ollama worker now emits `prompt_tokens`; SDK claude worker emits usage+cost (fairness: no arm may be cost-invisible to the bandit).
- `orch bench report cost` — sixth report subcommand, fully offline. Local rows at frozen rates (`PRICING_AS_OF`), cloud rows at recorded actual, unpriced + missing-prompt disclosure, gross vs success-only avoided spend.

## Decisions & findings worth remembering

1. **Cloud rows' counterfactual = their actual cost.** Token re-pricing underpriced a real CLI call ~100× because `input_tokens` excludes cache tokens — a fresh Claude Code call writes ~35K tokens of system-prompt cache (~$0.21). Caught by adversarial review before any number was quoted.
2. **All 961 historical rows have `prompt_tokens=0`** — Ollama never emitted it. The $1.55 avoided figure is therefore a disclosed floor. Numbers produced before the fix would have been silently wrong in *both* directions (local understated, savings% inflated).
3. **The honest comparison is per-task cost of the real alternative** (a full Claude Code invocation ≈ $210/1k tasks), not bare token math ($1.61/1k on short bench prompts). Phase 4 measures it instead of estimating.
4. **`total_cost_usd: 0.0` must be treated as missing** — some CLI versions report 0 under subscription auth; the naive check would have silently zeroed the entire feature.
5. Old pricing table had Opus 4.6 at $15/$75 (3× real) — would have inflated savings indefensibly. Rates frozen + dated; prefix-match for dated model IDs.
6. Security posture for CLI arms: a user-derived prompt must not run in a cwd whose `.claude/settings.json` pre-authorizes tools. Isolated-cwd + disallowedTools is now the pattern; codex/aider (intentional file-writers) keep workdir.

## Deferred (documented in PR #19)

Per-attempt cost accumulation (retries/escalation misattribute), eval-path ledger writes, gateway-path task_metrics rows, `agent_name`-prefix local detection.

## Next

Phase 4 head-to-head over `experiments/prompts_verifiable.jsonl` with `claude-cli` enabled (exact commands in current_state.md). Possibly grow the verifiable bank first — 18 prompts is thin for a quotable pass@1.
