from flask import current_app

from app.models.message import Message
from app.workspace.loader import load_agents, load_memory, load_soul, load_tools

TOOL_PROTOCOL = """## Tool Usage Protocol

Before any tool call, write **one short line of plan** describing the next step. Then call the tool.

Hard rules:
- NEVER call a tool without its `required` arguments filled in. If you don't know a value, ask the user or obtain it with a different tool first.
- Do not repeat the same tool call with the same arguments. The runtime aborts the run after 3 identical repetitions.
- Prefer one precise call over many exploratory ones. Do not call a tool "to see what happens".
- When a tool returns `{"error": ...}`, read the hint, correct the arguments, and try a different approach. Do not retry unchanged.

Built-in tool cheatsheet (always call with JSON objects like these):
- `list_workspace_files` — no args. Use once at the start if you need to discover files.
- `read_workspace_file` — `{"filename": "SOUL.md"}`. filename must already exist in the workspace.
- `fetch_url` — `{"url": "https://example.com"}`. Absolute http(s) URL required.
- `propose_change` — `{"target_path": "MEMORY.md", "new_content": "...full content...", "title": "Update memory", "reason": "why"}`. All four fields required. Use for single-file edits.
- `create_skill` — `{"slug": "weather-bcn", "title": "Weather Barcelona", "summary": "...", "instructions": "...markdown...", "code": "def handler(...): ..."}`. Preferred over two propose_change calls when creating a skill.
- `create_tool` — `{"slug": "echo2", "description": "...", "parameters_schema": {"type":"object","properties":{...},"required":[...]}, "code": "def handler(_agent=None, **kwargs): return {...}"}`.
- `delegate_task` — `{"target_name": "reviewer", "message": "review the patch #42"}`.
- `get_current_time` — no args. Returns ISO-8601 UTC.
- `list_subagents` / `list_patches` — no required args.

Multi-step task template:
1. State the plan in one line.
2. Gather any information you need (one tool call at a time).
3. Produce the artefact(s) with `propose_change`.
4. Summarise what you did for the user.
"""


def build_context(agent, session, user_message):
    """Build the messages array for the OpenAI API call."""
    max_history = current_app.config["MAX_HISTORY_MESSAGES"]

    # System prompt from workspace files
    soul = load_soul(agent)
    tools_doc = load_tools(agent)
    agents_doc = load_agents(agent)
    memory = load_memory(agent)

    system_parts = [TOOL_PROTOCOL]
    if soul:
        system_parts.append(f"## Identity and Principles\n{soul}")
    if tools_doc:
        system_parts.append(f"## Available Tools\n{tools_doc}")
    if agents_doc:
        system_parts.append(f"## Agent Network\n{agents_doc}")
    if memory:
        system_parts.append(f"## Memory\n{memory}")

    # Inject enabled skill descriptions into system prompt
    from app.workspace.discovery import get_enabled_skills
    from app.workspace.manager import read_file

    for skill in get_enabled_skills(agent):
        skill_md = read_file(agent, f"{skill.path}/SKILL.md")
        if skill_md:
            system_parts.append(f"## Skill: {skill.name}\n{skill_md}")

    messages = []

    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

    # Load message history
    history = (
        Message.query.filter_by(session_id=session.id)
        .order_by(Message.created_at.asc())
        .limit(max_history)
        .all()
    )

    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})

    # Add the new user message
    messages.append({"role": "user", "content": user_message})

    return messages
