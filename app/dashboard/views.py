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

        try:
            update_agent(agent, {
                "name": request.form.get("name", ""),
                "model_name": request.form.get("model_name", ""),
                "status": request.form.get("status", agent.status),
                "parent_agent_id": request.form.get("parent_agent_id", ""),
                "review_effort": request.form.get("review_effort", ""),
                "review_token_budget_daily": request.form.get("review_token_budget_daily", ""),
            })
        except ValueError as e:
            flash(str(e), "danger")
            return redirect(url_for("dashboard.agent_edit", agent_id=agent.id))
        flash(f"Agent '{agent.name}' updated.", "success")
        return redirect(url_for("dashboard.agent_detail", agent_id=agent.id))

    available_models = codex_auth.list_models()
    if agent.model_name and agent.model_name not in available_models:
        available_models = [agent.model_name, *available_models]

    from app.services.agent_service import _descendant_ids

    excluded = {agent.id, *_descendant_ids(agent)}
    parent_candidates = (
        Agent.query.filter(~Agent.id.in_(excluded)).order_by(Agent.name).all()
    )

    from app.services import review_service

    return render_template(
        "dashboard/agent_edit.html",
        agent=agent,
        available_models=available_models,
        codex_logged_in=codex_auth.is_logged_in(),
        parent_candidates=parent_candidates,
        codex_pressure_pct=int(review_service.REVIEW_CODEX_PRESSURE_PERCENT),
    )


@dashboard_bp.route("/agents/<int:agent_id>/delete", methods=["POST"])
@login_required
def agent_delete(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))

    from app.services.agent_service import delete_agent

    name = agent.name
    remove_ws = request.form.get("remove_workspace") == "on"
    try:
        delete_agent(agent, remove_workspace=remove_ws)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("dashboard.agent_detail", agent_id=agent_id))

    msg = f"Agent '{name}' deleted"
    if remove_ws:
        msg += " (workspace removed)"
    flash(msg + ".", "success")
    return redirect(url_for("dashboard.agents_list"))


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

    from app.services import review_service

    review_budget = review_service.review_budget_status(agent)

    return render_template(
        "dashboard/agent_detail.html",
        agent=agent,
        workspace_files=workspace_files,
        recent_runs=recent_runs,
        review_budget=review_budget,
    )


@dashboard_bp.route("/agents/<int:agent_id>/heartbeat")
@login_required
def agent_heartbeat(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))

    from app.models.heartbeat_event import HeartbeatEvent
    from app.services.objective_service import list_objectives
    from app.workspace.manager import read_file

    objectives = list_objectives(agent_id, include_done=False)
    recent_events = (
        HeartbeatEvent.query.filter_by(agent_id=agent_id)
        .order_by(HeartbeatEvent.tick_at.desc())
        .limit(30)
        .all()
    )
    heartbeat_md = read_file(agent, "HEARTBEAT.md") or ""

    return render_template(
        "dashboard/agent_heartbeat.html",
        agent=agent,
        objectives=objectives,
        recent_events=recent_events,
        heartbeat_md=heartbeat_md,
    )


@dashboard_bp.route("/agents/<int:agent_id>/heartbeat/tick", methods=["POST"])
@login_required
def agent_heartbeat_tick(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))

    from app.services import heartbeat_supervisor

    try:
        event = heartbeat_supervisor.tick(agent_id)
        flash(f"Tick: {event.decision} — {event.reason}", "info")
    except Exception as e:
        flash(f"Tick failed: {e}", "danger")
    return redirect(url_for("dashboard.agent_heartbeat", agent_id=agent_id))


@dashboard_bp.route("/agents/<int:agent_id>/objectives/create", methods=["POST"])
@login_required
def objective_create(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Objective title is required.", "danger")
        return redirect(url_for("dashboard.agent_heartbeat", agent_id=agent_id))
    from app.services.objective_service import create_objective
    create_objective(agent_id, title, description=request.form.get("description", "").strip())
    flash("Objective created.", "success")
    return redirect(url_for("dashboard.agent_heartbeat", agent_id=agent_id))


@dashboard_bp.route("/objectives/<int:objective_id>/update", methods=["POST"])
@login_required
def objective_update(objective_id):
    from app.models.objective import Objective
    from app.services.objective_service import update_objective

    obj = db.session.get(Objective, objective_id)
    if obj is None:
        flash("Objective not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))
    update_objective(obj, status=request.form.get("status", obj.status))
    return redirect(url_for("dashboard.agent_heartbeat", agent_id=obj.agent_id))


@dashboard_bp.route("/objectives/<int:objective_id>/delete", methods=["POST"])
@login_required
def objective_delete(objective_id):
    from app.models.objective import Objective
    from app.services.objective_service import delete_objective

    obj = db.session.get(Objective, objective_id)
    if obj is None:
        flash("Objective not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))
    agent_id = obj.agent_id
    delete_objective(obj)
    flash("Objective deleted.", "success")
    return redirect(url_for("dashboard.agent_heartbeat", agent_id=agent_id))


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
