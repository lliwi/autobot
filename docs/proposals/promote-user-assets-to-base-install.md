# Proposal: Promote User Tools and Skills to Base Installation

## Status

Proposed.

## Context

Autobot agents can create useful workspace-level assets while solving real user tasks:

- `skills/<slug>/SKILL.md`
- `skills/<slug>/skill.py`
- `tools/<slug>/manifest.json`
- `tools/<slug>/tool.py`

Some of these assets are generic enough to become part of the base installation so that future agents or workspaces can reuse them without manual copying.

Today, promotion from a user workspace to the base install is not represented as a first-class workflow. This creates several risks:

- useful tools remain trapped in one workspace;
- manual copy/paste can lose context or metadata;
- dependencies may not be installed in the base environment;
- secrets or environment-specific assumptions may leak into shared code;
- rollback and ownership are unclear.

## Goal

Add a safe, auditable system that lets a user choose which workspace tools and skills should be promoted into the base Autobot installation.

The workflow must preserve security, reviewability, reversibility, and traceability.

## Non-goals

- Automatically promote every created tool or skill.
- Promote credentials or secrets.
- Bypass reviewer/admin approval.
- Modify protected core areas without the existing approval process.
- Guarantee that every workspace-specific integration is suitable for global use.

## Proposed workflow

```text
Workspace asset
      ↓ user selects promote
Promotion candidate
      ↓ automated checks
Review package
      ↓ reviewer/admin approval
Base installation asset
      ↓ available to future agents/workspaces
```

## User experience

A user or agent should be able to request promotion explicitly, for example:

```text
Promote the matrix-audio-handler tool to the base install.
```

The system should then:

1. Identify the asset and its files.
2. Build a promotion candidate.
3. Run validation checks.
4. Produce a reviewable patch or PR.
5. Require approval before merging into the base install.
6. Record promotion metadata.

## Asset types

### Skills

Supported files:

- `skills/<slug>/SKILL.md`
- optional `skills/<slug>/skill.py`
- optional tests/docs/examples if present.

### Tools

Supported files:

- `tools/<slug>/manifest.json`
- `tools/<slug>/tool.py`
- optional tests/docs/examples if present.

## Promotion manifest

Each promoted asset should include metadata, either in an existing manifest or in a generated promotion manifest.

Suggested fields:

```json
{
  "slug": "matrix-audio-handler",
  "type": "tool",
  "source_agent": "optimus",
  "source_workspace": "optimus",
  "promoted_by": "user-or-admin-id",
  "promoted_at": "2026-04-26T00:00:00Z",
  "version": "1.0.0",
  "dependencies": ["openai-whisper", "imageio-ffmpeg"],
  "credentials": [],
  "security_review": {
    "status": "required",
    "reviewer": null,
    "notes": []
  },
  "runtime": {
    "timeout_seconds": 600,
    "network_access": false,
    "filesystem_access": "workspace-relative"
  }
}
```

## Validation checks

Before a promotion candidate can be approved, the system should run checks.

### Required checks

- Asset slug is valid and unique in base install.
- Required files exist.
- Python files compile.
- Tool manifest is valid JSON.
- Tool handler entrypoint exists.
- No obvious secrets are embedded.
- No absolute workspace-specific paths are hardcoded.
- No destructive commands are executed without confirmation.
- Dependencies are declared.
- Credential names are documented but credential values are absent.
- Network endpoints are configurable.
- Files are small enough for review.

### Recommended checks

- Unit tests pass if present.
- Tool can run in dry-run mode if applicable.
- Skill documentation includes:
  - purpose;
  - when to use;
  - inputs;
  - outputs;
  - dependencies;
  - credentials;
  - safety notes;
  - examples.

## Dependency policy

Promoted assets must not silently install dependencies at runtime.

The promotion system should:

1. Extract dependency requirements.
2. Compare them with base environment packages.
3. Generate an install request or base package update.
4. Block promotion if required packages are unavailable or unapproved.

Dependencies should be tracked separately from secrets.

## Credential policy

Credentials must never be promoted.

Allowed:

- credential names;
- credential descriptions;
- expected credential type, such as token or username/password;
- setup instructions.

Forbidden:

- token values;
- passwords;
- copied `.env` values;
- provider secrets in examples.

## Review model

Promotion should create a normal auditable change, preferably as a GitHub pull request.

Suggested reviewers:

- `reviewer` agent for automated code/security review;
- human admin for final approval;
- optional domain owner for integrations touching external services.

## Storage model

The base installation can expose promoted assets from a shared directory, for example:

```text
base_assets/
  skills/
    <slug>/
      SKILL.md
      skill.py
      promotion.json
  tools/
    <slug>/
      manifest.json
      tool.py
      promotion.json
```

At new agent/workspace creation time, the platform can copy or mount approved base assets.

## Versioning and rollback

Each promoted asset should have a version.

Recommended rules:

- first promotion starts at `1.0.0`;
- compatible documentation or validation changes increment patch;
- behavior changes increment minor;
- breaking interface changes increment major;
- rollback restores the previous approved version.

Promotion metadata should preserve:

- source workspace;
- source commit or patch ID when available;
- approving reviewer;
- timestamp;
- previous version.

## Conflict handling

If an asset with the same slug already exists in base install, the system should require one of:

- update existing asset;
- promote under a new slug;
- reject promotion.

No silent overwrite should occur.

## Security constraints

Promotion must be blocked or require elevated review when assets:

- access credentials;
- access network services;
- execute shell commands;
- write files;
- call external APIs;
- perform destructive operations;
- modify agent/core/runtime configuration.

Assets must obey existing platform levels:

- L1: workspace memory and new skills/tools can be auto-applied where allowed;
- L2: existing tools/skills/docs require review;
- L3: core, OAuth, DB, and security-sensitive systems remain protected.

## Suggested implementation steps

### Phase 1 — Proposal-only promotion command

Add a command/tool that packages selected assets into a reviewable PR without modifying base install directly.

Inputs:

```json
{
  "asset_type": "tool|skill",
  "slug": "matrix-audio-handler",
  "target": "base-install",
  "reason": "Reusable Matrix audio transcription handler"
}
```

Outputs:

```json
{
  "ok": true,
  "status": "pr_created",
  "pr_url": "https://github.com/...",
  "checks": [...],
  "warnings": [...]
}
```

### Phase 2 — Automated validation

Add static checks and metadata extraction.

### Phase 3 — Base asset loader

Teach the platform to expose approved base assets to new workspaces.

### Phase 4 — Admin UI

Allow admins to view, approve, reject, update, and rollback promoted assets.

## Acceptance criteria

- A user can explicitly select a workspace skill/tool for promotion.
- The system creates an auditable patch or PR.
- Promotion includes metadata and dependency information.
- Embedded secrets are detected and blocked.
- Reviewer/admin approval is required before base installation changes.
- Promoted assets are available to future agents/workspaces after approval.
- Rollback path is documented and tested.

## Example candidates from current usage

Potential future promotion candidates:

- `local-audio-transcriber`
- `matrix-audio-handler`
- `homeassistant-assist-token`
- `github-pr-creator-token`
- `notion-skill-doc-auditor-token`

Each candidate should still pass review individually before inclusion in the base install.
