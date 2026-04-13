from flask import jsonify, request
from flask_login import current_user, login_user, logout_user

from app.api import api_bp
from app.api.middleware import auth_required
from app.extensions import db
from app.models.user import User


@api_bp.route("/auth/login", methods=["POST"])
def api_login():
    data = request.get_json()
    if not data or "email" not in data or "password" not in data:
        return jsonify(error="Email and password required"), 400

    user = User.query.filter_by(email=data["email"]).first()
    if user is None or not user.check_password(data["password"]):
        return jsonify(error="Invalid credentials"), 401

    from datetime import datetime, timezone

    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()
    login_user(user)

    return jsonify(id=user.id, email=user.email, role=user.role)


@api_bp.route("/auth/logout", methods=["POST"])
@auth_required
def api_logout():
    logout_user()
    return jsonify(message="Logged out")


@api_bp.route("/auth/me")
@auth_required
def api_me():
    return jsonify(id=current_user.id, email=current_user.email, role=current_user.role)
