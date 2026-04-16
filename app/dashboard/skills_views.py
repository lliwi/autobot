from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.services.skill_service import (
    list_skills,
    reload_skill,
    share_skill,
    sync_agent_skills,
    toggle_skill,
)


@dashboard_bp.route("/skills")
@login_required
def skills_overview():
    """Catalog of every skill across all agents, groupable by slug (shared copies)."""
    skills = list_skills()
    agents = Agent.query.order_by(Agent.name).all()

    groups: dict[str, dict] = {}
    for s in skills:
        g = groups.setdefault(s.slug, {"slug": s.slug, "name": s.name, "items": []})
        g["items"].append(s)
        if s.name and not g["name"]:
            g["name"] = s.name
    grouped = sorted(groups.values(), key=lambda g: g["name"].lower())

    return render_template(
        "dashboard/skills_overview.html",
        grouped=grouped,
        agents=agents,
    )


@dashboard_bp.route("/skills/<int:skill_id>/share", methods=["POST"])
@login_required
def skill_share(skill_id):
    target_id = request.form.get("target_agent_id", type=int)
    if not target_id:
        flash("Target agent is required.", "danger")
        return redirect(url_for("dashboard.skills_overview"))
    try:
        copy = share_skill(skill_id, target_id)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("dashboard.skills_overview"))
    flash(f"Skill '{copy.name}' copied to agent '{copy.agent.name}'.", "success")
    return redirect(url_for("dashboard.skills_overview"))


@dashboard_bp.route("/agents/<int:agent_id>/skills")
@login_required
def skills_list(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))
    skills = list_skills(agent_id=agent_id)
    return render_template("dashboard/skills_list.html", agent=agent, skills=skills)


@dashboard_bp.route("/agents/<int:agent_id>/skills/sync", methods=["POST"])
@login_required
def skills_sync(agent_id):
    skills = sync_agent_skills(agent_id)
    flash(f"Synced {len(skills)} skills from workspace.", "success")
    return redirect(url_for("dashboard.skills_list", agent_id=agent_id))


@dashboard_bp.route("/skills/<int:skill_id>/toggle", methods=["POST"])
@login_required
def skill_toggle(skill_id):
    skill = toggle_skill(skill_id)
    if skill is None:
        flash("Skill not found.", "danger")
        return redirect(url_for("dashboard.overview"))
    return redirect(url_for("dashboard.skills_list", agent_id=skill.agent_id))


@dashboard_bp.route("/skills/<int:skill_id>/reload", methods=["POST"])
@login_required
def skill_reload(skill_id):
    skill = reload_skill(skill_id)
    if skill is None:
        flash("Skill not found.", "danger")
        return redirect(url_for("dashboard.overview"))
    flash(f"Skill '{skill.name}' reloaded.", "success")
    return redirect(url_for("dashboard.skills_list", agent_id=skill.agent_id))
