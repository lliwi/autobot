"""Admin-only endpoints for promoting tools/skills to the default template."""
import os
from pathlib import Path

from flask import jsonify, request, send_file

from app.api import api_bp
from app.api.middleware import admin_required
from app.services.promotion_service import (
    _PROMOTIONS_DIR,
    broadcast_to_all_agents,
    create_promotion_pr,
    generate_promotion_bundle,
    get_promotion_status,
)


def _parse_body(required=("type", "agent_id", "slug")):
    data = request.get_json()
    if not data:
        return None, jsonify(error="JSON body required"), 400

    item_type = data.get("type")
    agent_id = data.get("agent_id")
    slug = data.get("slug")

    if item_type not in ("tool", "skill"):
        return None, jsonify(error="'type' must be 'tool' or 'skill'"), 400
    if not isinstance(agent_id, int):
        return None, jsonify(error="'agent_id' must be an integer"), 400
    if not slug or not isinstance(slug, str):
        return None, jsonify(error="'slug' must be a non-empty string"), 400

    return data, None, None


@api_bp.route("/admin/promote/status", methods=["GET"])
@admin_required
def promote_status():
    """GET /api/admin/promote/status?type=tool&slug=my-tool"""
    item_type = request.args.get("type")
    slug = request.args.get("slug", "")
    if item_type not in ("tool", "skill"):
        return jsonify(error="'type' must be 'tool' or 'skill'"), 400
    return jsonify(get_promotion_status(item_type, slug))


@api_bp.route("/admin/promote/bundle", methods=["POST"])
@admin_required
def promote_bundle():
    """POST /api/admin/promote/bundle — generate a downloadable promotion bundle."""
    data, err_resp, status = _parse_body()
    if err_resp:
        return err_resp, status

    result = generate_promotion_bundle(data["agent_id"], data["type"], data["slug"])
    http_status = 200 if result["ok"] else 400
    return jsonify(result), http_status


@api_bp.route("/admin/promote/bundle/<path:filename>", methods=["GET"])
@admin_required
def promote_bundle_download(filename):
    """GET /api/admin/promote/bundle/<filename> — download a generated bundle."""
    bundle_path = _PROMOTIONS_DIR / filename
    if not bundle_path.exists() or not bundle_path.is_file():
        return jsonify(error="Bundle not found"), 404
    # Safety: ensure it's inside the promotions dir
    try:
        bundle_path.relative_to(_PROMOTIONS_DIR)
    except ValueError:
        return jsonify(error="Invalid path"), 400
    return send_file(bundle_path, as_attachment=True, download_name=filename)


@api_bp.route("/admin/promote/pr", methods=["POST"])
@admin_required
def promote_pr():
    """POST /api/admin/promote/pr — create a GitHub PR for the promotion."""
    data, err_resp, status = _parse_body()
    if err_resp:
        return err_resp, status

    result = create_promotion_pr(data["agent_id"], data["type"], data["slug"])
    http_status = 200 if result["ok"] else 400
    return jsonify(result), http_status


@api_bp.route("/admin/promote/broadcast", methods=["POST"])
@admin_required
def promote_broadcast():
    """POST /api/admin/promote/broadcast — copy item to all existing agents."""
    data, err_resp, status = _parse_body()
    if err_resp:
        return err_resp, status

    result = broadcast_to_all_agents(data["agent_id"], data["type"], data["slug"])
    http_status = 200 if result["ok"] else 400
    return jsonify(result), http_status
