"""Self-modification tools: propose changes, author skills/tools, and manage the
global tool catalog (create/rename/delete/list/grant/revoke)."""
import json
import re

from app.runtime.tool_registry.core import ToolDefinition, register

_SKILL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,50}$")


def register_selfmod_tools():
    register(
        ToolDefinition(
            name="propose_change",
            description=(
                "Propose a change to a workspace file. For level-1 targets (MEMORY.md, "
                "new skills/tools) the change is auto-applied. For level-2 targets "
                "(existing code, AGENTS.md, TOOLS.md) it requires admin approval. "
                "Level-3 targets (core, OAuth, DB) are prohibited."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "Relative path within the workspace (e.g. 'MEMORY.md', 'skills/my-skill/skill.py').",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "The complete new content for the file.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title describing the change.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this change is needed.",
                    },
                },
                "required": ["target_path", "new_content", "title", "reason"],
            },
            handler=lambda **kwargs: _propose_change(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="create_skill",
            description=(
                "Create a new skill in the agent workspace in one call. Writes "
                "`skills/<slug>/SKILL.md` and (optionally) `skills/<slug>/skill.py`. "
                "Prefer this over chaining multiple propose_change calls."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Short kebab-case identifier, e.g. 'weather-barcelona'.",
                    },
                    "title": {"type": "string", "description": "Human-readable title."},
                    "summary": {
                        "type": "string",
                        "description": "One or two sentences explaining what the skill does and when to use it.",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Markdown body with steps, inputs, outputs, examples.",
                    },
                    "code": {
                        "type": "string",
                        "description": "Optional Python implementation. If provided, saved as skill.py.",
                    },
                },
                "required": ["slug", "title", "summary", "instructions"],
            },
            handler=lambda **kwargs: _create_skill(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="create_tool",
            description=(
                "Create a new tool in the GLOBAL catalog (manifest.json + tool.py) under "
                "`_global/tools/<slug>/` and enable it for yourself. Tools are shared by all "
                "agents, so make them GENERIC and REUSABLE: take parameters (host, action, "
                "credential name, ...) instead of hardcoding; read secrets via "
                "`os.environ['AUTOBOT_CRED_<NAME>']`. Before creating, check if an existing "
                "tool already does this — if so, update it and bump its manifest 'version' "
                "rather than creating a near-duplicate. NEVER encode a version in the slug "
                "(no `foo2`, no `foo-v2`)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Short kebab-case identifier."},
                    "description": {"type": "string", "description": "Human-readable description of what the tool does."},
                    "parameters_schema": {
                        "type": "object",
                        "description": "JSON Schema object describing the tool parameters (type, properties, required).",
                    },
                    "code": {
                        "type": "string",
                        "description": "Python source implementing `def handler(_agent=None, **kwargs): ...` returning a dict.",
                    },
                },
                "required": ["slug", "description", "parameters_schema", "code"],
            },
            handler=lambda **kwargs: _create_tool(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="rename_tool",
            description=(
                "Rename an existing workspace tool: moves `tools/<old_slug>/` to "
                "`tools/<new_slug>/`, updates manifest.name and the DB row, and "
                "refreshes TOOLS.md. Use to fix versioned names (e.g. runner2 → runner)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "old_slug": {"type": "string", "description": "Current tool directory name (kebab-case)."},
                    "new_slug": {"type": "string", "description": "New tool directory name (kebab-case, no -vN suffixes)."},
                },
                "required": ["old_slug", "new_slug"],
            },
            handler=lambda **kwargs: _rename_tool(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="delete_tool",
            description=(
                "Delete an existing workspace tool: removes `tools/<slug>/` from disk "
                "and its DB row, then refreshes TOOLS.md. "
                "Irreversible — use only for superseded or duplicate tools."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Tool directory name to delete."},
                    "reason": {"type": "string", "description": "Why this tool is being deleted (for audit log)."},
                },
                "required": ["slug", "reason"],
            },
            handler=lambda **kwargs: _delete_tool(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_global_tools",
            description=(
                "List the global tool catalog: every tool's slug, description and which "
                "agents currently have it enabled. Use this to discover tools you could "
                "grant to another agent with `grant_tool`."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_global_tools(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="grant_tool",
            description=(
                "Grant another agent access to a global tool by enabling it for them. "
                "Tools are shared, but each agent only sees the tools assigned to it — "
                "use this when a sub-agent needs a capability it doesn't yet have. "
                "Idempotent: re-granting just re-enables it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "description": "Tool slug or name to grant (see list_global_tools)."},
                    "agent": {"type": "string", "description": "Target agent name or slug to grant the tool to."},
                },
                "required": ["tool", "agent"],
            },
            handler=lambda **kwargs: _grant_tool(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="revoke_tool",
            description=(
                "Remove an agent's access to a global tool (disables the assignment). "
                "Does not delete the tool from the catalog — other agents keep it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "description": "Tool slug or name to revoke."},
                    "agent": {"type": "string", "description": "Target agent name or slug to revoke from."},
                },
                "required": ["tool", "agent"],
            },
            handler=lambda **kwargs: _revoke_tool(**kwargs),
        )
    )


def _propose_change(_agent=None, _run_id=None, target_path=None, new_content=None, title=None, reason=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    missing = [k for k, v in (
        ("target_path", target_path),
        ("new_content", new_content),
        ("title", title),
        ("reason", reason),
    ) if not v]
    if missing:
        return {"error": f"Missing required argument(s): {', '.join(missing)}"}
    from app.services.patch_service import propose_change

    try:
        patch = propose_change(
            agent_id=_agent.id,
            target_path=target_path,
            new_content=new_content,
            title=title,
            reason=reason,
            run_id=_run_id,
        )
        return {
            "patch_id": patch.id,
            "status": patch.status,
            "security_level": patch.security_level,
            "target_path": patch.target_path,
            "message": (
                "Change auto-applied." if patch.status == "applied"
                else "Change queued for admin review." if patch.status == "pending_review"
                else f"Change rejected: {(patch.test_result_json or {}).get('error', 'unknown')}"
            ),
        }
    except ValueError as e:
        return {"error": str(e)}


def _create_skill(_agent=None, _run_id=None, slug=None, title=None, summary=None,
                  instructions=None, code=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    missing = [k for k, v in (
        ("slug", slug), ("title", title), ("summary", summary), ("instructions", instructions),
    ) if not v]
    if missing:
        return {"error": f"Missing required argument(s): {', '.join(missing)}"}
    if not _SKILL_SLUG_RE.match(slug):
        return {"error": "slug must be lowercase kebab-case (letters, digits, '-')."}

    from app.workspace.manager import get_global_skills_path

    skill_dir = get_global_skills_path() / slug
    skill_md = f"# {title}\n\n{summary}\n\n{instructions}\n"
    manifest = {"name": slug, "description": summary, "version": "0.1.0"}
    outputs = []

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        outputs.append({"file": f"_global/skills/{slug}/manifest.json", "status": "applied"})
        (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        outputs.append({"file": f"_global/skills/{slug}/SKILL.md", "status": "applied"})
        if code:
            (skill_dir / "skill.py").write_text(code, encoding="utf-8")
            outputs.append({"file": f"_global/skills/{slug}/skill.py", "status": "applied"})
    except OSError as e:
        return {"error": str(e), "created": outputs}

    from app.workspace.discovery import sync_global_skills_to_db
    sync_global_skills_to_db(_agent)

    from app.services.review_service import review_creation
    review_payload = f"# {title}\n\n{summary}\n\n{instructions}"
    if code:
        review_payload += f"\n\n---\n# skill.py\n```python\n{code}\n```"
    review = review_creation(_agent, "skill", slug, review_payload, run_id=_run_id)

    result = {"slug": slug, "created": outputs, "message": "Skill written to global catalog and indexed."}
    if review is not None:
        result["review"] = review
    return result


def _create_tool(_agent=None, _run_id=None, slug=None, description=None,
                 parameters_schema=None, code=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    missing = [k for k, v in (
        ("slug", slug), ("description", description),
        ("parameters_schema", parameters_schema), ("code", code),
    ) if not v]
    if missing:
        return {"error": f"Missing required argument(s): {', '.join(missing)}"}
    if not isinstance(parameters_schema, dict):
        return {"error": "parameters_schema must be a JSON object."}
    if "def handler" not in code:
        return {"error": "code must define a `handler` function."}

    from app.models.tool import Tool
    from app.services.tool_service import validate_tool_slug

    slug_error = validate_tool_slug(slug)
    if slug_error:
        return {"error": slug_error}

    # Versioning policy: never create a sibling tool — bump the existing one.
    if Tool.query.filter_by(slug=slug).first():
        return {
            "error": (
                f"A tool '{slug}' already exists in the global catalog. Do NOT create "
                f"'{slug}2' or '{slug}-v2'. Update tools/{slug}/ and bump 'version' in "
                "its manifest.json instead."
            )
        }

    from app.workspace.manager import get_global_tools_path

    tool_dir = get_global_tools_path() / slug
    manifest = {
        "name": slug,
        "description": description,
        "version": "0.1.0",
        "parameters": parameters_schema,
    }

    outputs = []
    try:
        tool_dir.mkdir(parents=True, exist_ok=True)
        (tool_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        outputs.append({"file": f"_global/tools/{slug}/manifest.json", "status": "applied"})
        (tool_dir / "tool.py").write_text(
            code if code.endswith("\n") else code + "\n", encoding="utf-8"
        )
        outputs.append({"file": f"_global/tools/{slug}/tool.py", "status": "applied"})
    except OSError as e:
        return {"error": str(e), "created": outputs}

    from app.workspace.discovery import sync_global_tools_to_db
    sync_global_tools_to_db(_agent)

    from app.workspace.manager import refresh_tools_md
    refresh_tools_md(_agent)

    from app.services.review_service import review_creation
    import json as _json
    review_payload = (
        f"# Tool '{slug}'\n\n{description}\n\n"
        f"Parameters schema:\n```json\n{_json.dumps(parameters_schema, indent=2)}\n```\n\n"
        f"Handler:\n```python\n{code}\n```"
    )
    review = review_creation(_agent, "tool", slug, review_payload, run_id=_run_id)

    result = {"slug": slug, "created": outputs, "message": "Tool scaffold written, indexed, and available for calling."}
    if review is not None:
        result["review"] = review
    return result


def _rename_tool(_agent=None, _run_id=None, old_slug=None, new_slug=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not old_slug or not new_slug:
        return {"error": "Both old_slug and new_slug are required"}
    if old_slug == new_slug:
        return {"error": "old_slug and new_slug are identical"}

    from app.extensions import db
    from app.models.tool import Tool
    from app.services.tool_service import validate_tool_slug
    from app.workspace.manager import get_global_tools_path

    slug_error = validate_tool_slug(new_slug)
    if slug_error:
        return {"error": f"new_slug invalid: {slug_error}"}

    old_tool = Tool.query.filter_by(slug=old_slug).first()
    if old_tool is None:
        return {"error": f"Tool '{old_slug}' not found in the global catalog"}

    if Tool.query.filter_by(slug=new_slug).first():
        return {"error": f"Tool '{new_slug}' already exists — choose a different name"}

    tools_root = get_global_tools_path()
    old_dir = tools_root / old_slug
    new_dir = tools_root / new_slug

    if not old_dir.exists():
        return {"error": f"Directory 'tools/{old_slug}' not found on disk"}
    if new_dir.exists():
        return {"error": f"Directory 'tools/{new_slug}' already exists on disk"}

    import shutil as _shutil
    try:
        _shutil.copytree(old_dir, new_dir)
        # Update manifest.name in the copy
        manifest_path = new_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["name"] = new_slug
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        _shutil.rmtree(old_dir)
    except Exception as exc:
        _shutil.rmtree(new_dir, ignore_errors=True)
        return {"error": f"Filesystem error: {exc}"}

    # Update DB row in-place (preserves ID and FK references)
    old_tool.slug = new_slug
    old_tool.name = new_slug
    old_tool.path = f"tools/{new_slug}"
    if old_tool.manifest_json:
        old_tool.manifest_json = {**old_tool.manifest_json, "name": new_slug}
    db.session.commit()

    from app.workspace.manager import refresh_tools_md
    refresh_tools_md(_agent)

    return {
        "renamed": True,
        "old_slug": old_slug,
        "new_slug": new_slug,
        "message": f"Tool renamed from '{old_slug}' to '{new_slug}' and TOOLS.md updated.",
    }


def _delete_tool(_agent=None, _run_id=None, slug=None, reason=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not slug:
        return {"error": "slug is required"}
    if not reason:
        return {"error": "reason is required — explain why this tool is being deleted"}

    from app.extensions import db
    from app.models.tool import Tool
    from app.workspace.manager import get_global_tools_path

    tool = Tool.query.filter_by(slug=slug).first()
    if tool is None:
        return {"error": f"Tool '{slug}' not found in the global catalog"}

    tool_dir = get_global_tools_path() / slug

    import shutil as _shutil
    if tool_dir.exists():
        _shutil.rmtree(tool_dir)

    db.session.delete(tool)
    db.session.commit()

    from app.workspace.manager import refresh_tools_md
    refresh_tools_md(_agent)

    return {
        "deleted": True,
        "slug": slug,
        "reason": reason,
        "message": f"Tool '{slug}' removed from disk, DB, and TOOLS.md.",
    }


def _resolve_tool_row(ident):
    """Resolve a global Tool by slug or name."""
    from app.models.tool import Tool
    return (Tool.query.filter_by(slug=ident).first()
            or Tool.query.filter_by(name=ident).first())


def _resolve_agent_row(ident):
    """Resolve an Agent by slug or name."""
    from app.models.agent import Agent
    return (Agent.query.filter_by(slug=ident).first()
            or Agent.query.filter_by(name=ident).first())


def _list_global_tools(_agent=None, **kwargs):
    from app.models.tool import AgentTool, Tool
    from app.models.agent import Agent

    agents = {a.id: (a.name or a.slug) for a in Agent.query.all()}
    assigns = {}
    for at in AgentTool.query.filter_by(enabled=True).all():
        assigns.setdefault(at.tool_id, []).append(agents.get(at.agent_id, str(at.agent_id)))

    tools = []
    for t in Tool.query.order_by(Tool.name).all():
        tools.append({
            "slug": t.slug,
            "version": t.version,
            "description": (t.description or "")[:200],
            "enabled_for": sorted(assigns.get(t.id, [])),
        })
    return {"count": len(tools), "tools": tools}


def _grant_tool(_agent=None, _run_id=None, tool=None, agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not tool or not agent:
        return {"error": "Both 'tool' and 'agent' are required"}

    from app.extensions import db
    from app.models.tool import AgentTool
    from app.workspace.manager import refresh_tools_md

    t = _resolve_tool_row(tool)
    if t is None:
        return {"error": f"Tool '{tool}' not found in the global catalog. Use list_global_tools to see available slugs."}
    target = _resolve_agent_row(agent)
    if target is None:
        return {"error": f"Agent '{agent}' not found"}

    existing = AgentTool.query.filter_by(tool_id=t.id, agent_id=target.id).first()
    if existing is not None:
        if not existing.enabled:
            existing.enabled = True
            db.session.commit()
            refresh_tools_md(target)
            return {"granted": True, "tool": t.slug, "agent": target.slug, "message": "re-enabled existing assignment"}
        return {"granted": True, "tool": t.slug, "agent": target.slug, "message": "already enabled (no change)"}

    db.session.add(AgentTool(tool_id=t.id, agent_id=target.id, enabled=True))
    db.session.commit()
    refresh_tools_md(target)
    return {"granted": True, "tool": t.slug, "agent": target.slug,
            "message": f"Tool '{t.slug}' is now available to agent '{target.slug}'."}


def _revoke_tool(_agent=None, _run_id=None, tool=None, agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not tool or not agent:
        return {"error": "Both 'tool' and 'agent' are required"}

    from app.extensions import db
    from app.models.tool import AgentTool
    from app.workspace.manager import refresh_tools_md

    t = _resolve_tool_row(tool)
    if t is None:
        return {"error": f"Tool '{tool}' not found"}
    target = _resolve_agent_row(agent)
    if target is None:
        return {"error": f"Agent '{agent}' not found"}

    at = AgentTool.query.filter_by(tool_id=t.id, agent_id=target.id).first()
    if at is None:
        return {"revoked": False, "message": f"Agent '{target.slug}' did not have '{t.slug}' assigned"}
    db.session.delete(at)
    db.session.commit()
    refresh_tools_md(target)
    return {"revoked": True, "tool": t.slug, "agent": target.slug,
            "message": f"Tool '{t.slug}' removed from agent '{target.slug}'."}
