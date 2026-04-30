from flask import render_template, redirect, url_for, request, flash, send_file
from flask_login import current_user, login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.models.skill import AgentSkill, Skill
from app.services.promotion_service import (
    _PROMOTIONS_DIR,
    create_promotion_pr,
    generate_promotion_bundle,
    is_promoted_to_template,
)
from app.services.skill_service import (
    assign_skill_to_agent,
    list_skills,
    reload_skill,
    remove_skill_from_agent,
    sync_agent_skills,
    toggle_skill,
)


@dashboard_bp.route("/skills")
@login_required
def skills_overview():
    """Global skill catalog — one entry per slug."""
    skills = Skill.query.order_by(Skill.name).all()
    agents = Agent.query.order_by(Agent.name).all()

    # Map skill_id → list of AgentSkill rows so the template knows who has what
    all_ags = AgentSkill.query.all()
    skill_assignments: dict[int, list] = {}
    for ags in all_ags:
        skill_assignments.setdefault(ags.skill_id, []).append(ags)

    return render_template(
        "dashboard/skills_overview.html",
        skills=skills,
        agents=agents,
        skill_assignments=skill_assignments,
    )


@dashboard_bp.route("/skills/<int:skill_id>/assign", methods=["POST"])
@login_required
def skill_assign(skill_id):
    target_id = request.form.get("target_agent_id", type=int)
    if not target_id:
        flash("Target agent is required.", "danger")
        return redirect(url_for("dashboard.skills_overview"))
    try:
        ags = assign_skill_to_agent(skill_id, target_id)
        skill = db.session.get(Skill, skill_id)
        agent = db.session.get(Agent, ags.agent_id)
        flash(f"Skill '{skill.name}' assigned to agent '{agent.name}'.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("dashboard.skills_overview"))


@dashboard_bp.route("/agents/<int:agent_id>/skills")
@login_required
def skills_list(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))

    skills = list_skills(agent_id=agent_id)
    # Build map of skill_id → AgentSkill for enabled status
    agent_skills_map = {
        ags.skill_id: ags
        for ags in AgentSkill.query.filter_by(agent_id=agent_id).all()
    }
    promoted_slugs = {s.slug for s in skills if is_promoted_to_template("skill", s.slug)}
    return render_template(
        "dashboard/skills_list.html",
        agent=agent,
        skills=skills,
        agent_skills_map=agent_skills_map,
        promoted_slugs=promoted_slugs,
        is_admin=(current_user.role == "admin"),
    )


@dashboard_bp.route("/agents/<int:agent_id>/skills/sync", methods=["POST"])
@login_required
def skills_sync(agent_id):
    skills = sync_agent_skills(agent_id)
    flash(f"Synced {len(skills)} skills from workspace.", "success")
    return redirect(url_for("dashboard.skills_list", agent_id=agent_id))


@dashboard_bp.route("/skills/<int:skill_id>/unassign", methods=["POST"])
@login_required
def skill_unassign(skill_id):
    agent_id = request.form.get("agent_id", type=int)
    if not agent_id:
        flash("agent_id missing.", "danger")
        return redirect(url_for("dashboard.skills_overview"))
    try:
        remove_skill_from_agent(skill_id, agent_id)
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("dashboard.skills_overview"))


@dashboard_bp.route("/skills/<int:skill_id>/toggle", methods=["POST"])
@login_required
def skill_toggle(skill_id):
    agent_id = request.form.get("agent_id", type=int)
    if not agent_id:
        flash("agent_id missing.", "danger")
        return redirect(url_for("dashboard.skills_overview"))
    result = toggle_skill(skill_id, agent_id)
    if result is None:
        flash("Assignment not found.", "danger")
        return redirect(url_for("dashboard.skills_overview"))
    return redirect(url_for("dashboard.skills_list", agent_id=agent_id))


@dashboard_bp.route("/skills/<int:skill_id>/reload", methods=["POST"])
@login_required
def skill_reload(skill_id):
    agent_id = request.form.get("agent_id", type=int)
    skill = reload_skill(skill_id)
    if skill is None:
        flash("Skill not found.", "danger")
        return redirect(url_for("dashboard.overview"))
    flash(f"Skill '{skill.name}' reloaded.", "success")
    if agent_id:
        return redirect(url_for("dashboard.skills_list", agent_id=agent_id))
    return redirect(url_for("dashboard.skills_overview"))


@dashboard_bp.route("/skills/<int:skill_id>/promote-bundle", methods=["POST"])
@login_required
def skill_promote_bundle(skill_id):
    if current_user.role != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("dashboard.skills_overview"))

    skill = db.session.get(Skill, skill_id)
    if skill is None:
        flash("Skill not found.", "danger")
        return redirect(url_for("dashboard.skills_overview"))

    result = generate_promotion_bundle(None, "skill", skill.slug)
    if not result["ok"]:
        flash(f"Bundle error: {result['error']}", "danger")
        return redirect(url_for("dashboard.skills_overview"))

    bundle_name = result["bundle_name"]
    return send_file(
        _PROMOTIONS_DIR / bundle_name,
        as_attachment=True,
        download_name=bundle_name,
    )


@dashboard_bp.route("/skills/<int:skill_id>/promote-pr", methods=["POST"])
@login_required
def skill_promote_pr(skill_id):
    if current_user.role != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("dashboard.skills_overview"))

    skill = db.session.get(Skill, skill_id)
    if skill is None:
        flash("Skill not found.", "danger")
        return redirect(url_for("dashboard.skills_overview"))

    result = create_promotion_pr(None, "skill", skill.slug)
    if result["ok"]:
        flash(f"PR creado: {result['pr_url']}", "success")
    else:
        flash(f"PR error: {result['error']}", "danger")
    return redirect(url_for("dashboard.skills_overview"))
