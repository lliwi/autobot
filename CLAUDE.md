# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Autobot** is a personal AI agent platform built on Python + Flask, designed for multi-agent architecture with self-improvement capabilities. It has two main interaction surfaces: a **web gateway** (chat, admin dashboard, observability) and a **Matrix** messaging channel. The project operates exclusively with OpenAI Codex via OAuth for the MVP.

The requirements spec is in [requisitos.md](requisitos.md) (Spanish). Refer to it for detailed data models, API contracts, and implementation phases.

## Development (Docker only)

All commands run inside Docker. Never install dependencies locally.

```bash
# Start all services
docker compose up -d

# Rebuild after dependency changes
docker compose build web

# Run database migrations
docker compose run --rm web flask db migrate -m "description"
docker compose run --rm web flask db upgrade

# Create admin user
docker compose run --rm web flask create-admin --email admin@autobot.local --password admin123

# Run tests
docker compose run --rm web pytest

# View logs
docker compose logs -f web
```

The app runs at `http://localhost:5000`. Admin credentials: `admin@autobot.local` / `admin123`.

## Tech Stack

- **Python 3.11+**, **Flask**, SSE for chat streaming
- **SQLAlchemy** + **Alembic** for ORM and migrations
- **PostgreSQL** as primary database, **Redis** for broker/cache
- **Pydantic** for schema validation
- **Flask + Jinja + HTMX** for the admin dashboard
- **matrix-nio** for Matrix integration (Phase 2)

## Architecture (Key Components)

- **Gateway Web (Flask)**: REST API, WebSocket/SSE streaming, admin dashboard, auth, metrics
- **Agent Runtime**: context building from workspace files, tool/skill selection, model invocation, sub-agent coordination
- **Channel Adapter (Matrix)**: event consumption, message normalization, session mapping
- **Workspace Manager**: manages persistent files (`SOUL.md`, `AGENTS.md`, `MEMORY.md`, `TOOLS.md`), skills, tools, versioning
- **Scheduler**: heartbeat, cron jobs, deferred tasks, retry/recovery
- **Self-Improvement Engine**: detects gaps, proposes changes (PatchProposal), generates diffs, runs tests, supports approval/rollback
- **Observability Layer**: per-run metrics, token usage, cost estimation, error tracking

## Workspace Structure (Per Agent)

Each agent has a workspace at `/workspaces/<agent_id>/` containing:
- `SOUL.md` — identity, style, principles, limits (rarely changes)
- `AGENTS.md` — catalog of agents/sub-agents
- `MEMORY.md` — persistent summarized memory
- `TOOLS.md` — available tools inventory
- `skills/`, `tools/`, `agents/`, `runs/`, `patches/`, `tests/` directories

## Self-Improvement Security Levels

This is critical to the project's design:
- **Level 1 (auto-allowed)**: edit `MEMORY.md`, create new skills/tools, edit workspace manifests
- **Level 2 (requires approval)**: modify existing skill Python files, create sub-agents, modify `AGENTS.md`/`TOOLS.md`
- **Level 3 (prohibited in MVP)**: modify Flask core, OAuth layer, DB/migrations, security policies

All changes must be auditable, reversible (one-click rollback), and pass basic tests.

## Implementation Phases

1. **Core**: Flask app, PostgreSQL + SQLAlchemy, admin auth, web chat, basic agent runtime, workspace file reading, OpenAI Codex OAuth
2. **Channels & Scheduler**: Matrix integration, heartbeat + cron, full run/metrics persistence
3. **Skills & Tools**: dynamic registration, workspace loading, validation, dashboard panel
4. **Multi-agent**: sub-agents, task delegation, agent topology panel
5. **Self-improvement**: patch proposals, diffs, auto-tests, approval/rollback
6. **Hardening**: execution sandbox, advanced observability, fine security limits, cost optimization

## Key Design Principles

- **Gateway as control plane** — not coupled business logic
- **Workspace-first** — agent behavior depends on persistent, versionable files
- **Real multi-agent** — agents and skills as separable pieces
- **Safe self-improvement** — all code/prompt changes must be auditable
- **Plugin-extensible** — tools, skills, connectors addable without rewriting core
- **Observability from day 1** — logs, events, usage, metrics, traces with structured JSON logging
