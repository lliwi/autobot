"""Inline-steering tools: let the agent queue a follow-up turn for itself."""
from app.runtime.tool_registry.core import ToolDefinition, register


def register_steering_tools():
    register(
        ToolDefinition(
            name="queue_followup",
            description=(
                "Queue a message to run as a NEW turn in this same chat session right "
                "after the current task finishes. Use this when the user interjected "
                "with a separate, short-lived task that should run next — not folded "
                "into what you are doing now, and not a long-running background goal "
                "(use create_objective for those). The queued message runs as if the "
                "user had just sent it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The task/prompt to run next, phrased as a user request.",
                    },
                },
                "required": ["message"],
            },
            handler=lambda **kwargs: _queue_followup(**kwargs),
        )
    )


def _queue_followup(_agent=None, _run_id=None, message=None, **kwargs):
    if not message or not message.strip():
        return {"error": "message is required"}
    if not _run_id:
        return {"error": "no run context — cannot resolve the session to queue into"}

    from app.extensions import db
    from app.models.run import Run
    from app.services.steering_service import queue_followup

    run = db.session.get(Run, _run_id)
    if run is None or run.session_id is None:
        return {"error": "this run has no chat session to queue a follow-up into"}

    if not queue_followup(run.session_id, message):
        return {"error": "could not queue follow-up (steering backend unavailable or full)"}
    return {
        "queued": True,
        "session_id": run.session_id,
        "message": "Follow-up queued — it will run as a new turn once the current task finishes.",
    }
