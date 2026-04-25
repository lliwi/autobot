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
            description=(
                "Read a file from the agent's workspace. Path is relative to the workspace root. "
                "Reference docs (TOOLS.md, AGENTS.md, skills/<slug>/SKILL.md) are NOT pre-loaded "
                "into context — use this tool to fetch them when the current task needs them. "
                "The same file is cached per-run: re-reading it in the same turn returns a stub "
                "so you don't pay the tokens twice."
            ),
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

    register(
        ToolDefinition(
            name="get_credential",
            description=(
                "Fetch a decrypted secret by name. Lookup order: credentials scoped to this agent "
                "first, then global. Response shape depends on credential type: "
                "{type: 'token', name, value} for API keys/tokens; "
                "{type: 'user_password', name, username, password} for username+password pairs. "
                "Treat values as sensitive — never echo them back to the user or log them. "
                "Use the value as-is: do NOT validate its prefix, length, or format based on "
                "your prior knowledge of what that provider's tokens should look like. Provider "
                "token formats change (e.g. Notion moved from 'secret_' to 'ntn_' in 2024). "
                "If the downstream API rejects the credential, report the API's exact error "
                "message verbatim — do not speculate about format."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Credential name, e.g. 'github_token'."}
                },
                "required": ["name"],
            },
            handler=lambda **kwargs: _get_credential(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_credentials",
            description=(
                "List credential names available to this agent (names + descriptions only — "
                "values are never returned). Includes agent-scoped and global credentials."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_credentials(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="set_credential",
            description=(
                "Create or update an agent-scoped credential. Encrypted at rest. Use when the "
                "user shares a secret in chat so future runs can reuse it. Two shapes: "
                "credential_type='token' stores a single value (API key, token). "
                "credential_type='user_password' stores a username+password pair — pass the "
                "password in 'value' and the login in 'username'. Agents cannot create global "
                "credentials — that's admin-only from the dashboard."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Unique name, letters/digits/._- only."},
                    "value": {"type": "string", "description": "Secret to store. For user_password this is the password."},
                    "credential_type": {
                        "type": "string",
                        "enum": ["token", "user_password"],
                        "description": "'token' (default) or 'user_password'.",
                    },
                    "username": {
                        "type": "string",
                        "description": "Login/username. Required when credential_type is 'user_password'.",
                    },
                    "description": {"type": "string", "description": "Optional human-readable note."},
                },
                "required": ["name", "value"],
            },
            handler=lambda **kwargs: _set_credential(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="install_package",
            description=(
                "Request a Python package install in this agent's isolated workspace "
                "venv. Packages on the platform allowlist install immediately; anything "
                "else is queued for admin approval. Use this when a skill or tool needs "
                "an import that's not already available (e.g. 'feedparser', 'pandas'). "
                "Returns {status: 'installed'|'pending_review'|'failed', ...}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "string",
                        "description": (
                            "PyPI install spec, e.g. 'feedparser' or 'pandas>=2,<3'. "
                            "Only PyPI names + version specifiers are accepted — no git URLs, "
                            "paths, or pip flags."
                        ),
                    }
                },
                "required": ["spec"],
            },
            handler=lambda **kwargs: _install_package(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_packages",
            description=(
                "List Python packages this agent has installed (or requested) in its "
                "workspace venv, with their status: installed, pending_review, failed, "
                "rejected."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_packages(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="delete_credential",
            description=(
                "Delete an agent-scoped credential by name. Does not touch global credentials "
                "(those are managed from the dashboard by the admin)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Credential name to delete."}
                },
                "required": ["name"],
            },
            handler=lambda **kwargs: _delete_credential(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="run_bash",
            description=(
                "Run a shell command inside the agent's workspace. Use this to execute "
                "`.sh` scripts stored under the workspace, run quick one-liners, or chain "
                "CLI tools. The command always runs with cwd set inside the workspace "
                "(optionally a subdirectory via `workdir`). The agent's per-workspace "
                "venv, if one exists, is prepended to PATH so packages installed through "
                "`install_package` are importable. Provide EITHER `command` (one-liner "
                "evaluated with `bash -lc`) OR `script` (multi-line bash body, wrapped "
                "with `set -euo pipefail`). Output is truncated to ~20k characters."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell one-liner. Mutually exclusive with `script`.",
                    },
                    "script": {
                        "type": "string",
                        "description": "Multi-line bash body. Mutually exclusive with `command`.",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Working directory relative to the workspace root. Defaults to the workspace root.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Hard timeout in seconds (1..600). Defaults to 30.",
                    },
                },
            },
            handler=lambda **kwargs: _run_bash(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="matrix_send",
            description=(
                "Send a direct message to a Matrix user via the bot that already runs "
                "in this process (reuses its login — no extra credentials needed). "
                "Use this to proactively deliver results from cron/heartbeat tasks to "
                "a user outside of an ongoing conversation. The target must be a full "
                "Matrix ID like '@alice:example.org'. The bot will open a DM room if "
                "one does not already exist."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "Full Matrix ID, e.g. '@alice:example.org'.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Plain-text body of the message to send.",
                    },
                },
                "required": ["user_id", "message"],
            },
            handler=lambda **kwargs: _matrix_send(**kwargs),
        )
    )


# Per-run cache of files already served by ``read_workspace_file``. Lets us
# return a cheap stub on the second read within the same turn instead of
# re-serializing the file into the model's context. Entries live for the run
# and are cleaned up by ``forget_run_reads`` at the end.
_RUN_READ_CACHE: dict[int, set[str]] = {}


def forget_run_reads(run_id: int | None) -> None:
    """Drop the per-run read cache once a run completes."""
    if run_id is None:
        return
    _RUN_READ_CACHE.pop(run_id, None)


def _read_workspace_file(_agent=None, _run_id=None, filename=None, path=None, file=None, name=None, **kwargs):
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

    # Dedup within the same run: the model already has the content in its
    # context from the earlier read, so returning it again is pure waste.
    if _run_id is not None:
        seen = _RUN_READ_CACHE.setdefault(_run_id, set())
        if filename in seen:
            return {
                "filename": filename,
                "cached": True,
                "note": (
                    "Already read earlier in this run — the full content is"
                    " in your context above. Do not re-request unless the"
                    " file may have changed (you proposed a patch to it)."
                ),
            }
        seen.add(filename)

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

    from app.workspace.discovery import sync_tools_to_db
    sync_tools_to_db(_agent)

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


def _get_credential(_agent=None, name=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not name:
        return {"error": "Missing required argument 'name'"}
    from app.services.credential_service import CredentialError, get_credential_pair

    try:
        pair = get_credential_pair(name, agent_id=_agent.id)
    except CredentialError as e:
        return {"error": str(e)}
    if pair is None:
        return {"error": f"Credential '{name}' not found"}
    return {"name": name, **pair}


def _list_credentials(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    import os
    from app.services.credential_service import (
        CredentialError,
        list_credentials,
        _ENV_PREFIX,
    )

    try:
        rows = list_credentials(agent_id=_agent.id)
    except CredentialError as e:
        return {"error": str(e)}
    items = [
        {
            "name": r.name,
            "description": r.description,
            "type": r.credential_type,
            "username": r.username if r.credential_type == "user_password" else None,
            "scope": "agent" if r.agent_id == _agent.id else "global",
            "source": "db",
        }
        for r in rows
    ]
    seen_db_names = {r.name for r in rows}
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(_ENV_PREFIX) or not env_val:
            continue
        name = env_key[len(_ENV_PREFIX):].lower()
        if not name or name in seen_db_names:
            continue
        items.append({
            "name": name,
            "description": f"Provided via .env (var {env_key})",
            "type": "token",
            "username": None,
            "scope": "env",
            "source": "env",
        })
    return {"credentials": items}


def _set_credential(_agent=None, name=None, value=None, description=None,
                    credential_type=None, username=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    missing = [k for k, v in (("name", name), ("value", value)) if not v]
    if missing:
        return {"error": f"Missing required argument(s): {', '.join(missing)}"}
    from app.services.credential_service import CredentialError, set_credential

    try:
        row = set_credential(
            name=name,
            value=value,
            description=description,
            credential_type=(credential_type or "token"),
            username=username,
            agent_id=_agent.id,
        )
    except CredentialError as e:
        return {"error": str(e)}
    return {
        "name": row.name,
        "type": row.credential_type,
        "username": row.username if row.credential_type == "user_password" else None,
        "scope": "agent",
        "message": f"Credential '{row.name}' stored securely.",
    }


def _install_package(_agent=None, _run_id=None, spec=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not spec:
        return {"error": "Missing required argument 'spec'"}
    from app.services import package_service
    try:
        row = package_service.request_install(_agent, spec, run_id=_run_id)
    except package_service.PackageError as e:
        return {"error": str(e)}

    payload = {
        "name": row.name,
        "spec": row.spec,
        "status": row.status,
        "installed_version": row.installed_version,
    }
    if row.status == "installed":
        payload["message"] = (
            f"Package '{row.name}' {row.installed_version or ''} installed. "
            f"You can import it now."
        ).strip()
    elif row.status == "pending_review":
        payload["message"] = (
            f"'{row.name}' is not on the allowlist — waiting for admin approval "
            f"from the Packages dashboard. Retry list_packages later."
        )
    elif row.status == "failed":
        payload["message"] = row.reason or "install failed"
        payload["stderr_tail"] = (row.stderr_tail or "")[-500:]
    else:
        payload["message"] = f"status: {row.status}"
    return payload


def _list_packages(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.models.package_installation import PackageInstallation
    rows = (
        PackageInstallation.query
        .filter_by(agent_id=_agent.id)
        .order_by(PackageInstallation.status, PackageInstallation.name)
        .all()
    )
    return {
        "packages": [
            {
                "name": r.name,
                "spec": r.spec,
                "status": r.status,
                "version": r.installed_version,
                "reason": r.reason,
            }
            for r in rows
        ]
    }


def _delete_credential(_agent=None, name=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not name:
        return {"error": "Missing required argument 'name'"}
    from app.models.credential import Credential
    from app.services.credential_service import delete_credential

    row = Credential.query.filter_by(agent_id=_agent.id, name=name).first()
    if row is None:
        return {"error": f"Credential '{name}' not found for this agent (global credentials are admin-only)."}
    delete_credential(row.id)
    return {"name": name, "message": "Credential deleted."}


_BASH_OUTPUT_LIMIT = 20000


def _run_bash(_agent=None, _run_id=None, command=None, script=None,
              workdir=None, timeout=30, **kwargs):
    """Run bash inside the agent's workspace, with path and time containment.

    The subprocess inherits the worker's env (so outbound HTTP/DNS work) but
    has cwd pinned inside ``workspace_path``. We also prepend the per-workspace
    venv's bin dir to PATH when present so the agent can call packages it
    installed via ``install_package`` without activating the venv by hand.
    """
    import os
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    if _agent is None:
        return {"error": "No agent context"}

    has_cmd = bool(command)
    has_script = bool(script)
    if has_cmd == has_script:
        return {"error": "provide exactly one of 'command' or 'script'"}

    try:
        timeout = int(timeout) if timeout is not None else 30
    except (TypeError, ValueError):
        return {"error": "timeout must be an integer"}
    if timeout < 1 or timeout > 600:
        return {"error": "timeout must be in 1..600 seconds"}

    workspace_root = Path(_agent.workspace_path).resolve()
    if not workspace_root.is_dir():
        return {"error": f"workspace not found at {workspace_root}"}

    # Resolve workdir relative to the workspace, reject escape attempts.
    rel = (workdir or ".").strip()
    if os.path.isabs(rel):
        return {"error": "workdir must be relative to the workspace root"}
    cwd = (workspace_root / rel).resolve()
    try:
        cwd.relative_to(workspace_root)
    except ValueError:
        return {"error": "workdir escapes the workspace"}
    if not cwd.is_dir():
        return {"error": f"workdir does not exist: {rel}"}

    env = os.environ.copy()
    venv_bin = None
    for candidate in (workspace_root / ".venv" / "bin",
                      workspace_root / "venv" / "bin"):
        if (candidate / "python").exists():
            venv_bin = candidate
            break
    if venv_bin is not None:
        env["VIRTUAL_ENV"] = str(venv_bin.parent)
        env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"

    def _trim(text):
        text = text or ""
        if len(text) <= _BASH_OUTPUT_LIMIT:
            return text
        return text[-_BASH_OUTPUT_LIMIT:] + "\n[truncated]"

    temp_path = None
    try:
        if has_script:
            fd, temp_path = tempfile.mkstemp(
                suffix=".sh", dir=str(workspace_root), text=True,
            )
            with os.fdopen(fd, "w") as f:
                f.write("#!/usr/bin/env bash\nset -euo pipefail\n")
                f.write(script)
            os.chmod(temp_path, 0o700)
            argv = ["bash", temp_path]
        else:
            argv = ["bash", "-lc", command]

        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": _trim(proc.stdout),
            "stderr": _trim(proc.stderr),
            "venv_active": venv_bin is not None,
            "cwd": str(cwd.relative_to(workspace_root)) or ".",
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "error": "timeout",
            "timeout": timeout,
            "stdout": _trim(e.stdout if isinstance(e.stdout, str) else ""),
            "stderr": _trim(e.stderr if isinstance(e.stderr, str) else ""),
        }
    except FileNotFoundError:
        return {"error": "bash not available in this process"}
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _matrix_send(_agent=None, user_id=None, message=None, **kwargs):
    """Send a Matrix DM or room message.

    Works from any process. When the MatrixBot is available in-process the
    message is sent synchronously. Otherwise it is placed in a Redis outbox
    and dispatched by the worker's drain loop (typically within a few seconds).

    ``user_id`` accepts either a Matrix user ID (``@user:server``) for a DM
    or a room ID (``!room:server``) to post into a specific room.
    """
    if not user_id:
        return {"error": "Missing required argument 'user_id' (Matrix user ID or room ID)."}
    if not message:
        return {"error": "Missing required argument 'message'."}

    target = user_id.strip()
    is_room = target.startswith("!")
    is_user = target.startswith("@") and ":" in target
    if not is_room and not is_user:
        return {"error": f"Invalid Matrix target '{target}'. Expected '@user:server' or '!room:server'."}

    from flask import current_app

    bot = getattr(current_app, "matrix_bot", None)
    if bot is not None:
        # In-process (worker): send immediately.
        if is_room:
            return bot.send_to_room(target, message)
        return bot.send_dm(target, message)

    # Web process: enqueue via Redis outbox; worker will dispatch.
    try:
        from app.services.matrix_outbox import enqueue
        enqueue(target, message)
        return {"ok": True, "queued": True, "note": "Message queued — will be sent by the worker within seconds."}
    except Exception as e:
        return {"error": f"Failed to queue Matrix message: {e}"}
