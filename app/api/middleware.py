from functools import wraps

from flask import jsonify
from flask_login import current_user


def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify(error="Unauthorized", status=401), 401
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify(error="Unauthorized", status=401), 401
        if current_user.role != "admin":
            return jsonify(error="Forbidden", status=403), 403
        return f(*args, **kwargs)

    return decorated
