import json

from flask import current_app

from app.extensions import db
from app.models.agent import Agent
from app.runtime.action_heuristics import is_task_like, summarize_task
from app.runtime.agent_runner import run as agent_run
from app.runtime.tool_registry import register_builtin_tools
from app.services.run_service import create_run, finish_run
from app.services.session_service import add_message, get_or_create_session


def _prepare_run(agent_id, message, session_id=None, channel_type="web", trigger_type="message",
                  external_chat_id=None, external_user_id=None):
    """Common setup for both streaming and non-streaming agent execution."""
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return None, None, None, None, "Agent not found"

    if agent.status != "active":
        return None, None, None, None, f"Agent '{agent.name}' is not active"

    register_builtin_tools()

    session = get_or_create_session(
        agent_id,
        channel_type=channel_type,
        session_id=session_id,
        external_chat_id=external_chat_id,
        external_user_id=external_user_id,
    )
    add_message(session.id, role="user", content=message)
    run = create_run(agent_id=agent.id, session_id=session.id, trigger_type=trigger_type)

    objective_id = _maybe_create_chat_objective(agent, session, run, message, trigger_type)

    return agent, session, run, objective_id, None


def _maybe_create_chat_objective(agent, session, run, message, trigger_type):
    """Spawn an Objective when a human-facing turn looks like a task.

    Gives the heartbeat supervisor something to chase if the turn doesn't
    finish the work. Skipped for delegation/cron/heartbeat triggers — those
    are either already tracked or don't represent user intent.
    """
    if trigger_type != "message":
        return None
    if not is_task_like(message):
        return None
    from app.services.objective_service import create_objective

    try:
        obj = create_objective(
            agent_id=agent.id,
            title=summarize_task(message),
            description=message,
            context={
                "source": "chat",
                "session_id": session.id,
                "run_id": run.id,
                "channel_type": session.channel_type,
            },
        )
        return obj.id
    except Exception as e:
        current_app.logger.warning(
            "Could not auto-create objective for agent=%s run=%s: %s",
            agent.id, run.id, e,
        )
        return None


def _close_chat_objective(objective_id, succeeded, error_summary=None):
    """Mark ``objective_id`` as done on success, otherwise leave it active for
    the heartbeat supervisor to pick up. Non-fatal on any failure."""
    if objective_id is None:
        return
    from app.models.objective import Objective
    from app.services.objective_service import mark_progress, update_objective

    try:
        obj = db.session.get(Objective, objective_id)
        if obj is None:
            return
        if succeeded:
            mark_progress(obj, note="Chat turn completed — auto-closing objective.")
            update_objective(obj, status="done")
        else:
            mark_progress(
                obj,
                note=f"Chat turn ended without completion: {error_summary or 'unknown'}",
            )
    except Exception as e:
        current_app.logger.warning(
            "Could not close objective %s: %s", objective_id, e
        )


def stream_response(agent_id, message, session_id=None):
    """Orchestrate a chat interaction. Generator yielding SSE-formatted JSON strings."""
    agent, session, run, objective_id, error = _prepare_run(agent_id, message, session_id=session_id)
    if error:
        yield json.dumps({"type": "error", "data": error})
        return

    yield json.dumps({"type": "session", "data": {"id": session.id}})

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
    _close_chat_objective(objective_id, succeeded=not error, error_summary=error)


def run_agent_non_streaming(agent_id, message, session_id=None, channel_type="web",
                            trigger_type="message", external_chat_id=None, external_user_id=None):
    """Run agent and collect the full response. Used by Matrix adapter and scheduler."""
    agent, session, run, objective_id, error = _prepare_run(
        agent_id, message, session_id=session_id, channel_type=channel_type,
        trigger_type=trigger_type, external_chat_id=external_chat_id,
        external_user_id=external_user_id,
    )
    if error:
        return {"error": error, "response": ""}

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
    except Exception as e:
        current_app.logger.error(f"Non-streaming agent error: {e}")
        error = str(e)

    if full_response:
        add_message(session.id, role="assistant", content=full_response,
                    token_count=usage.get("output_tokens"))

    finish_run(
        run.id,
        status="error" if error else "completed",
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        error_summary=error,
    )
    _close_chat_objective(objective_id, succeeded=not error, error_summary=error)

    return {"response": full_response, "error": error, "session_id": session.id, "run_id": run.id}
