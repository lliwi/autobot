"""Discovery engine for workspace skills and tools.

Scans the filesystem, validates manifests, syncs with DB, and provides
dynamic tool loading for the agent runtime.
"""

import importlib.util
import logging
from pathlib import Path

from app.extensions import db
from app.models.skill import Skill
from app.models.tool import Tool
from app.runtime.tool_registry import get_all_definitions as get_builtin_definitions
from app.workspace.manager import get_workspace_path
from app.workspace.manifest import load_manifest, validate_skill_manifest, validate_tool_manifest

logger = logging.getLogger(__name__)


# -- Filesystem discovery --


def discover_workspace_tools(agent):
    """Scan tools/ directory and return list of valid tool dicts."""
    workspace = get_workspace_path(agent)
    tools_dir = workspace / "tools"
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


def discover_workspace_skills(agent):
    """Scan skills/ directory and return list of valid skill dicts."""
    workspace = get_workspace_path(agent)
    skills_dir = workspace / "skills"
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


def sync_tools_to_db(agent):
    """Discover workspace tools and upsert Tool rows. Returns list of Tool instances."""
    discovered = discover_workspace_tools(agent)
    discovered_slugs = {t["slug"] for t in discovered}

    existing = Tool.query.filter_by(agent_id=agent.id).all()
    existing_map = {t.slug: t for t in existing}

    results = []
    for td in discovered:
        tool = existing_map.get(td["slug"])
        is_new = tool is None
        if tool:
            tool.name = td["name"]
            tool.description = td["description"]
            tool.version = td["version"]
            tool.manifest_json = td["manifest"]
            tool.path = td["path"]
            tool.timeout = td["timeout"]
        else:
            tool = Tool(
                agent_id=agent.id,
                name=td["name"],
                slug=td["slug"],
                description=td["description"],
                version=td["version"],
                source="workspace",
                enabled=True,
                manifest_json=td["manifest"],
                path=td["path"],
                timeout=td["timeout"],
            )
            db.session.add(tool)
        results.append(tool)

        # Register packages from requirements.txt for new tools (or if file appeared)
        tool_dir = get_workspace_path(agent) / td["path"]
        _register_requirements(agent, tool_dir)

    # Disable tools whose directory was removed
    for slug, tool in existing_map.items():
        if slug not in discovered_slugs:
            tool.enabled = False

    db.session.commit()
    return results


def sync_skills_to_db(agent):
    """Discover workspace skills and upsert Skill rows. Returns list of Skill instances."""
    discovered = discover_workspace_skills(agent)
    discovered_slugs = {s["slug"] for s in discovered}

    existing = Skill.query.filter_by(agent_id=agent.id).all()
    existing_map = {s.slug: s for s in existing}

    results = []
    for sd in discovered:
        skill = existing_map.get(sd["slug"])
        if skill:
            skill.name = sd["name"]
            skill.description = sd["description"]
            skill.version = sd["version"]
            skill.manifest_json = sd["manifest"]
            skill.path = sd["path"]
        else:
            skill = Skill(
                agent_id=agent.id,
                name=sd["name"],
                slug=sd["slug"],
                description=sd["description"],
                version=sd["version"],
                source="workspace",
                enabled=True,
                manifest_json=sd["manifest"],
                path=sd["path"],
            )
            db.session.add(skill)
        results.append(skill)

        # Register packages from requirements.txt
        skill_dir = get_workspace_path(agent) / sd["path"]
        _register_requirements(agent, skill_dir)

    # Disable skills whose directory was removed
    for slug, skill in existing_map.items():
        if slug not in discovered_slugs:
            skill.enabled = False

    db.session.commit()
    return results


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
            # Only request if not already installed
            from app.services.package_service import _normalise_name, parse_spec
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


def load_tool_handler(agent, tool_name):
    """Dynamically import a workspace tool's handler function.

    Returns the callable or None if not found/invalid.
    """
    tool = Tool.query.filter_by(agent_id=agent.id, slug=tool_name, enabled=True).first()
    if tool is None:
        # Also try by name (tool_name might be the manifest name, not slug)
        tool = Tool.query.filter_by(agent_id=agent.id, name=tool_name, enabled=True).first()
    if tool is None:
        return None

    workspace = get_workspace_path(agent)
    tool_py = workspace / tool.path / "tool.py"

    # Path traversal protection
    try:
        resolved = tool_py.resolve()
        if not resolved.is_relative_to(workspace.resolve()):
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


def get_agent_tool_definitions(agent):
    """Get all tool definitions for an agent: builtins + enabled workspace tools.

    Returns list of dicts in OpenAI function-calling format.
    """
    # Start with builtins
    definitions = list(get_builtin_definitions())
    builtin_names = {d["function"]["name"] for d in definitions}

    # Add enabled workspace tools
    tools = Tool.query.filter_by(agent_id=agent.id, enabled=True).all()
    for tool in tools:
        manifest = tool.manifest_json or {}
        name = manifest.get("name", tool.name)

        # Workspace tools override builtins with the same name
        if name in builtin_names:
            definitions = [d for d in definitions if d["function"]["name"] != name]

        definitions.append({
            "type": "function",
            "function": {
                "name": name,
                "description": manifest.get("description", tool.description or ""),
                "parameters": manifest.get("parameters", {"type": "object", "properties": {}}),
            },
        })

    return definitions


def get_enabled_skills(agent):
    """Get enabled skills for an agent."""
    return Skill.query.filter_by(agent_id=agent.id, enabled=True).all()
