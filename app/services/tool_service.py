import json
import logging
import re
from datetime import datetime, timezone

from app.extensions import db
from app.models.agent import Agent
from app.models.tool import AgentTool, Tool
from app.workspace.discovery import load_tool_handler, sync_global_tools_to_db
from app.workspace.manager import get_global_tools_path

logger = logging.getLogger(__name__)

# A valid tool slug is kebab-case and must NOT encode a version (e.g. no
# trailing digit like ``foo2`` or ``foo-v2``). Versions live in manifest.json.
_VERSION_SUFFIX_RE = re.compile(r"(-?v?\d+)$")


def _slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def validate_tool_slug(slug):
    """Return an error string if the slug is malformed or version-encoding, else None."""
    if not slug or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        return "slug must be lowercase kebab-case (letters, digits, hyphens)"
    # Reject names that bake a version into the directory: foo2, foo-2, foo-v2
    if _VERSION_SUFFIX_RE.search(slug):
        return (
            f"slug '{slug}' looks like a versioned name — versions belong in "
            "manifest.json ('version'), not the slug. Bump the version of the "
            "existing tool instead of creating a sibling."
        )
    return None


def _version_gt(a: str, b: str) -> bool:
    """Return True if semver string a is strictly greater than b."""
    def _parse(v):
        try:
            return tuple(int(x) for x in str(v).split("."))
        except ValueError:
            return (0,)
    return _parse(a) > _parse(b)


def list_tools(agent_id=None):
    """Return tools. When agent_id is given, only tools assigned to that agent."""
    if agent_id:
        return (
            Tool.query
            .join(AgentTool, AgentTool.tool_id == Tool.id)
            .filter(AgentTool.agent_id == agent_id)
            .order_by(Tool.name)
            .all()
        )
    return Tool.query.order_by(Tool.name).all()


def get_tool(tool_id):
    return db.session.get(Tool, tool_id)


def get_agent_tool(tool_id, agent_id):
    """Return the AgentTool junction row for (tool, agent), or None."""
    return AgentTool.query.filter_by(tool_id=tool_id, agent_id=agent_id).first()


def create_tool(agent_id, data):
    """Create a global tool: scaffold _global/tools/<slug>/ and DB rows.

    The creating agent is auto-assigned access via an AgentTool row.
    """
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    name = data["name"]
    slug = data.get("slug") or _slugify(name)

    slug_error = validate_tool_slug(slug)
    if slug_error:
        raise ValueError(slug_error)

    if Tool.query.filter_by(slug=slug).first():
        raise ValueError(
            f"A tool with slug '{slug}' already exists in the global catalog. "
            "Update it and bump its version instead of creating a duplicate."
        )

    tool_dir = get_global_tools_path() / slug
    tool_dir.mkdir(parents=True, exist_ok=True)

    parameters = data.get("parameters", {"type": "object", "properties": {}})

    manifest = {
        "name": name,
        "description": data.get("description", ""),
        "version": data.get("version", "0.1.0"),
        "parameters": parameters,
        "timeout": data.get("timeout", 30),
    }
    (tool_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    tool_py = data.get("tool_py")
    if not tool_py:
        tool_py = (
            f'"""Tool: {name}\n\n'
            f"Keep tools generic and reusable: accept parameters (host, action,\n"
            f"credential name, ...) rather than hardcoding values. Read secrets via\n"
            f'os.environ["AUTOBOT_CRED_<NAME>"] so one tool serves every agent."""\n\n\n'
            f"def handler(_agent=None, **kwargs):\n"
            f'    """Execute the tool. Receives agent context and tool parameters."""\n'
            f'    return {{"result": "Not implemented"}}\n'
        )
    (tool_dir / "tool.py").write_text(tool_py, encoding="utf-8")

    tool = Tool(
        name=name,
        slug=slug,
        version=manifest["version"],
        description=manifest["description"],
        source="manual",
        manifest_json=manifest,
        path=f"tools/{slug}",
        timeout=manifest["timeout"],
    )
    db.session.add(tool)
    db.session.flush()

    db.session.add(AgentTool(
        agent_id=agent_id,
        tool_id=tool.id,
        enabled=True,
        created_at=datetime.now(timezone.utc),
    ))
    db.session.commit()
    return tool


def toggle_tool(tool_id, agent_id):
    """Toggle enabled on the AgentTool row for (tool, agent)."""
    at = AgentTool.query.filter_by(tool_id=tool_id, agent_id=agent_id).first()
    if at is None:
        return None
    at.enabled = not at.enabled
    db.session.commit()
    return at


def test_tool(tool_id, agent_id, test_input=None):
    """Load and execute a global tool with test input on behalf of an agent."""
    tool = db.session.get(Tool, tool_id)
    if tool is None:
        return {"error": "Tool not found"}

    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return {"error": "Agent not found"}

    handler = load_tool_handler(agent, tool.slug)
    if handler is None:
        return {"error": "Could not load tool handler (is it enabled for this agent?)"}

    try:
        result = handler(_agent=agent, **(test_input or {}))
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


def reload_tool(tool_id):
    """Re-read manifest from _global/tools/ and update the Tool row.

    If the _template/ copy has a strictly higher version, its files are synced
    into _global/ first so the template always wins on Reload. Mirrors
    reload_skill().
    """
    import shutil

    from app.workspace.manager import get_template_path

    tool = db.session.get(Tool, tool_id)
    if tool is None:
        return None

    global_dir = get_global_tools_path() / tool.slug
    template_dir = get_template_path() / "tools" / tool.slug

    from app.workspace.manifest import load_manifest, validate_tool_manifest

    if template_dir.is_dir():
        try:
            tmpl_manifest = load_manifest(template_dir / "manifest.json")
            global_manifest = (
                load_manifest(global_dir / "manifest.json")
                if (global_dir / "manifest.json").exists() else {}
            )
            if _version_gt(tmpl_manifest.get("version", "0"), global_manifest.get("version", "0")):
                global_dir.mkdir(parents=True, exist_ok=True)
                for src in template_dir.rglob("*"):
                    if src.is_file():
                        dst = global_dir / src.relative_to(template_dir)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                logger.info("reload_tool: synced %s from template (%s → %s)",
                            tool.slug,
                            global_manifest.get("version", "?"),
                            tmpl_manifest.get("version", "?"))
        except Exception:
            logger.warning("reload_tool: template sync failed for %s", tool.slug, exc_info=True)

    manifest_path = global_dir / "manifest.json"
    if not manifest_path.exists():
        return tool

    try:
        manifest = load_manifest(manifest_path)
        errors = validate_tool_manifest(manifest)
        if errors:
            logger.warning("reload_tool %s: invalid manifest: %s", tool_id, errors)
            return tool
    except ValueError as exc:
        logger.warning("reload_tool %s: could not load manifest: %s", tool_id, exc)
        return tool

    tool.name = manifest.get("name", tool.name)
    tool.description = manifest.get("description", tool.description)
    tool.version = manifest.get("version", tool.version)
    tool.manifest_json = manifest
    db.session.commit()
    return tool


def sync_agent_tools(agent_id):
    """Sync global tools to DB and ensure junction rows exist for the agent."""
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return []
    return sync_global_tools_to_db(agent)


def assign_tool_to_agent(tool_id, agent_id):
    """Create an AgentTool row giving the agent access to a global tool.

    Returns the AgentTool row. Raises ValueError if already assigned.
    """
    tool = db.session.get(Tool, tool_id)
    if tool is None:
        raise ValueError("Tool not found")
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    existing = AgentTool.query.filter_by(tool_id=tool_id, agent_id=agent_id).first()
    if existing is not None:
        raise ValueError(f"Agent '{agent.name}' already has tool '{tool.slug}' assigned")

    at = AgentTool(
        agent_id=agent_id,
        tool_id=tool_id,
        enabled=True,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(at)
    db.session.commit()
    return at


def remove_tool_from_agent(tool_id, agent_id):
    """Remove an AgentTool assignment. Does not delete the global Tool row."""
    at = AgentTool.query.filter_by(tool_id=tool_id, agent_id=agent_id).first()
    if at is None:
        raise ValueError("Assignment not found")
    db.session.delete(at)
    db.session.commit()
