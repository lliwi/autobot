import json
import logging
import re
import shutil
from pathlib import Path

from app.extensions import db
from app.models.agent import Agent
from app.models.tool import Tool
from app.workspace.discovery import load_tool_handler, sync_tools_to_db
from app.workspace.manager import get_workspace_path

logger = logging.getLogger(__name__)


def _slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def list_tools(agent_id=None, include_disabled=False):
    query = Tool.query
    if agent_id:
        query = query.filter_by(agent_id=agent_id)
    if not include_disabled:
        query = query.filter_by(enabled=True)
    return query.order_by(Tool.name).all()


def get_tool(tool_id):
    return db.session.get(Tool, tool_id)


def create_tool(agent_id, data):
    """Create a tool: scaffold filesystem structure and DB row."""
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    name = data["name"]
    slug = _slugify(name)
    workspace = get_workspace_path(agent)
    tool_dir = workspace / "tools" / slug
    tool_dir.mkdir(parents=True, exist_ok=True)

    parameters = data.get("parameters", {"type": "object", "properties": {}})

    # Write manifest
    manifest = {
        "name": name,
        "description": data.get("description", ""),
        "version": data.get("version", "0.1.0"),
        "parameters": parameters,
        "timeout": data.get("timeout", 30),
    }
    (tool_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Write tool.py template
    tool_py = data.get("tool_py")
    if not tool_py:
        tool_py = (
            f'"""Tool: {name}"""\n\n\n'
            f"def handler(_agent=None, **kwargs):\n"
            f'    """Execute the tool. Receives agent context and tool parameters."""\n'
            f'    return {{"result": "Not implemented"}}\n'
        )
    (tool_dir / "tool.py").write_text(tool_py, encoding="utf-8")

    tool = Tool(
        agent_id=agent_id,
        name=name,
        slug=slug,
        version=manifest["version"],
        description=manifest["description"],
        source="manual",
        enabled=True,
        manifest_json=manifest,
        path=f"tools/{slug}",
        timeout=manifest["timeout"],
    )
    db.session.add(tool)
    db.session.commit()
    return tool


def toggle_tool(tool_id):
    tool = db.session.get(Tool, tool_id)
    if tool is None:
        return None
    tool.enabled = not tool.enabled
    db.session.commit()
    return tool


def test_tool(tool_id, test_input=None):
    """Load and execute a tool with test input. Returns result dict."""
    tool = db.session.get(Tool, tool_id)
    if tool is None:
        return {"error": "Tool not found"}

    agent = db.session.get(Agent, tool.agent_id)
    handler = load_tool_handler(agent, tool.slug)
    if handler is None:
        return {"error": "Could not load tool handler"}

    try:
        result = handler(_agent=agent, **(test_input or {}))
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


def reload_tool(tool_id):
    """Re-read manifest.json from the workspace and update the Tool row.

    Mirrors reload_skill() in skill_service. Returns the (updated) Tool or
    None if the tool_id doesn't exist.
    """
    tool = db.session.get(Tool, tool_id)
    if tool is None:
        return None

    agent = db.session.get(Agent, tool.agent_id)
    if agent is None:
        return tool

    manifest_path = get_workspace_path(agent) / tool.path / "manifest.json"
    if not manifest_path.exists():
        return tool

    from app.workspace.manifest import load_manifest, validate_tool_manifest

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
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return []
    return sync_tools_to_db(agent)


def copy_tool(tool_id, target_agent_id):
    """Copy a tool's filesystem directory from its source agent to a target agent
    and create the corresponding Tool row. Returns the new Tool or raises ValueError.
    """
    source = db.session.get(Tool, tool_id)
    if source is None:
        raise ValueError("Tool not found")

    source_agent = db.session.get(Agent, source.agent_id)
    target_agent = db.session.get(Agent, target_agent_id)
    if target_agent is None:
        raise ValueError("Target agent not found")
    if source_agent is None:
        raise ValueError("Source agent not found")
    if source_agent.id == target_agent.id:
        raise ValueError("Source and target agents are the same")

    existing = Tool.query.filter_by(agent_id=target_agent.id, slug=source.slug).first()
    if existing is not None:
        raise ValueError(f"Agent '{target_agent.name}' already has a tool with slug '{source.slug}'")

    source_dir = get_workspace_path(source_agent) / source.path
    target_dir = get_workspace_path(target_agent) / source.path
    if not source_dir.exists():
        raise ValueError(f"Source tool directory missing: {source_dir}")

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target_dir)

    copy = Tool(
        agent_id=target_agent.id,
        name=source.name,
        slug=source.slug,
        version=source.version,
        description=source.description,
        source=source.source,
        enabled=True,
        manifest_json=source.manifest_json,
        path=source.path,
        timeout=source.timeout,
    )
    db.session.add(copy)
    db.session.commit()
    return copy
