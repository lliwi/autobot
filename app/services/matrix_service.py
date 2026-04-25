import re

from flask import current_app


def is_user_allowed(user_id):
    """Check if a Matrix user is in the allowlist."""
    allowed = current_app.config.get("MATRIX_ALLOWED_USERS", "")
    if not allowed:
        return True  # No allowlist = allow all
    allowed_list = [u.strip() for u in allowed.split(",") if u.strip()]
    return user_id in allowed_list


def is_dm_user_allowed(user_id):
    """Check if a user is allowed to DM the bot.

    Uses ``MATRIX_ALLOWED_DM_USERS`` when configured. When empty, falls back to
    the generic ``MATRIX_ALLOWED_USERS`` allowlist so admins only need to opt
    in to the DM-specific list when they want stricter-than-global gating.
    """
    allowed = current_app.config.get("MATRIX_ALLOWED_DM_USERS", "")
    if not allowed:
        return is_user_allowed(user_id)
    allowed_list = [u.strip() for u in allowed.split(",") if u.strip()]
    return user_id in allowed_list


def is_room_allowed(room_id):
    """Check if a Matrix room is in the allowlist."""
    allowed = current_app.config.get("MATRIX_ALLOWED_ROOMS", "")
    if not allowed:
        return True  # No allowlist = allow all
    allowed_list = [r.strip() for r in allowed.split(",") if r.strip()]
    return room_id in allowed_list


def should_respond(room_member_count, message_body, bot_user_id, agent):
    """Determine if the bot should respond based on group policy.

    Policies:
    - always: respond to every message
    - mention: respond only when mentioned (DMs always respond)
    - allowlist: respond only in allowed rooms (checked separately)
    """
    policy = agent.group_response_policy

    # DMs (2 members) always get a response
    if room_member_count <= 2:
        return True

    if policy == "always":
        return True

    if policy == "mention":
        # Check if bot is mentioned in the message
        display_name = bot_user_id.split(":")[0].lstrip("@")
        return bot_user_id in message_body or display_name in message_body

    # Default: don't respond in groups
    return False


def get_agent_for_room(room_id):
    """Return the agent that should handle messages from *room_id*.

    Resolution order:
      1. Agent whose ``sync_matrix_room`` matches the room_id exactly.
      2. Agent whose ``forward_matrix_room`` matches the room_id exactly.
      3. Agent with slug matching ``MATRIX_DEFAULT_AGENT_SLUG`` (config).
      4. First active agent (legacy fallback).
    """
    from app.models.agent import Agent

    # 1. Explicit sync mapping
    agent = Agent.query.filter_by(sync_matrix_room=room_id, status="active").first()
    if agent:
        return agent

    # 2. Forward mapping (agent uses this room as its output channel)
    agent = Agent.query.filter_by(forward_matrix_room=room_id, status="active").first()
    if agent:
        return agent

    # 3. Agent flagged as Matrix default in the DB
    agent = Agent.query.filter_by(matrix_default=True, status="active").first()
    if agent:
        return agent

    # 4. Configured default slug (env-var fallback for deployments without DB flag)
    default_slug = current_app.config.get("MATRIX_DEFAULT_AGENT_SLUG", "").strip()
    if default_slug:
        agent = Agent.query.filter_by(slug=default_slug, status="active").first()
        if agent:
            return agent

    # 5. Legacy fallback
    return Agent.query.filter_by(status="active").first()
