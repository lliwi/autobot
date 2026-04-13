from flask import jsonify, request

from app.api import api_bp
from app.api.middleware import auth_required
from app.services.tool_service import (
    create_tool,
    get_tool,
    list_tools,
    sync_agent_tools,
    test_tool,
    toggle_tool,
)


@api_bp.route("/tools")
@auth_required
def list_tools_api():
    agent_id = request.args.get("agent_id", type=int)
    tools = list_tools(agent_id=agent_id)
    return jsonify([t.to_dict() for t in tools])


@api_bp.route("/tools", methods=["POST"])
@auth_required
def create_tool_api():
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

    for field in ("name", "description", "enabled", "version", "timeout"):
        if field in data:
            setattr(tool, field, data[field])
    db.session.commit()
    return jsonify(tool.to_dict())


@api_bp.route("/tools/<int:tool_id>/toggle", methods=["POST"])
@auth_required
def toggle_tool_api(tool_id):
    tool = toggle_tool(tool_id)
    if tool is None:
        return jsonify(error="Tool not found"), 404
    return jsonify(tool.to_dict())


@api_bp.route("/tools/<int:tool_id>/test", methods=["POST"])
@auth_required
def test_tool_api(tool_id):
    test_input = request.get_json().get("input", {}) if request.is_json else {}
    result = test_tool(tool_id, test_input)
    return jsonify(result)


@api_bp.route("/tools/sync", methods=["POST"])
@auth_required
def sync_tools_api():
    agent_id = request.get_json().get("agent_id") if request.is_json else request.args.get("agent_id", type=int)
    if not agent_id:
        return jsonify(error="agent_id required"), 400
    tools = sync_agent_tools(agent_id)
    return jsonify([t.to_dict() for t in tools])
