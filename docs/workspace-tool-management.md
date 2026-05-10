# Workspace tool management

This document defines the lifecycle and migration rules for Autobot workspace tools.
It exists to avoid stale catalog entries such as `example-tool-v2` being shown as
active tools after the code has already moved to `example-tool`.

## Policy

1. **Use the system version field**: every tool keeps its version in
   `tools/<slug>/manifest.json` under `version`.
2. **Do not encode versions in names**: names such as `cloudflare-csp-updater-token-v3`
   are invalid for active tools. Use `cloudflare-csp-updater-token` with
   `"version": "1.3.0"` instead.
3. **Use stable base + function names**: choose names that group naturally by
   domain and action, for example:
   - `cloudflare-csp-updater-token`
   - `cloudflare-firewall-reader-token`
   - `portainer-containers-agentcred`
   - `notion-page-style-rewriter-token`
4. **Remove superseded tools**: once a newer implementation is consolidated into
   the canonical slug, obsolete `-vN` directories and catalog records must be
   unregistered, not merely marked deprecated.
5. **Keep variants only when they represent different behavior**, not versions:
   - acceptable: `*-token`, `*-agentcred`, `*-reader`, `*-publisher`;
   - not acceptable: `*-v2`, `*-v3`, `*-new`, `*-final`.

## Why filesystem cleanup is not enough

The runtime may expose tools from an internal registry/catalog in addition to the
workspace filesystem. Removing `tools/example-v2/` can leave stale catalog rows if
registration is append-only or cache-based. A robust system must reconcile both:

- filesystem source of truth (`tools/<slug>/manifest.json`, `tool.py`);
- persisted tool registry/catalog entries;
- generated inventory such as `TOOLS.md`;
- references from skills and docs.

## Desired reconciliation flow

1. Discover installed tools from the workspace filesystem.
2. Discover registered tools from the platform registry.
3. Compute canonical slugs by stripping only semantic suffixes matching `-v[0-9]+`.
4. For each canonical group:
   - choose the newest/best implementation;
   - move it to `tools/<canonical>/`;
   - set `manifest.name = <canonical>`;
   - set/increment `manifest.version`;
   - remove `deprecated`, `replacement`, and stale `supersedes` references;
   - delete obsolete `tools/<canonical>-vN/` directories;
   - unregister or disable obsolete registry rows.
5. Regenerate `TOOLS.md` from active registry/filesystem state.
6. Scan skills/docs for obsolete names and rewrite references to canonical names.
7. Fail CI if a new `tools/*-vN` directory appears.

## CLI utility

This PR adds `scripts/workspace_tools_manager.py`, a dependency-free utility that
can audit and repair existing installations.

### Audit only

```bash
python scripts/workspace_tools_manager.py --root . --json
```

Example failing output:

```json
{
  "ok": false,
  "tool_count": 3,
  "versioned_tool_dirs": ["cloudflare-csp-updater-token-v3"],
  "findings": [
    {
      "severity": "error",
      "code": "VERSION_IN_NAME",
      "path": "/workspace/tools/cloudflare-csp-updater-token-v3",
      "message": "Tool name 'cloudflare-csp-updater-token-v3' encodes a version; use 'cloudflare-csp-updater-token' and manifest.version instead.",
      "fix": "migrate-versioned"
    }
  ]
}
```

### Repair existing installations

Dry run:

```bash
python scripts/workspace_tools_manager.py --root . --repair
```

Apply:

```bash
python scripts/workspace_tools_manager.py --root . --repair --apply
```

What it fixes:

- `tools/foo-v2` + `tools/foo` -> keeps the best implementation at `tools/foo`;
- `tools/foo-v3` only -> renames it to `tools/foo`;
- missing `manifest.version` -> adds `0.1.0`;
- `manifest.name` mismatch -> syncs it with the directory name;
- stale `supersedes`/`replacement` references to versioned tools -> removes them;
- docs/skills references to `*-vN` -> reports them for manual or automated rewrite.

## Registry/catalog integration recommendation

The platform should expose a management API/command with these operations:

```python
class ToolRegistry:
    def list_registered_tools(self, agent_id: str) -> list[RegisteredTool]: ...
    def register_or_update(self, agent_id: str, slug: str, manifest: dict, code_hash: str) -> None: ...
    def unregister(self, agent_id: str, slug: str, reason: str) -> None: ...
    def disable(self, agent_id: str, slug: str, reason: str) -> None: ...
```

Suggested reconciliation pseudocode:

```python
fs_tools = discover_tools(workspace / "tools")
registry_tools = registry.list_registered_tools(agent_id)
active_fs_slugs = {tool.slug for tool in fs_tools if not tool.versioned}

for registered in registry_tools:
    if registered.slug not in active_fs_slugs:
        registry.unregister(agent_id, registered.slug, reason="not present in filesystem source of truth")

for tool in fs_tools:
    if tool.versioned:
        raise PolicyError(f"Version encoded in tool name: {tool.slug}")
    registry.register_or_update(agent_id, tool.slug, tool.manifest, code_hash(tool.tool_path))
```

## CI guard

Add a CI step or pre-merge check:

```bash
python scripts/workspace_tools_manager.py --root . --json
```

The command exits with status `2` when any error-level finding exists, so it can
block PRs that introduce `tools/*-vN` or missing manifests.

## Migration cases covered

### Case 1: base + v2 exist

Before:

```text
tools/mealie-recipe-reader/
tools/mealie-recipe-reader-v2/
```

After repair:

```text
tools/mealie-recipe-reader/       # contains best implementation
tools/mealie-recipe-reader-v2/    # removed
```

### Case 2: only a versioned tool exists

Before:

```text
tools/cloudflare-firewall-reader-token-v3/
```

After:

```text
tools/cloudflare-firewall-reader-token/
```

### Case 3: credential variants are legitimate

These are not versions and should remain separate:

```text
tools/notion-page-search-token/
tools/notion-page-search-agentcred/
```

### Case 4: stale registry entry remains after filesystem cleanup

If the UI still shows `cloudflare-csp-updater-token-v3` after deleting the folder,
the registry must be reconciled/unregistered. The filesystem repair cannot remove
database rows by itself; the platform reconciliation API above is required.

## Developer checklist

- [ ] Run audit and capture JSON report.
- [ ] Run repair dry-run.
- [ ] Apply repair in a branch.
- [ ] Run tests: `python -m pytest tests/test_workspace_tools_manager.py`.
- [ ] Reconcile platform registry/catalog against filesystem active tools.
- [ ] Regenerate `TOOLS.md`.
- [ ] Verify UI no longer lists obsolete `-vN` tools.
- [ ] Add CI guard to prevent regressions.
