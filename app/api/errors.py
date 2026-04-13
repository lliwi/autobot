from flask import jsonify

from app.api import api_bp


@api_bp.errorhandler(400)
def bad_request(e):
    return jsonify(error=str(e), status=400), 400


@api_bp.errorhandler(401)
def unauthorized(e):
    return jsonify(error="Unauthorized", status=401), 401


@api_bp.errorhandler(403)
def forbidden(e):
    return jsonify(error="Forbidden", status=403), 403


@api_bp.errorhandler(404)
def not_found(e):
    return jsonify(error="Not found", status=404), 404


@api_bp.errorhandler(422)
def unprocessable(e):
    return jsonify(error=str(e), status=422), 422


@api_bp.errorhandler(500)
def internal_error(e):
    return jsonify(error="Internal server error", status=500), 500
