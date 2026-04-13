import json
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.models.tool_execution import ToolExecution
from app.runtime.tool_registry import get as get_tool


def execute(run_id, agent, tool_name, arguments):
    """Execute a tool and record the execution."""
    tool_def = get_tool(tool_name)
    if tool_def is None:
        # Try workspace tool
        from app.workspace.discovery import load_tool_handler

        handler = load_tool_handler(agent, tool_name)
        if handler is None:
            return {"error": f"Unknown tool: {tool_name}"}

        execution = ToolExecution(
            run_id=run_id,
            agent_id=agent.id,
            tool_name=tool_name,
            input_json=arguments,
            status="running",
        )
        db.session.add(execution)
        db.session.commit()

        try:
            result = handler(_agent=agent, **arguments)
            execution.output_json = result
            execution.status = "success"
        except Exception as e:
            current_app.logger.error(f"Workspace tool error: {tool_name}: {e}")
            execution.output_json = {"error": str(e)}
            execution.status = "error"
        finally:
            execution.finished_at = datetime.now(timezone.utc)
            db.session.commit()

        return execution.output_json

    execution = ToolExecution(
        run_id=run_id,
        agent_id=agent.id,
        tool_name=tool_name,
        input_json=arguments,
        status="running",
    )
    db.session.add(execution)
    db.session.commit()

    try:
        # Inject agent context for workspace tools
        result = tool_def.handler(_agent=agent, **arguments)
        execution.output_json = result
        execution.status = "success"
    except Exception as e:
        current_app.logger.error(f"Tool execution error: {tool_name}: {e}")
        execution.output_json = {"error": str(e)}
        execution.status = "error"
    finally:
        execution.finished_at = datetime.now(timezone.utc)
        db.session.commit()

    return execution.output_json
