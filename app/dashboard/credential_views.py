from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.dashboard import dashboard_bp
from app.models.agent import Agent
from app.services.credential_service import (
    CredentialError,
    delete_credential,
    get_credential,
    list_credentials,
    reveal_credential,
    set_credential,
    to_dict,
)


@dashboard_bp.route("/credentials")
@login_required
def credentials_list():
    try:
        rows = [to_dict(r) for r in list_credentials()]
        error = None
    except CredentialError as e:
        rows = []
        error = str(e)
    agents = Agent.query.order_by(Agent.name).all()
    return render_template(
        "dashboard/credentials_list.html",
        credentials=rows,
        agents=agents,
        error=error,
    )


@dashboard_bp.route("/credentials", methods=["POST"])
@login_required
def credential_create():
    name = (request.form.get("name") or "").strip()
    value = request.form.get("value") or ""
    description = (request.form.get("description") or "").strip() or None
    credential_type = (request.form.get("credential_type") or "token").strip()
    username = (request.form.get("username") or "").strip() or None
    agent_id = request.form.get("agent_id", type=int)
    # "0" / "" from the select means global
    agent_id = agent_id if agent_id else None

    try:
        row = set_credential(
            name=name,
            value=value,
            description=description,
            credential_type=credential_type,
            username=username,
            agent_id=agent_id,
            created_by_user_id=current_user.id,
        )
    except CredentialError as e:
        flash(str(e), "danger")
        return redirect(url_for("dashboard.credentials_list"))

    scope = f"agent '{row.agent.name}'" if row.agent_id else "global"
    flash(f"Credential '{row.name}' saved ({scope}).", "success")
    return redirect(url_for("dashboard.credentials_list"))


@dashboard_bp.route("/credentials/<int:credential_id>/delete", methods=["POST"])
@login_required
def credential_delete(credential_id):
    row = get_credential(credential_id)
    name = row.name if row else None
    if not delete_credential(credential_id):
        flash("Credential not found.", "danger")
    else:
        flash(f"Credential '{name}' deleted.", "success")
    return redirect(url_for("dashboard.credentials_list"))


@dashboard_bp.route("/credentials/<int:credential_id>/reveal")
@login_required
def credential_reveal(credential_id):
    """Return the decrypted value as JSON. Called by the UI's reveal button."""
    try:
        value = reveal_credential(credential_id)
    except CredentialError as e:
        return jsonify({"error": str(e)}), 500
    if value is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"value": value})
