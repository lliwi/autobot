from flask import jsonify, request

from app.api import api_bp
from app.api.middleware import auth_required
from app.services.tool_service import (
    assign_tool_to_agent,
    create_tool,
    get_tool,
    list_tools,
    remove_tool_from_agent,
    sync_agent_tools,
    test_tool,
    toggle_tool,
)


@api_bp.route("/tools")
@auth_required
def list_tools_api():
    """List tools. With ?agent_id=N, only tools assigned to that agent."""
    agent_id = request.args.get("agent_id", type=int)
    tools = list_tools(agent_id=agent_id)
    return jsonify([t.to_dict() for t in tools])


@api_bp.route("/tools", methods=["POST"])
@auth_required
def create_tool_api():
    """Create a global tool. Requires agent_id (the creator, auto-assigned)."""
    data = request.get_json()
    if not data or "agent_id" not in data or "name" not in data:
        return jsonify(error="agent_id and name required"), 400
    try:
        tool = create_tool(data["agent_id"], data)
        return jsonify(tool.to_dict()), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400


@api_bp.route("/tools/<int:tool_id>", methods=["PATCH"])
@auth_required
def update_tool_api(tool_id):
    tool = get_tool(tool_id)
    if tool is None:
        return jsonify(error="Tool not found"), 404

    data = request.get_json()
    if not data:
        return jsonify(error="No data provided"), 400

    from app.extensions import db

    for field in ("name", "description", "version", "timeout"):
        if field in data:
            setattr(tool, field, data[field])
    db.session.commit()
    return jsonify(tool.to_dict())


@api_bp.route("/tools/<int:tool_id>/assign", methods=["POST"])
@auth_required
def assign_tool_api(tool_id):
    """Give an agent access to a global tool."""
    agent_id = (request.get_json() or {}).get("agent_id") if request.is_json else None
    if not agent_id:
        return jsonify(error="agent_id required"), 400
    try:
        at = assign_tool_to_agent(tool_id, agent_id)
        return jsonify({"tool_id": at.tool_id, "agent_id": at.agent_id, "enabled": at.enabled}), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400


@api_bp.route("/tools/<int:tool_id>/unassign", methods=["POST"])
@auth_required
def unassign_tool_api(tool_id):
    agent_id = (request.get_json() or {}).get("agent_id") if request.is_json else None
    if not agent_id:
        return jsonify(error="agent_id required"), 400
    try:
        remove_tool_from_agent(tool_id, agent_id)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify(error=str(e)), 400


@api_bp.route("/tools/<int:tool_id>/toggle", methods=["POST"])
@auth_required
def toggle_tool_api(tool_id):
    """Toggle a tool's enabled state for a given agent."""
    agent_id = (request.get_json() or {}).get("agent_id") if request.is_json else request.args.get("agent_id", type=int)
    if not agent_id:
        return jsonify(error="agent_id required"), 400
    at = toggle_tool(tool_id, agent_id)
    if at is None:
        return jsonify(error="Assignment not found"), 404
    return jsonify({"tool_id": at.tool_id, "agent_id": at.agent_id, "enabled": at.enabled})


@api_bp.route("/tools/<int:tool_id>/test", methods=["POST"])
@auth_required
def test_tool_api(tool_id):
    body = request.get_json() if request.is_json else {}
    agent_id = (body or {}).get("agent_id")
    if not agent_id:
        return jsonify(error="agent_id required"), 400
    test_input = (body or {}).get("input", {})
    result = test_tool(tool_id, agent_id, test_input)
    return jsonify(result)


@api_bp.route("/tools/sync", methods=["POST"])
@auth_required
def sync_tools_api():
    agent_id = request.get_json().get("agent_id") if request.is_json else request.args.get("agent_id", type=int)
    if not agent_id:
        return jsonify(error="agent_id required"), 400
    tools = sync_agent_tools(agent_id)
    return jsonify([t.to_dict() for t in tools])
