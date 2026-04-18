from datetime import datetime, timedelta, timezone

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.models.heartbeat_event import HeartbeatEvent
from app.models.patch_proposal import PatchProposal
from app.models.review_event import ReviewEvent
from app.models.run import Run
from app.models.session import Session as ChatSession
from app.models.tool_execution import ToolExecution
from app.services import codex_auth


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dashboard_bp.route("/")
@login_required
def overview():
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = day_start - timedelta(days=1)
    last_24h = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    # Agent roster
    agents = Agent.query.all()
    agents_total = len(agents)
    active_agents = [a for a in agents if a.status == "active"]
    agents_online = len(active_agents)
    agents_names = ", ".join(a.name for a in active_agents[:4]) or "—"
    if len(active_agents) > 4:
        agents_names += f", +{len(active_agents) - 4}"

    # Sessions
    active_sessions = ChatSession.query.filter_by(status="active").count()
    sessions_last_hour = ChatSession.query.filter(
        ChatSession.created_at >= now - timedelta(hours=1)
    ).count()

    # Tool calls today vs yesterday
    tool_calls_today = ToolExecution.query.filter(
        ToolExecution.started_at >= day_start
    ).count()
    tool_calls_yesterday = ToolExecution.query.filter(
        ToolExecution.started_at >= yesterday_start,
        ToolExecution.started_at < day_start,
    ).count()
    if tool_calls_yesterday > 0:
        tool_calls_delta_pct = round(
            (tool_calls_today - tool_calls_yesterday) / tool_calls_yesterday * 100
        )
    else:
        tool_calls_delta_pct = None

    # Pending patches
    pending_patches = PatchProposal.query.filter_by(status="pending_review").count()

    # Policy blocks (L3 proposals rejected/blocked in last 24h)
    policy_blocks = PatchProposal.query.filter(
        PatchProposal.security_level == 3,
        PatchProposal.created_at >= last_24h,
    ).count()

    # Avg latency — last 24h vs same window a week ago
    avg_ms = db.session.query(func.avg(Run.duration_ms)).filter(
        Run.started_at >= last_24h,
        Run.duration_ms.isnot(None),
    ).scalar()
    prev_avg_ms = db.session.query(func.avg(Run.duration_ms)).filter(
        Run.started_at >= week_ago - timedelta(hours=24),
        Run.started_at < week_ago,
        Run.duration_ms.isnot(None),
    ).scalar()
    avg_latency_s = round((avg_ms or 0) / 1000, 1) if avg_ms else 0
    if prev_avg_ms and avg_ms:
        latency_delta_pct = round((avg_ms - prev_avg_ms) / prev_avg_ms * 100)
    else:
        latency_delta_pct = None

    # Last heartbeat
    last_hb = _aware(db.session.query(func.max(HeartbeatEvent.tick_at)).scalar())
    seconds_ago = int((now - last_hb).total_seconds()) if last_hb else None

    # Activity bars — last 24 hourly buckets
    window_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    execs = ToolExecution.query.filter(
        ToolExecution.started_at >= window_start
    ).with_entities(ToolExecution.started_at, ToolExecution.status).all()
    bins_total = [0] * 24
    bins_err = [0] * 24
    for started_at, status in execs:
        started_at = _aware(started_at)
        idx = int((started_at - window_start).total_seconds() // 3600)
        if 0 <= idx < 24:
            bins_total[idx] += 1
            if status in ("error", "failed"):
                bins_err[idx] += 1
    max_bin = max(bins_total) or 1
    bars = []
    for i in range(24):
        hour_ts = window_start + timedelta(hours=i)
        count = bins_total[i]
        bars.append({
            "hour": hour_ts.hour,
            "count": count,
            "errors": bins_err[i],
            "pct": round(count / max_bin * 100) if max_bin else 0,
            "err_pct": round(bins_err[i] / max_bin * 100) if max_bin else 0,
            "is_now": i == 23,
        })

    # Recent events — mixed timeline
    events = []
    for p in PatchProposal.query.order_by(PatchProposal.created_at.desc()).limit(4).all():
        name = p.agent.name if p.agent else "?"
        events.append({
            "when": _aware(p.created_at),
            "kind": "patch",
            "text": f"{name} proposed patch #{p.id:04d} — {p.title}",
        })
    for te in ToolExecution.query.order_by(ToolExecution.started_at.desc()).limit(6).all():
        agent = db.session.get(Agent, te.agent_id) if te.agent_id else None
        events.append({
            "when": _aware(te.started_at),
            "kind": "tool",
            "text": f"{agent.name if agent else '?'} → {te.tool_name}",
        })
    for rev in ReviewEvent.query.filter(
        ReviewEvent.status.in_(("done", "error"))
    ).order_by(ReviewEvent.processed_at.desc().nullslast()).limit(3).all():
        agent = db.session.get(Agent, rev.agent_id) if rev.agent_id else None
        verdict = (rev.findings_json or {}).get("verdict") if rev.findings_json else None
        text = f"reviewer {verdict.lower()}" if verdict else f"reviewer {rev.status}"
        text += f" {rev.event_type}"
        if agent:
            text += f" on {agent.name}"
        events.append({
            "when": _aware(rev.processed_at or rev.created_at),
            "kind": "review",
            "text": text,
        })
    for hb in HeartbeatEvent.query.filter(
        HeartbeatEvent.decision != "skip"
    ).order_by(HeartbeatEvent.tick_at.desc()).limit(3).all():
        agent = db.session.get(Agent, hb.agent_id) if hb.agent_id else None
        events.append({
            "when": _aware(hb.tick_at),
            "kind": "scheduler",
            "text": f"heartbeat {hb.decision} — {agent.name if agent else '?'}",
        })
    for r in Run.query.filter(Run.status == "error").order_by(Run.started_at.desc()).limit(2).all():
        agent = db.session.get(Agent, r.agent_id) if r.agent_id else None
        events.append({
            "when": _aware(r.started_at),
            "kind": "agent",
            "text": f"{agent.name if agent else '?'} run #{r.id} failed",
        })
    events.sort(key=lambda e: e["when"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    events = events[:8]
    for e in events:
        e["time_str"] = e["when"].strftime("%H:%M:%S") if e["when"] else "—"

    return render_template(
        "dashboard/overview.html",
        # cards
        agents_online=agents_online,
        agents_total=agents_total,
        agents_names=agents_names,
        active_sessions=active_sessions,
        sessions_last_hour=sessions_last_hour,
        tool_calls_today=tool_calls_today,
        tool_calls_delta_pct=tool_calls_delta_pct,
        pending_patches=pending_patches,
        policy_blocks=policy_blocks,
        avg_latency_s=avg_latency_s,
        latency_delta_pct=latency_delta_pct,
        seconds_ago=seconds_ago,
        # activity
        bars=bars,
        # events
        events=events,
        # codex
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
