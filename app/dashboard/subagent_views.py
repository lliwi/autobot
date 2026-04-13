from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.services.subagent_service import (
    create_subagent,
    get_agent_topology,
    list_subagents,
)


@dashboard_bp.route("/agents/<int:agent_id>/subagents")
@login_required
def subagents_list(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))
    subagents = list_subagents(agent_id)
    return render_template("dashboard/subagents_list.html", agent=agent, subagents=subagents)


@dashboard_bp.route("/agents/<int:agent_id>/subagents/create", methods=["GET", "POST"])
@login_required
def subagent_create(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))

    if request.method == "POST":
        data = {
            "name": request.form["name"],
            "model_name": request.form.get("model_name", ""),
            "role": request.form.get("role", ""),
        }
        try:
            child = create_subagent(agent_id, data)
            flash(f"Sub-agent '{child.name}' created.", "success")
            return redirect(url_for("dashboard.subagents_list", agent_id=agent_id))
        except ValueError as e:
            flash(str(e), "danger")

    return render_template("dashboard/subagent_create.html", agent=agent)


@dashboard_bp.route("/topology")
@login_required
def topology():
    tree = get_agent_topology()
    return render_template("dashboard/topology.html", tree=tree)
