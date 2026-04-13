from flask import render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.models.run import Run


@dashboard_bp.route("/")
@login_required
def overview():
    agents_count = Agent.query.filter_by(status="active").count()
    total_runs = Run.query.count()
    recent_runs = Run.query.order_by(Run.started_at.desc()).limit(10).all()
    error_runs = Run.query.filter_by(status="error").order_by(Run.started_at.desc()).limit(5).all()
    return render_template(
        "dashboard/overview.html",
        agents_count=agents_count,
        total_runs=total_runs,
        recent_runs=recent_runs,
        error_runs=error_runs,
    )


@dashboard_bp.route("/agents")
@login_required
def agents_list():
    agents = Agent.query.order_by(Agent.created_at.desc()).all()
    return render_template("dashboard/agents_list.html", agents=agents)


@dashboard_bp.route("/agents/create", methods=["GET", "POST"])
@login_required
def agent_create():
    if request.method == "POST":
        from app.services.agent_service import create_agent

        data = {
            "name": request.form["name"],
            "model_name": request.form.get("model_name", ""),
        }
        agent = create_agent(data)
        flash(f"Agent '{agent.name}' created.", "success")
        return redirect(url_for("dashboard.agent_detail", agent_id=agent.id))

    return render_template("dashboard/agent_create.html")


@dashboard_bp.route("/agents/<int:agent_id>")
@login_required
def agent_detail(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))

    from app.workspace.loader import load_full_context

    workspace_files = load_full_context(agent)
    recent_runs = Run.query.filter_by(agent_id=agent_id).order_by(Run.started_at.desc()).limit(10).all()

    return render_template(
        "dashboard/agent_detail.html",
        agent=agent,
        workspace_files=workspace_files,
        recent_runs=recent_runs,
    )


@dashboard_bp.route("/agents/<int:agent_id>/toggle", methods=["POST"])
@login_required
def agent_toggle(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))

    agent.status = "active" if agent.status == "inactive" else "inactive"
    db.session.commit()
    return redirect(url_for("dashboard.agent_detail", agent_id=agent.id))
