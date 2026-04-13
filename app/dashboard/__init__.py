from flask import Blueprint

dashboard_bp = Blueprint("dashboard", __name__, template_folder="../templates")

from app.dashboard import views, auth_views, chat_views  # noqa: E402, F401
