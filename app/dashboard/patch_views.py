from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
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
    else:
        flash(f"Patch '{patch.title}' approved.", "success")
    return redirect(url_for("dashboard.patch_detail", patch_id=patch_id))


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
