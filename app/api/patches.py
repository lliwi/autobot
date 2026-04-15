from flask import jsonify, request

from app.api import api_bp
from app.api.middleware import auth_required
from app.services.patch_service import (
    apply_patch,
    approve_patch,
    get_patch,
    list_patches,
    reject_patch,
    rollback_patch,
)


@api_bp.route("/patches")
@auth_required
def list_patches_api():
    agent_id = request.args.get("agent_id", type=int)
    status = request.args.get("status")
    patches = list_patches(agent_id=agent_id, status=status)
    return jsonify([p.to_dict() for p in patches])


@api_bp.route("/patches/<int:patch_id>")
@auth_required
def get_patch_api(patch_id):
    patch = get_patch(patch_id)
    if patch is None:
        return jsonify(error="Patch not found"), 404
    return jsonify(patch.to_dict())


@api_bp.route("/patches/<int:patch_id>/approve", methods=["POST"])
@auth_required
def approve_patch_api(patch_id):
    patch = approve_patch(patch_id)
    if patch is None:
        return jsonify(error="Patch not found"), 404
    return jsonify(patch.to_dict())


@api_bp.route("/patches/<int:patch_id>/reject", methods=["POST"])
@auth_required
def reject_patch_api(patch_id):
    patch = reject_patch(patch_id)
    if patch is None:
        return jsonify(error="Patch not found"), 404
    return jsonify(patch.to_dict())


@api_bp.route("/patches/<int:patch_id>/apply", methods=["POST"])
@auth_required
def apply_patch_api(patch_id):
    patch, error = apply_patch(patch_id)
    if patch is None:
        return jsonify(error=error or "Patch not found"), 404
    if error:
        return jsonify(error=error, patch=patch.to_dict()), 400
    return jsonify(patch.to_dict())


@api_bp.route("/patches/<int:patch_id>/rollback", methods=["POST"])
@auth_required
def rollback_patch_api(patch_id):
    patch, error = rollback_patch(patch_id)
    if patch is None:
        return jsonify(error=error or "Patch not found"), 404
    if error:
        return jsonify(error=error, patch=patch.to_dict()), 400
    return jsonify(patch.to_dict())
