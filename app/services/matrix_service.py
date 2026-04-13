import re

from flask import current_app


def is_user_allowed(user_id):
    """Check if a Matrix user is in the allowlist."""
    allowed = current_app.config.get("MATRIX_ALLOWED_USERS", "")
    if not allowed:
        return True  # No allowlist = allow all
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
    """Get the agent assigned to a Matrix room. Falls back to first active agent."""
    from app.models.agent import Agent

    # For MVP, use the first active agent
    # TODO: Phase 4 will add room-to-agent mapping
    agent = Agent.query.filter_by(status="active").first()
    return agent
