import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    handler: Callable


_registry: dict[str, ToolDefinition] = {}


def register(tool_def: ToolDefinition):
    _registry[tool_def.name] = tool_def


def get(name: str) -> ToolDefinition | None:
    return _registry.get(name)


def get_all_definitions() -> list[dict]:
    """Return tools in OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters,
            },
        }
        for td in _registry.values()
    ]


def register_builtin_tools():
    """Register the built-in tools that ship with Phase 1."""
    from datetime import datetime, timezone

    register(
        ToolDefinition(
            name="read_workspace_file",
            description="Read a file from the agent's workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Path to the file relative to the workspace root."}
                },
                "required": ["filename"],
            },
            handler=lambda **kwargs: _read_workspace_file(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_workspace_files",
            description="List all files in the agent's workspace.",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_workspace_files(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="get_current_time",
            description="Get the current date and time in UTC.",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: {"time": datetime.now(timezone.utc).isoformat()},
        )
    )

    register(
        ToolDefinition(
            name="delegate_task",
            description="Delegate a task to a sub-agent. The sub-agent will execute the task and return the result.",
            parameters={
                "type": "object",
                "properties": {
                    "target_name": {
                        "type": "string",
                        "description": "Name or slug of the sub-agent to delegate to.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The task/message to send to the sub-agent.",
                    },
                },
                "required": ["target_name", "message"],
            },
            handler=lambda **kwargs: _delegate_task(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_subagents",
            description="List available sub-agents that can receive delegated tasks.",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_subagents(**kwargs),
        )
    )

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
                "Create a new workspace tool (manifest.json + tool.py) in one call under "
                "`tools/<slug>/`. Prefer this over chaining propose_change for new tools."
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
            name="fetch_url",
            description=(
                "Fetch the contents of an HTTP(S) URL. Returns up to 200 KB of text. "
                "Use to read web pages, JSON APIs, or RSS feeds when building or running a skill."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute URL to fetch (http or https)."},
                    "method": {"type": "string", "description": "HTTP method (default GET)."},
                    "headers": {
                        "type": "object",
                        "description": "Optional HTTP headers as a flat string-to-string map.",
                    },
                },
                "required": ["url"],
            },
            handler=lambda **kwargs: _fetch_url(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_patches",
            description="List recent patch proposals for this agent, optionally filtered by status.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status: pending_review, approved, applied, rejected, rolled_back.",
                    },
                },
            },
            handler=lambda **kwargs: _list_patches(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="schedule_task",
            description=(
                "Create a recurring scheduled task for THIS agent. At each trigger the "
                "scheduler will invoke the agent with the given message. Use this when "
                "the user asks for a daily/weekly/periodic task (e.g. 'every day at 18:00')."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "schedule_expr": {
                        "type": "string",
                        "description": "Standard 5-field cron expression, e.g. '0 18 * * *' for every day at 18:00.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The prompt the agent will receive when the task fires.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone (default 'UTC'). Note: cron fields are currently evaluated in UTC.",
                    },
                },
                "required": ["schedule_expr", "message"],
            },
            handler=lambda **kwargs: _schedule_task(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_scheduled_tasks",
            description="List scheduled tasks owned by this agent.",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_scheduled_tasks(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="cancel_scheduled_task",
            description="Delete a scheduled task owned by this agent.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID of the ScheduledTask to delete."}
                },
                "required": ["task_id"],
            },
            handler=lambda **kwargs: _cancel_scheduled_task(**kwargs),
        )
    )


def _read_workspace_file(_agent=None, filename=None, path=None, file=None, name=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    # Accept common aliases the model might emit.
    filename = filename or path or file or name
    from app.workspace.manager import list_files, read_file

    if not filename:
        available = list_files(_agent)
        return {
            "error": "Missing required argument 'filename'.",
            "hint": "Call this tool again with filename set to one of the entries in 'available_files'.",
            "available_files": available,
        }

    available = list_files(_agent)
    if filename not in available:
        return {
            "error": f"File '{filename}' not found in workspace.",
            "available_files": available,
        }
    return {"filename": filename, "content": read_file(_agent, filename)}


def _list_workspace_files(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.workspace.manager import list_files

    files = list_files(_agent)
    return {"files": files}


def _delegate_task(_agent=None, _run_id=None, target_name=None, message=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    missing = [k for k, v in (("target_name", target_name), ("message", message)) if not v]
    if missing:
        return {"error": f"Missing required argument(s): {', '.join(missing)}"}
    from app.services.subagent_service import delegate_task_by_name

    return delegate_task_by_name(_agent.id, target_name, message, parent_run_id=_run_id)


def _list_subagents(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.services.subagent_service import list_subagents

    subagents = list_subagents(_agent.id)
    return {
        "subagents": [
            {"id": a.id, "name": a.name, "slug": a.slug, "status": a.status}
            for a in subagents
        ]
    }


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


_SKILL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,50}$")


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

    from app.services.patch_service import propose_change

    skill_md = f"# {title}\n\n{summary}\n\n{instructions}\n"
    manifest = {"name": slug, "description": summary, "version": "0.1.0"}
    outputs = []
    try:
        manifest_patch = propose_change(
            agent_id=_agent.id,
            target_path=f"skills/{slug}/manifest.json",
            new_content=json.dumps(manifest, indent=2) + "\n",
            title=f"Create skill manifest '{slug}'",
            reason=summary,
            run_id=_run_id,
        )
        outputs.append({"file": f"skills/{slug}/manifest.json", "patch_id": manifest_patch.id, "status": manifest_patch.status})
        md_patch = propose_change(
            agent_id=_agent.id,
            target_path=f"skills/{slug}/SKILL.md",
            new_content=skill_md,
            title=f"Create skill '{slug}'",
            reason=summary,
            run_id=_run_id,
        )
        outputs.append({"file": f"skills/{slug}/SKILL.md", "patch_id": md_patch.id, "status": md_patch.status})
        if code:
            code_patch = propose_change(
                agent_id=_agent.id,
                target_path=f"skills/{slug}/skill.py",
                new_content=code,
                title=f"Create skill code for '{slug}'",
                reason=summary,
                run_id=_run_id,
            )
            outputs.append({"file": f"skills/{slug}/skill.py", "patch_id": code_patch.id, "status": code_patch.status})
    except ValueError as e:
        return {"error": str(e), "created": outputs}

    from app.workspace.discovery import sync_skills_to_db
    sync_skills_to_db(_agent)

    from app.services.review_service import review_creation
    review_payload = f"# {title}\n\n{summary}\n\n{instructions}"
    if code:
        review_payload += f"\n\n---\n# skill.py\n```python\n{code}\n```"
    review = review_creation(_agent, "skill", slug, review_payload, run_id=_run_id)

    result = {"slug": slug, "created": outputs, "message": "Skill scaffold written and indexed."}
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
    if not _SKILL_SLUG_RE.match(slug):
        return {"error": "slug must be lowercase kebab-case."}
    if not isinstance(parameters_schema, dict):
        return {"error": "parameters_schema must be a JSON object."}
    if "def handler" not in code:
        return {"error": "code must define a `handler` function."}

    from app.services.patch_service import propose_change

    manifest = {
        "name": slug,
        "description": description,
        "version": "0.1.0",
        "parameters": parameters_schema,
    }

    outputs = []
    try:
        man_patch = propose_change(
            agent_id=_agent.id,
            target_path=f"tools/{slug}/manifest.json",
            new_content=json.dumps(manifest, indent=2) + "\n",
            title=f"Create tool '{slug}'",
            reason=description,
            run_id=_run_id,
        )
        outputs.append({"file": f"tools/{slug}/manifest.json", "patch_id": man_patch.id, "status": man_patch.status})
        code_patch = propose_change(
            agent_id=_agent.id,
            target_path=f"tools/{slug}/tool.py",
            new_content=code if code.endswith("\n") else code + "\n",
            title=f"Create tool code for '{slug}'",
            reason=description,
            run_id=_run_id,
        )
        outputs.append({"file": f"tools/{slug}/tool.py", "patch_id": code_patch.id, "status": code_patch.status})
    except ValueError as e:
        return {"error": str(e), "created": outputs}

    from app.services.review_service import review_creation
    import json as _json
    review_payload = (
        f"# Tool '{slug}'\n\n{description}\n\n"
        f"Parameters schema:\n```json\n{_json.dumps(parameters_schema, indent=2)}\n```\n\n"
        f"Handler:\n```python\n{code}\n```"
    )
    review = review_creation(_agent, "tool", slug, review_payload, run_id=_run_id)

    result = {"slug": slug, "created": outputs, "message": "Tool scaffold written."}
    if review is not None:
        result["review"] = review
    return result


_FETCH_MAX_BYTES = 200_000


def _fetch_url(_agent=None, url=None, method="GET", headers=None, **kwargs):
    if not url:
        return {"error": "Missing required argument 'url'"}
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"error": "URL must start with http:// or https://"}
    import httpx

    try:
        with httpx.Client(follow_redirects=True, timeout=20.0) as client:
            resp = client.request(method.upper(), url, headers=headers or None)
    except httpx.HTTPError as e:
        return {"error": f"Request failed: {e}"}

    body = resp.text
    truncated = False
    if len(body.encode("utf-8", errors="replace")) > _FETCH_MAX_BYTES:
        body = body[:_FETCH_MAX_BYTES]
        truncated = True

    return {
        "url": str(resp.url),
        "status": resp.status_code,
        "content_type": resp.headers.get("content-type"),
        "body": body,
        "truncated": truncated,
    }


def _schedule_task(_agent=None, _run_id=None, schedule_expr=None, message=None, timezone=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    missing = [k for k, v in (("schedule_expr", schedule_expr), ("message", message)) if not v]
    if missing:
        return {"error": f"Missing required argument(s): {', '.join(missing)}"}
    from croniter import croniter
    if not croniter.is_valid(schedule_expr):
        return {"error": f"Invalid cron expression: {schedule_expr!r}. Expected 5 fields, e.g. '0 18 * * *'."}
    from app.services.scheduler_service import create_task

    task = create_task(
        agent_id=_agent.id,
        task_type="cron",
        schedule_expr=schedule_expr,
        timezone_str=timezone or "UTC",
        payload_json={"message": message},
    )

    from app.services.review_service import review_creation
    review_payload = (
        f"Cron: `{schedule_expr}` (tz={timezone or 'UTC'})\n"
        f"Next run: {task.next_run_at.isoformat() if task.next_run_at else 'n/a'}\n\n"
        f"Prompt that will fire:\n---\n{message}\n---"
    )
    review = review_creation(_agent, "scheduled_task", str(task.id), review_payload, run_id=_run_id)

    result = {
        "task_id": task.id,
        "schedule_expr": task.schedule_expr,
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "enabled": task.enabled,
        "message": "Scheduled task created. The worker will pick it up within ~30s.",
    }
    if review is not None:
        result["review"] = review
    return result


def _list_scheduled_tasks(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.services.scheduler_service import list_tasks

    tasks = list_tasks(agent_id=_agent.id)
    return {
        "tasks": [
            {
                "id": t.id,
                "task_type": t.task_type,
                "schedule_expr": t.schedule_expr,
                "enabled": t.enabled,
                "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
                "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
                "message": (t.payload_json or {}).get("message"),
            }
            for t in tasks
        ]
    }


def _cancel_scheduled_task(_agent=None, task_id=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not task_id:
        return {"error": "Missing required argument 'task_id'"}
    from app.services.scheduler_service import delete_task, get_task

    task = get_task(task_id)
    if task is None:
        return {"error": f"Task {task_id} not found"}
    if task.agent_id != _agent.id:
        return {"error": f"Task {task_id} does not belong to this agent"}
    delete_task(task_id)
    return {"task_id": task_id, "message": "Scheduled task deleted."}


def _list_patches(_agent=None, status=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.services.patch_service import list_patches

    patches = list_patches(agent_id=_agent.id, status=status)
    return {
        "patches": [
            {
                "id": p.id,
                "title": p.title,
                "target_path": p.target_path,
                "status": p.status,
                "security_level": p.security_level,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in patches[:20]
        ]
    }
