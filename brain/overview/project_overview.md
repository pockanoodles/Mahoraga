# Mahoraga — Project Overview

## What it is

Mahoraga is a local-first agent orchestrator that learns what works best on your machine and makes free/open LLMs actually usable.

It turns the chaos of fast-moving public LLMs into a usable, measurable workflow.

## Two operating modes

**Standalone orchestrator** — Mahoraga is the front-end. User gives one input, Mahoraga picks the best available free/local/public agent, explains what it did, tracks the outcome.

**Claude-connected routing layer** — Mahoraga sits behind Claude via MCP. Easy or cheap tasks get offloaded to free/local agents. Claude handles what's left. Cost drops, flexibility rises.

Both modes use the same routing stack and memory layer.

## Target hardware

v1 is built for a 16 GB Mac. All routing decisions, benchmark conclusions, and hardware assumptions are made with this constraint in mind.

## What makes it different

- Routing policy is explicit, not a black box
- Benchmark data is visible and queryable
- The system learns at the orchestration layer (bandit routing, episodic memory, OLS weight learning)
- Hardware-aware agent selection — warm/cold state, RAM, chip family all factor in
- Works with free/public models: Qwen, Gemma, Codex, OpenCode, Goose, Gemini CLI

## What it is not

- Not a model training pipeline (no weight updates)
- Not a generic personal assistant
- Not a replacement for Claude on hard tasks

## v1 scope

- Adaptive bandit routing (LinUCB + Thompson + UCB1)
- OLS weight learning for reward signals
- Episodic memory (hnswlib HNSW index)
- MCP server exposing routing tools to Claude
- Parallel wave execution for batch tasks
- Web chat + Telegram interfaces
- Repo-local markdown brain (this folder)
- Structured SQLite runtime storage
