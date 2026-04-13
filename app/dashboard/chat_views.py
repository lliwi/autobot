from flask import render_template
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.models.agent import Agent


@dashboard_bp.route("/chat")
@login_required
def chat():
    agents = Agent.query.filter_by(status="active").order_by(Agent.name).all()
    return render_template("chat/index.html", agents=agents)
