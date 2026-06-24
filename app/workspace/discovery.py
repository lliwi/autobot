"""Discovery engine for workspace skills and tools.

Scans the filesystem, validates manifests, syncs with DB, and provides
dynamic tool loading for the agent runtime.
"""

import importlib.util
import logging
from pathlib import Path

from app.extensions import db
from app.models.skill import AgentSkill, Skill
from app.models.tool import AgentTool, Tool
from app.runtime.tool_registry import get_all_definitions as get_builtin_definitions
from app.workspace.manager import (
    get_global_skills_path,
    get_global_tools_path,
)
from app.workspace.manifest import load_manifest, validate_skill_manifest, validate_tool_manifest

logger = logging.getLogger(__name__)


# -- Filesystem discovery --


def discover_global_tools():
    """Scan _global/tools/ and return list of valid tool dicts."""
    tools_dir = get_global_tools_path()
    if not tools_dir.exists():
        return []

    results = []
    for tool_dir in sorted(tools_dir.iterdir()):
        if not tool_dir.is_dir():
            continue
        manifest_path = tool_dir / "manifest.json"
        if not manifest_path.exists():
            continue

        try:
            manifest = load_manifest(manifest_path)
        except ValueError as e:
            logger.warning(f"Skipping tool {tool_dir.name}: {e}")
            continue

        errors = validate_tool_manifest(manifest)
        if errors:
            logger.warning(f"Invalid tool manifest {tool_dir.name}: {errors}")
            continue

        results.append({
            "name": manifest["name"],
            "slug": tool_dir.name,
            "description": manifest.get("description", ""),
            "version": manifest.get("version", "0.1.0"),
            "parameters": manifest.get("parameters", {"type": "object", "properties": {}}),
            "timeout": manifest.get("timeout", 30),
            "path": f"tools/{tool_dir.name}",
            "manifest": manifest,
        })

    return results


def discover_global_skills():
    """Scan _global/skills/ and return list of valid skill dicts."""
    skills_dir = get_global_skills_path()
    if not skills_dir.exists():
        return []

    results = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        manifest_path = skill_dir / "manifest.json"
        if not manifest_path.exists():
            continue

        try:
            manifest = load_manifest(manifest_path)
        except ValueError as e:
            logger.warning(f"Skipping skill {skill_dir.name}: {e}")
            continue

        errors = validate_skill_manifest(manifest)
        if errors:
            logger.warning(f"Invalid skill manifest {skill_dir.name}: {errors}")
            continue

        results.append({
            "name": manifest["name"],
            "slug": skill_dir.name,
            "description": manifest.get("description", ""),
            "version": manifest.get("version", "0.1.0"),
            "path": f"skills/{skill_dir.name}",
            "manifest": manifest,
        })

    return results


# -- DB sync --


def sync_global_tools_to_db(agent=None):
    """Discover global tools and upsert Tool rows.

    When ``agent`` is provided, also creates AgentTool rows for any newly
    discovered tool so the agent has access to it, and disables AgentTool
    rows whose underlying tool directory was removed.

    Returns list of Tool instances.
    """
    discovered = discover_global_tools()
    discovered_slugs = {t["slug"] for t in discovered}

    existing = {t.slug: t for t in Tool.query.all()}

    results = []
    for td in discovered:
        tool = existing.get(td["slug"])
        if tool:
            tool.name = td["name"]
            tool.description = td["description"]
            tool.version = td["version"]
            tool.manifest_json = td["manifest"]
            tool.path = td["path"]
            tool.timeout = td["timeout"]
        else:
            tool = Tool(
                name=td["name"],
                slug=td["slug"],
                description=td["description"],
                version=td["version"],
                source="workspace",
                manifest_json=td["manifest"],
                path=td["path"],
                timeout=td["timeout"],
            )
            db.session.add(tool)
        db.session.flush()
        results.append(tool)

        if agent is not None:
            _ensure_agent_tool(agent, tool)
            # Register packages from requirements.txt for this agent's venv
            tool_dir = get_global_tools_path() / td["slug"]
            _register_requirements(agent, tool_dir)

    # Disable AgentTool rows whose tool directory was removed (soft).
    if agent is not None:
        for slug, removed in existing.items():
            if slug not in discovered_slugs:
                at = AgentTool.query.filter_by(
                    agent_id=agent.id, tool_id=removed.id
                ).first()
                if at:
                    at.enabled = False

    db.session.commit()
    return results


# Keep old name as alias for callers that still use it
sync_tools_to_db = sync_global_tools_to_db


def _ensure_agent_tool(agent, tool):
    """Create an AgentTool row if one doesn't already exist for (agent, tool)."""
    from datetime import datetime, timezone
    existing = AgentTool.query.filter_by(
        agent_id=agent.id, tool_id=tool.id
    ).first()
    if existing is None:
        db.session.add(AgentTool(
            agent_id=agent.id,
            tool_id=tool.id,
            enabled=True,
            created_at=datetime.now(timezone.utc),
        ))


def sync_global_skills_to_db(agent=None):
    """Discover global skills and upsert Skill rows.

    When ``agent`` is provided, also creates AgentSkill rows for any newly
    discovered skills so the agent has access to them.

    Returns list of Skill instances.
    """
    discovered = discover_global_skills()
    discovered_slugs = {s["slug"] for s in discovered}

    existing = {s.slug: s for s in Skill.query.all()}

    results = []
    for sd in discovered:
        skill = existing.get(sd["slug"])
        if skill:
            skill.name = sd["name"]
            skill.description = sd["description"]
            skill.version = sd["version"]
            skill.manifest_json = sd["manifest"]
            skill.path = sd["path"]
        else:
            skill = Skill(
                name=sd["name"],
                slug=sd["slug"],
                description=sd["description"],
                version=sd["version"],
                source="workspace",
                manifest_json=sd["manifest"],
                path=sd["path"],
            )
            db.session.add(skill)
        db.session.flush()
        results.append(skill)

        if agent is not None:
            _ensure_agent_skill(agent, skill)

        # Register packages from requirements.txt
        skill_dir = get_global_skills_path() / sd["slug"]
        if agent is not None:
            _register_requirements(agent, skill_dir)

    # Disable skills whose directory was removed (soft: remove AgentSkill rows that
    # point to non-existing slugs — the Skill row itself stays for audit).
    if agent is not None:
        for slug in list(existing.keys()):
            if slug not in discovered_slugs:
                removed = existing[slug]
                ags = AgentSkill.query.filter_by(
                    agent_id=agent.id, skill_id=removed.id
                ).first()
                if ags:
                    ags.enabled = False

    db.session.commit()
    return results


# Keep old name as alias for callers that still use it
sync_skills_to_db = sync_global_skills_to_db


def _ensure_agent_skill(agent, skill):
    """Create an AgentSkill row if one doesn't already exist for (agent, skill)."""
    from datetime import datetime, timezone
    existing = AgentSkill.query.filter_by(
        agent_id=agent.id, skill_id=skill.id
    ).first()
    if existing is None:
        db.session.add(AgentSkill(
            agent_id=agent.id,
            skill_id=skill.id,
            enabled=True,
            created_at=datetime.now(timezone.utc),
        ))


def _register_requirements(agent, item_dir: Path) -> None:
    """Register packages from requirements.txt that are not yet installed."""
    req_file = item_dir / "requirements.txt"
    if not req_file.exists():
        return

    from app.models.package_installation import PackageInstallation
    from app.services.package_service import request_install

    for line in req_file.read_text(encoding="utf-8").splitlines():
        spec = line.strip()
        if not spec or spec.startswith("#"):
            continue
        try:
            from app.services.package_service import parse_spec
            name, _ = parse_spec(spec)
            existing = PackageInstallation.query.filter_by(
                agent_id=agent.id, name=name
            ).first()
            if existing and existing.status == "installed":
                continue
            request_install(agent, spec)
        except Exception as e:
            logger.warning("Could not register requirement '%s' for agent %s: %s",
                           spec, agent.slug, e)


# -- Dynamic tool loading --


def resolve_agent_tool(agent, tool_name):
    """Find a global Tool the *agent* has enabled, by slug or manifest name.

    Returns the Tool row or None.
    """
    tool = (
        Tool.query
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .filter(
            AgentTool.agent_id == agent.id,
            AgentTool.enabled.is_(True),
            Tool.slug == tool_name,
        )
        .first()
    )
    if tool is None:
        tool = (
            Tool.query
            .join(AgentTool, AgentTool.tool_id == Tool.id)
            .filter(
                AgentTool.agent_id == agent.id,
                AgentTool.enabled.is_(True),
                Tool.name == tool_name,
            )
            .first()
        )
    return tool


def tool_dir_for(tool):
    """Absolute directory of a global tool: _global/tools/<slug>/."""
    return get_global_tools_path().parent / tool.path


def load_tool_handler(agent, tool_name):
    """Dynamically import a global tool's handler function.

    Returns the callable or None if not found/invalid.
    """
    tool = resolve_agent_tool(agent, tool_name)
    if tool is None:
        return None

    global_root = get_global_tools_path().parent.resolve()
    tool_py = tool_dir_for(tool) / "tool.py"

    # Path traversal protection — must stay within the global tools tree
    try:
        resolved = tool_py.resolve()
        if not resolved.is_relative_to(global_root):
            logger.error(f"Path traversal attempt: {tool_py}")
            return None
    except (ValueError, OSError):
        return None

    if not tool_py.exists():
        logger.warning(f"tool.py not found for {tool_name}: {tool_py}")
        return None

    try:
        # Use unique module name to avoid caching issues on reload
        module_name = f"workspace_tool_{agent.id}_{tool.slug}"
        spec = importlib.util.spec_from_file_location(module_name, str(resolved))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        handler = getattr(module, "handler", None)
        if handler is None:
            logger.warning(f"No 'handler' function in {tool_py}")
            return None
        return handler
    except Exception as e:
        logger.error(f"Failed to load tool handler {tool_name}: {e}")
        return None


# -- Agent tool resolution (used by agent_runner) --


def _validate_parameters_schema(schema, path=""):
    """Recursively check that a JSON Schema is valid for the Codex API.

    Returns (True, None) or (False, error_description).
    Arrays must declare 'items'; objects with 'properties' are recursed into.
    """
    if not isinstance(schema, dict):
        return True, None
    if schema.get("type") == "array" and "items" not in schema:
        loc = f" at '{path}'" if path else ""
        return False, f"array schema missing items{loc}"
    for prop, sub in schema.get("properties", {}).items():
        ok, msg = _validate_parameters_schema(sub, f"{path}.{prop}" if path else prop)
        if not ok:
            return False, msg
    if "items" in schema:
        ok, msg = _validate_parameters_schema(schema["items"], f"{path}[]")
        if not ok:
            return False, msg
    return True, None


def get_agent_tool_definitions(agent):
    """Get all tool definitions for an agent: builtins + enabled workspace tools.

    Returns list of dicts in OpenAI function-calling format.
    Tools with invalid JSON Schema are skipped and logged so they never cause
    a Codex API 400.
    """
    # Start with builtins
    definitions = list(get_builtin_definitions())
    builtin_names = {d["function"]["name"] for d in definitions}

    # Add global tools the agent has enabled (via the agent_tools junction)
    tools = get_enabled_tools(agent)
    for tool in tools:
        manifest = tool.manifest_json or {}
        name = manifest.get("name", tool.name)
        parameters = manifest.get("parameters", {"type": "object", "properties": {}})

        ok, schema_error = _validate_parameters_schema(parameters)
        if not ok:
            logger.warning(
                "Tool '%s' (agent %s) has invalid schema — skipping to avoid API 400: %s",
                name, agent.slug, schema_error,
            )
            continue

        # Workspace tools override builtins with the same name
        if name in builtin_names:
            definitions = [d for d in definitions if d["function"]["name"] != name]

        definitions.append({
            "type": "function",
            "function": {
                "name": name,
                "description": manifest.get("description", tool.description or ""),
                "parameters": parameters,
            },
        })

    return definitions


def get_enabled_skills(agent):
    """Get enabled skills for an agent via the agent_skills junction table."""
    return (
        Skill.query
        .join(AgentSkill, AgentSkill.skill_id == Skill.id)
        .filter(AgentSkill.agent_id == agent.id, AgentSkill.enabled.is_(True))
        .all()
    )


def get_enabled_tools(agent):
    """Get enabled tools for an agent via the agent_tools junction table."""
    return (
        Tool.query
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .filter(AgentTool.agent_id == agent.id, AgentTool.enabled.is_(True))
        .order_by(Tool.name)
        .all()
    )
