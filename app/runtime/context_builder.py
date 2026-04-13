from flask import current_app

from app.models.message import Message
from app.workspace.loader import load_agents, load_memory, load_soul, load_tools


def build_context(agent, session, user_message):
    """Build the messages array for the OpenAI API call."""
    max_history = current_app.config["MAX_HISTORY_MESSAGES"]

    # System prompt from workspace files
    soul = load_soul(agent)
    tools_doc = load_tools(agent)
    agents_doc = load_agents(agent)
    memory = load_memory(agent)

    system_parts = []
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
