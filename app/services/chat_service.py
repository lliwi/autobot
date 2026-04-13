import json

from flask import current_app

from app.extensions import db
from app.models.agent import Agent
from app.runtime.agent_runner import run as agent_run
from app.runtime.tool_registry import register_builtin_tools
from app.services.run_service import create_run, finish_run
from app.services.session_service import add_message, get_or_create_session


def stream_response(agent_id, message, session_id=None):
    """Orchestrate a chat interaction. Generator yielding SSE-formatted JSON strings."""
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        yield json.dumps({"type": "error", "data": "Agent not found"})
        return

    if agent.status != "active":
        yield json.dumps({"type": "error", "data": f"Agent '{agent.name}' is not active"})
        return

    # Ensure built-in tools are registered
    register_builtin_tools()

    # Get or create session
    session = get_or_create_session(agent_id, session_id=session_id)

    # Persist user message
    add_message(session.id, role="user", content=message)

    # Create run record
    run = create_run(agent_id=agent.id, session_id=session.id)

    # Run agent and stream response
    full_response = ""
    usage = {}
    error = None

    try:
        for chunk_json in agent_run(agent, session, message, run.id):
            chunk = json.loads(chunk_json)

            if chunk["type"] == "done":
                full_response = chunk.get("data", "")
                usage = chunk.get("usage", {})
            elif chunk["type"] == "error":
                error = chunk.get("data", "Unknown error")

            yield chunk_json

    except Exception as e:
        current_app.logger.error(f"Chat stream error: {e}")
        error = str(e)
        yield json.dumps({"type": "error", "data": error})

    # Persist assistant response
    if full_response:
        add_message(
            session.id,
            role="assistant",
            content=full_response,
            token_count=usage.get("output_tokens"),
        )

    # Finalize run
    finish_run(
        run.id,
        status="error" if error else "completed",
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        error_summary=error,
    )
