from flask import render_template, redirect, url_for, request, flash
from flask_login import current_user, login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.services.approval_rule_service import (
    create_rule,
    delete_rule,
    list_rules,
)
from app.services.patch_service import (
    apply_patch,
    approve_patch,
    get_patch,
    list_patches,
    reject_patch,
    rollback_patch,
)


@dashboard_bp.route("/patches")
@login_required
def patches_list():
    agent_id = request.args.get("agent_id", type=int)
    status = request.args.get("status")
    patches = list_patches(agent_id=agent_id, status=status)

    agents = Agent.query.order_by(Agent.name).all()
    return render_template(
        "dashboard/patches_list.html",
        patches=patches,
        agents=agents,
        filter_agent_id=agent_id,
        filter_status=status,
    )


@dashboard_bp.route("/patches/<int:patch_id>")
@login_required
def patch_detail(patch_id):
    patch = get_patch(patch_id)
    if patch is None:
        flash("Patch not found.", "danger")
        return redirect(url_for("dashboard.patches_list"))
    return render_template("dashboard/patch_detail.html", patch=patch)


@dashboard_bp.route("/patches/<int:patch_id>/approve", methods=["POST"])
@login_required
def patch_approve(patch_id):
    patch = approve_patch(patch_id)
    if patch is None:
        flash("Patch not found.", "danger")
        return redirect(url_for("dashboard.patches_list"))

    should_apply = request.form.get("apply") == "1"
    remember = request.form.get("remember") == "1"

    if remember:
        pattern = (request.form.get("rule_pattern") or patch.target_path).strip()
        note = (request.form.get("rule_note") or "").strip() or None
        try:
            create_rule(
                agent_id=patch.agent_id,
                pattern=pattern,
                note=note,
                created_by_user_id=getattr(current_user, "id", None),
            )
            flash(f"Standing approval rule added for '{pattern}'.", "success")
        except ValueError as e:
            flash(f"Could not create rule: {e}", "warning")

    if should_apply and patch.status == "approved":
        patch, error = apply_patch(patch_id)
        if error:
            flash(f"Approved but apply failed: {error}", "danger")
        else:
            flash(f"Patch '{patch.title}' approved and applied.", "success")
    else:
        flash(f"Patch '{patch.title}' approved.", "success")
    return redirect(url_for("dashboard.patch_detail", patch_id=patch_id))


@dashboard_bp.route("/approval-rules")
@login_required
def approval_rules_list():
    agent_id = request.args.get("agent_id", type=int)
    rules = list_rules(agent_id=agent_id)
    agents = Agent.query.order_by(Agent.name).all()
    return render_template(
        "dashboard/approval_rules.html",
        rules=rules,
        agents=agents,
        filter_agent_id=agent_id,
    )


@dashboard_bp.route("/approval-rules", methods=["POST"])
@login_required
def approval_rule_create():
    agent_id = request.form.get("agent_id", type=int) or None
    pattern = (request.form.get("pattern") or "").strip()
    note = (request.form.get("note") or "").strip() or None
    if not pattern:
        flash("Pattern is required.", "danger")
        return redirect(url_for("dashboard.approval_rules_list"))
    try:
        create_rule(
            agent_id=agent_id,
            pattern=pattern,
            note=note,
            created_by_user_id=getattr(current_user, "id", None),
        )
        flash(f"Approval rule added: '{pattern}'.", "success")
    except ValueError as e:
        flash(f"Could not create rule: {e}", "danger")
    return redirect(url_for("dashboard.approval_rules_list"))


@dashboard_bp.route("/approval-rules/<int:rule_id>/delete", methods=["POST"])
@login_required
def approval_rule_delete(rule_id):
    if delete_rule(rule_id):
        flash("Approval rule deleted.", "success")
    else:
        flash("Approval rule not found.", "danger")
    return redirect(url_for("dashboard.approval_rules_list"))


@dashboard_bp.route("/patches/<int:patch_id>/reject", methods=["POST"])
@login_required
def patch_reject(patch_id):
    patch = reject_patch(patch_id)
    if patch is None:
        flash("Patch not found.", "danger")
    else:
        flash(f"Patch '{patch.title}' rejected.", "warning")
    return redirect(url_for("dashboard.patch_detail", patch_id=patch_id))


@dashboard_bp.route("/patches/<int:patch_id>/apply", methods=["POST"])
@login_required
def patch_apply(patch_id):
    patch, error = apply_patch(patch_id)
    if error:
        flash(f"Error applying patch: {error}", "danger")
    else:
        flash(f"Patch '{patch.title}' applied successfully.", "success")
    return redirect(url_for("dashboard.patch_detail", patch_id=patch_id))


@dashboard_bp.route("/patches/<int:patch_id>/rollback", methods=["POST"])
@login_required
def patch_rollback(patch_id):
    patch, error = rollback_patch(patch_id)
    if error:
        flash(f"Error rolling back: {error}", "danger")
    else:
        flash(f"Patch '{patch.title}' rolled back.", "success")
    return redirect(url_for("dashboard.patch_detail", patch_id=patch_id))
