from flask import jsonify, request

from app.api import api_bp
from app.api.middleware import auth_required
from app.extensions import db
from app.models.agent import Agent


@api_bp.route("/agents")
@auth_required
def list_agents():
    agents = Agent.query.order_by(Agent.created_at.desc()).all()
    return jsonify([a.to_dict() for a in agents])


@api_bp.route("/agents", methods=["POST"])
@auth_required
def create_agent():
    data = request.get_json()
    if not data or "name" not in data:
        return jsonify(error="Name required"), 400

    from app.services.agent_service import create_agent as svc_create

    agent = svc_create(data)
    return jsonify(agent.to_dict()), 201


@api_bp.route("/agents/<int:agent_id>")
@auth_required
def get_agent(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return jsonify(error="Agent not found"), 404
    return jsonify(agent.to_dict())


@api_bp.route("/agents/<int:agent_id>", methods=["PATCH"])
@auth_required
def update_agent(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return jsonify(error="Agent not found"), 404

    data = request.get_json()
    for field in ("name", "model_name", "status"):
        if field in data:
            setattr(agent, field, data[field])
    db.session.commit()
    return jsonify(agent.to_dict())


@api_bp.route("/agents/<int:agent_id>/start", methods=["POST"])
@auth_required
def start_agent(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return jsonify(error="Agent not found"), 404
    agent.status = "active"
    db.session.commit()
    return jsonify(agent.to_dict())


@api_bp.route("/agents/<int:agent_id>/stop", methods=["POST"])
@auth_required
def stop_agent(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        return jsonify(error="Agent not found"), 404
    agent.status = "inactive"
    db.session.commit()
    return jsonify(agent.to_dict())
