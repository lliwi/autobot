from flask import jsonify, request

from app.api import api_bp
from app.api.middleware import auth_required
from app.extensions import db
from app.models.agent import Agent
from app.services.subagent_service import (
    create_subagent,
    delegate_task,
    delegate_task_by_name,
    get_agent_topology,
    list_subagents,
)


@api_bp.route("/agents/<int:agent_id>/subagents")
@auth_required
def list_subagents_api(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return jsonify(error="Agent not found"), 404
    subagents = list_subagents(agent_id)
    return jsonify([a.to_dict() for a in subagents])


@api_bp.route("/agents/<int:agent_id>/subagents", methods=["POST"])
@auth_required
def create_subagent_api(agent_id):
    data = request.get_json()
    if not data or "name" not in data:
        return jsonify(error="name required"), 400
    try:
        agent = create_subagent(agent_id, data)
        return jsonify(agent.to_dict()), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400


@api_bp.route("/agents/<int:agent_id>/delegate", methods=["POST"])
@auth_required
def delegate_task_api(agent_id):
    """Delegate a task to a sub-agent.

    Body: {"target_agent_id": 2, "message": "..."} or {"target_name": "slug", "message": "..."}
    """
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify(error="message required"), 400

    if "target_agent_id" in data:
        result = delegate_task(agent_id, data["target_agent_id"], data["message"])
    elif "target_name" in data:
        result = delegate_task_by_name(agent_id, data["target_name"], data["message"])
    else:
        return jsonify(error="target_agent_id or target_name required"), 400

    if result.get("error"):
        return jsonify(result), 400
    return jsonify(result)


@api_bp.route("/agents/topology")
@auth_required
def agent_topology_api():
    """Get the full agent topology tree."""
    topology = get_agent_topology()
    return jsonify(topology)


@api_bp.route("/agents/<int:agent_id>/topology")
@auth_required
def agent_subtree_api(agent_id):
    """Get the topology subtree rooted at a specific agent."""
    tree = get_agent_topology(agent_id)
    if tree is None:
        return jsonify(error="Agent not found"), 404
    return jsonify(tree)
