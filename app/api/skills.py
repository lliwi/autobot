from flask import jsonify, request

from app.api import api_bp
from app.api.middleware import auth_required
from app.services.skill_service import (
    create_skill,
    get_skill,
    list_skills,
    reload_skill,
    sync_agent_skills,
    toggle_skill,
)


@api_bp.route("/skills")
@auth_required
def list_skills_api():
    agent_id = request.args.get("agent_id", type=int)
    skills = list_skills(agent_id=agent_id)
    return jsonify([s.to_dict() for s in skills])


@api_bp.route("/skills", methods=["POST"])
@auth_required
def create_skill_api():
    data = request.get_json()
    if not data or "agent_id" not in data or "name" not in data:
        return jsonify(error="agent_id and name required"), 400
    try:
        skill = create_skill(data["agent_id"], data)
        return jsonify(skill.to_dict()), 201
    except ValueError as e:
        return jsonify(error=str(e)), 400


@api_bp.route("/skills/<int:skill_id>", methods=["PATCH"])
@auth_required
def update_skill_api(skill_id):
    skill = get_skill(skill_id)
    if skill is None:
        return jsonify(error="Skill not found"), 404

    data = request.get_json()
    if not data:
        return jsonify(error="No data provided"), 400

    from app.extensions import db

    for field in ("name", "description", "enabled", "version"):
        if field in data:
            setattr(skill, field, data[field])
    db.session.commit()
    return jsonify(skill.to_dict())


@api_bp.route("/skills/<int:skill_id>/reload", methods=["POST"])
@auth_required
def reload_skill_api(skill_id):
    skill = reload_skill(skill_id)
    if skill is None:
        return jsonify(error="Skill not found"), 404
    return jsonify(skill.to_dict())


@api_bp.route("/skills/<int:skill_id>/toggle", methods=["POST"])
@auth_required
def toggle_skill_api(skill_id):
    skill = toggle_skill(skill_id)
    if skill is None:
        return jsonify(error="Skill not found"), 404
    return jsonify(skill.to_dict())


@api_bp.route("/skills/sync", methods=["POST"])
@auth_required
def sync_skills_api():
    agent_id = request.get_json().get("agent_id") if request.is_json else request.args.get("agent_id", type=int)
    if not agent_id:
        return jsonify(error="agent_id required"), 400
    skills = sync_agent_skills(agent_id)
    return jsonify([s.to_dict() for s in skills])
