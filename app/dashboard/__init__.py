from flask import Blueprint

dashboard_bp = Blueprint("dashboard", __name__, template_folder="../templates")

from app.dashboard import views, auth_views, chat_views, scheduler_views, metrics_views, skills_views, tools_views, subagent_views, patch_views, credential_views, package_views, review_views  # noqa: E402, F401
