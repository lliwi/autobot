from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.dashboard import dashboard_bp
from app.models.agent import Agent
from app.services import package_service
from app.services.package_service import PackageError


@dashboard_bp.route("/packages")
@login_required
def packages_list():
    rows = [package_service.to_dict(r) for r in package_service.list_installations()]
    agents = Agent.query.order_by(Agent.name).all()
    pending_count = sum(1 for r in rows if r["status"] == "pending_review")
    return render_template(
        "dashboard/packages_list.html",
        packages=rows,
        agents=agents,
        pending_count=pending_count,
    )


@dashboard_bp.route("/packages/request", methods=["POST"])
@login_required
def package_request():
    agent_id = request.form.get("agent_id", type=int)
    spec = (request.form.get("spec") or "").strip()
    if not agent_id or not spec:
        flash("Select an agent and provide a spec.", "danger")
        return redirect(url_for("dashboard.packages_list"))
    agent = Agent.query.get(agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.packages_list"))
    try:
        row = package_service.request_install(agent, spec)
    except PackageError as e:
        flash(f"Invalid spec: {e}", "danger")
        return redirect(url_for("dashboard.packages_list"))
    flash(
        f"'{row.name}' for {agent.slug}: {row.status}"
        + (f" ({row.installed_version})" if row.installed_version else ""),
        "success" if row.status == "installed" else "info",
    )
    return redirect(url_for("dashboard.packages_list"))


@dashboard_bp.route("/packages/<int:installation_id>/approve", methods=["POST"])
@login_required
def package_approve(installation_id):
    try:
        row = package_service.approve(installation_id, user_id=current_user.id)
    except PackageError as e:
        flash(str(e), "danger")
        return redirect(url_for("dashboard.packages_list"))
    if row.status == "installed":
        flash(f"'{row.name}' installed ({row.installed_version or 'no version'}).", "success")
    else:
        flash(f"'{row.name}' install failed: {row.reason}", "danger")
    return redirect(url_for("dashboard.packages_list"))


@dashboard_bp.route("/packages/<int:installation_id>/reject", methods=["POST"])
@login_required
def package_reject(installation_id):
    reason = (request.form.get("reason") or "").strip() or None
    try:
        row = package_service.reject(installation_id, reason=reason, user_id=current_user.id)
    except PackageError as e:
        flash(str(e), "danger")
        return redirect(url_for("dashboard.packages_list"))
    flash(f"'{row.name}' rejected.", "info")
    return redirect(url_for("dashboard.packages_list"))


@dashboard_bp.route("/packages/<int:installation_id>/uninstall", methods=["POST"])
@login_required
def package_uninstall(installation_id):
    try:
        row = package_service.uninstall(installation_id)
    except PackageError as e:
        flash(str(e), "danger")
        return redirect(url_for("dashboard.packages_list"))
    flash(f"'{row.name}' uninstalled.", "success")
    return redirect(url_for("dashboard.packages_list"))
