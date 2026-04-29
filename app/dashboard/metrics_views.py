from flask import render_template
from flask_login import login_required

from app.dashboard import dashboard_bp


@dashboard_bp.route("/metrics")
@login_required
def metrics():
    from app.services.codex_quota_service import refresh_quota
    refresh_quota()
    return render_template("dashboard/metrics.html")
