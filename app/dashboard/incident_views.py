from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.services import incident_service


@dashboard_bp.route("/incidents")
@login_required
def incidents_list():
    status = request.args.get("status") or None
    if status == "pending":
        incidents = incident_service.pending()
    else:
        incidents = incident_service.recent(limit=100, status=status)
    return render_template(
        "dashboard/incidents_list.html",
        incidents=incidents,
        filter_status=status,
        pending_count=len(incident_service.pending()),
    )


@dashboard_bp.route("/incidents/<int:incident_id>")
@login_required
def incident_detail(incident_id):
    incident = incident_service.get(incident_id)
    if incident is None:
        flash("Incident not found.", "danger")
        return redirect(url_for("dashboard.incidents_list"))
    return render_template("dashboard/incident_detail.html", incident=incident)


@dashboard_bp.route("/incidents/<int:incident_id>/approve", methods=["POST"])
@login_required
def incident_approve(incident_id):
    incident, error = incident_service.approve(incident_id)
    if incident is None:
        flash("Incident not found.", "danger")
        return redirect(url_for("dashboard.incidents_list"))
    if error:
        flash(f"Could not open on GitHub: {error}", "danger")
    elif incident.github_url:
        flash(f"Opened {incident.proposed_action.upper()}: {incident.github_url}", "success")
    else:
        flash("Incident resolved.", "success")
    return redirect(url_for("dashboard.incident_detail", incident_id=incident_id))


@dashboard_bp.route("/incidents/<int:incident_id>/dismiss", methods=["POST"])
@login_required
def incident_dismiss(incident_id):
    note = (request.form.get("note") or "").strip() or None
    incident = incident_service.dismiss(incident_id, note=note)
    if incident is None:
        flash("Incident not found.", "danger")
    else:
        flash("Incident dismissed.", "success")
    return redirect(url_for("dashboard.incidents_list"))
