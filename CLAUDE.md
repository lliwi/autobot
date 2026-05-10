# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Autobot** is a personal AI agent platform built on Python + Flask. Agents live in per-agent workspaces, use tools and skills loaded from the filesystem, and can propose self-improvement patches subject to a security policy. The requirements spec is in [requisitos.md](requisitos.md) (Spanish).

## Development (Docker only)

All commands run inside Docker. Never install dependencies locally.

```bash
# Start / stop
docker compose up -d
docker compose logs -f web

# Rebuild after dependency changes
docker compose build web

# Database migrations (run after git pull or restore)
docker compose run --rm web flask db upgrade
docker compose run --rm web flask db migrate -m "description"   # create migration
docker compose run --rm web flask db downgrade                   # revert last

# First-time setup (runs migrations, creates admin, prompts for config)
docker compose run --rm web flask onboard

# Run all tests
docker compose run --rm web pytest

# Run a single test file or test
docker compose run --rm web pytest tests/test_scheduler_service.py
docker compose run --rm web pytest tests/test_patch_service.py::test_propose_patch

# Backup / restore
scripts/backup.sh --include-env --include-secrets   # writes to ./backups/
scripts/restore.sh                                   # restores most recent backup
scripts/restore.sh --list

# Export/import bundle manually (use /app/... not /tmp/ — /tmp is ephemeral in Docker)
docker compose exec web flask export-bundle -o /app/backups/bundle.tar.gz --include-env --include-secrets
docker compose exec web flask import-bundle -i /app/backups/bundle.tar.gz --overwrite
```

App runs at `http://localhost:5000`.

## Workspace Layout

Three distinct workspace directories with different roles:

```
workspaces/
  _template/skills/<slug>/    ← golden copy; new agents inherit from here
  _template/tools/<slug>/     ← same for tools
  _global/skills/<slug>/      ← live skills read by the app and DB sync
  _global/tools/ (unused)     ← tools are per-agent, not global
  <agent-slug>/               ← per-agent workspace
    SOUL.md, MEMORY.md, AGENTS.md, TOOLS.md
    skills/<slug>/            ← agent-local skill copy (deprecated; global preferred)
    tools/<slug>/tool.py      ← agent-specific tools
    tools/<slug>/manifest.json
```

**Critical distinction:**
- Skills are **global** — they live in `_global/skills/` and are shared across agents via `AgentSkill` junction rows. Updating `_template/skills/` does NOT update the running DB version; click **Reload** in the dashboard or it auto-syncs when the template version is ahead.
- Tools are **per-agent** — each agent owns its `tools/<slug>/` directory. Promotion copies a tool to `_template/tools/` for new agents, then broadcasts to existing agents.

## Skill / Tool Naming Policy

- Version goes in `manifest.json` under `"version"`, **never** in the directory name.
- `tools/my-tool-v2/` is invalid. Use `tools/my-tool/` with `"version": "0.2.0"`.
- Credential variants are acceptable: `*-token`, `*-agentcred` (different behavior, not different versions).
- `scripts/workspace_tools_manager.py --root . --json` audits and `--repair --apply` fixes naming violations.

## Agent Runtime Flow

`chat_service.run_agent_non_streaming()` / SSE streaming both converge on:

1. **`context_builder.build_context()`** — assembles system prompt from workspace files (`SOUL.md`, `MEMORY.md`, `TOOLS.md`, skill `SKILL.md` files), retrieves session history, enforces token budget.
2. **`model_client.stream_chat_completion()`** — calls the LLM (Codex via OAuth).
3. **`agent_runner.run()`** — tool-call loop (up to `MAX_TOOL_ROUNDS`, default 20). Each round: model response → `tool_executor.execute()` → result capped at 20 000 chars for context → next round.
4. **`tool_executor`** dispatches to `tool_registry` (built-in tools) or `tool_subprocess_runner` (workspace `tool.py` files executed in agent's `.venv`).

Action-first enforcement: if the model responds with intent text instead of a tool call on an action-type request, an `_ENFORCE_ACTION_NUDGE` system message is injected and the round retried.

## Self-Improvement (PatchProposal)

Security levels gate what agents can self-modify:

- **L1 (auto-allowed)**: `MEMORY.md`, new tool/skill creation, manifest edits
- **L2 (requires human approval)**: modifying existing `tool.py`/`skill.py`, `AGENTS.md`/`TOOLS.md`
- **L3 (prohibited)**: Flask core, migrations, auth, security policies

All proposals go through `patch_service` → `patch_validator` (JSON/AST/handler/smoke-import checks) → optional `review_service` (sub-agent reviewer) → `apply_patch` / `rollback_patch`.

## DB Models (key relationships)

```
Agent ──< Tool           (per-agent; enabled flag)
Agent ──< AgentSkill >── Skill   (global skills, junction table)
Agent ──< Session ──< Message
Agent ──< Run ──< ToolExecution
Agent ──< PatchProposal
Agent ──< ScheduledTask
```

`Skill` rows are synced from `_global/skills/` via `sync_global_skills_to_db()`. `Tool` rows are synced from agent workspace via `sync_workspace_tools_to_db()`.

## Promotion Flow (tool/skill → template)

1. Agent workspace has a battle-tested tool/skill.
2. Dashboard → Bundle (downloads tar.gz with diff + `PROMOTION.md`) **or** PR (auto-creates GitHub branch + PR if `GH_TOKEN`/`AUTOBOT_GITHUB_REPO` are set).
3. PR merged → `_template/` updated in git.
4. **Reload** in Skills dashboard (or `flask db upgrade` equivalent) syncs `_global/` from `_template/` when template version is ahead.
5. Broadcast to existing agents (optional).

## CLI Commands Reference

| Command | Purpose |
|---|---|
| `flask onboard` | Interactive first-time setup |
| `flask db upgrade/migrate/downgrade` | Schema migrations |
| `flask create-admin` | Create admin user |
| `flask setup-default-agents` | (Re)create optimus + reviewer agents |
| `flask setup-matrix` | Configure Matrix channel only |
| `flask codex-login/logout/status` | Manage Codex OAuth token |
| `flask export-bundle` | Snapshot installation to tar.gz |
| `flask import-bundle` | Restore snapshot |

## Scheduler

`app/worker/scheduler.py` runs via `worker.py` (separate process). Two sync loops:
- `_sync_jobs` every 30 s — syncs `ScheduledTask` rows to APScheduler jobs. Passes timezone as **string** to `CronTrigger.from_crontab()` (not `ZoneInfo` object).
- `_drain_review_queue` every 15 s — processes pending `ReviewEvent` rows.

`_execute_cron_task()` includes a pre-dispatch guard: if current time is >300 s past the last valid cron fire time (e.g. misfire near midnight crossing a day boundary), the task is skipped and logged.

## Self-Improvement Security Levels

- **Level 1 (auto-allowed)**: edit `MEMORY.md`, create new skills/tools, edit workspace manifests
- **Level 2 (requires approval)**: modify existing skill/tool Python files, create sub-agents, modify `AGENTS.md`/`TOOLS.md`
- **Level 3 (prohibited)**: modify Flask core, OAuth layer, DB/migrations, security policies
