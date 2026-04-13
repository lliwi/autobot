from flask import render_template
from flask_login import login_required

from app.dashboard import dashboard_bp


@dashboard_bp.route("/metrics")
@login_required
def metrics():
    return render_template("dashboard/metrics.html")
