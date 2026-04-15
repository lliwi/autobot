from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.models.run import Run
from app.services import codex_auth


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
        codex_logged_in=codex_auth.is_logged_in(),
        codex_account_id=codex_auth.get_account_id(),
        codex_token_path=codex_auth.token_path(),
    )


@dashboard_bp.route("/oauth/codex/logout", methods=["POST"])
@login_required
def oauth_codex_logout():
    if codex_auth.logout():
        flash("Codex token eliminado.", "success")
    else:
        flash("No había token de Codex que eliminar.", "info")
    return redirect(url_for("dashboard.overview"))


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

    return render_template(
        "dashboard/agent_create.html",
        available_models=codex_auth.list_models(),
        codex_logged_in=codex_auth.is_logged_in(),
    )


@dashboard_bp.route("/agents/<int:agent_id>/edit", methods=["GET", "POST"])
@login_required
def agent_edit(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))

    if request.method == "POST":
        from app.services.agent_service import update_agent

        update_agent(agent, {
            "name": request.form.get("name", ""),
            "model_name": request.form.get("model_name", ""),
            "status": request.form.get("status", agent.status),
        })
        flash(f"Agent '{agent.name}' updated.", "success")
        return redirect(url_for("dashboard.agent_detail", agent_id=agent.id))

    available_models = codex_auth.list_models()
    # Make sure the agent's current model is in the list, even if the provider no longer advertises it.
    if agent.model_name and agent.model_name not in available_models:
        available_models = [agent.model_name, *available_models]

    return render_template(
        "dashboard/agent_edit.html",
        agent=agent,
        available_models=available_models,
        codex_logged_in=codex_auth.is_logged_in(),
    )


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
