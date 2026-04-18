from datetime import datetime, timezone

from flask import (
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.user import User
from app.services import user_service


_MFA_SESSION_KEY = "pending_mfa_user_id"
_MFA_NEXT_KEY = "pending_mfa_next"


@dashboard_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.overview"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            if user.mfa_enabled:
                session[_MFA_SESSION_KEY] = user.id
                session[_MFA_NEXT_KEY] = request.args.get("next") or ""
                return redirect(url_for("dashboard.mfa_verify"))
            return _complete_login(user, request.args.get("next"))

        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html")


def _complete_login(user: User, next_page: str | None):
    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()
    login_user(user)
    return redirect(next_page or url_for("dashboard.overview"))


@dashboard_bp.route("/login/mfa", methods=["GET", "POST"])
def mfa_verify():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.overview"))

    pending_id = session.get(_MFA_SESSION_KEY)
    if not pending_id:
        return redirect(url_for("dashboard.login"))

    user = db.session.get(User, pending_id)
    if user is None or not user.mfa_enabled:
        session.pop(_MFA_SESSION_KEY, None)
        session.pop(_MFA_NEXT_KEY, None)
        return redirect(url_for("dashboard.login"))

    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        if user_service.verify_mfa_code(user, code):
            next_page = session.pop(_MFA_NEXT_KEY, None) or None
            session.pop(_MFA_SESSION_KEY, None)
            return _complete_login(user, next_page)
        flash("Invalid or expired MFA code.", "danger")

    return render_template("auth/mfa_verify.html", email=user.email)


@dashboard_bp.route("/logout")
def logout():
    logout_user()
    session.pop(_MFA_SESSION_KEY, None)
    session.pop(_MFA_NEXT_KEY, None)
    return redirect(url_for("dashboard.login"))


# --- Profile -------------------------------------------------------------


@dashboard_bp.route("/profile", methods=["GET"])
@login_required
def profile():
    provisioning_uri = ""
    if current_user.mfa_secret and not current_user.mfa_enabled:
        provisioning_uri = user_service.mfa_provisioning_uri(current_user)
    return render_template(
        "dashboard/profile.html",
        mfa_pending_secret=current_user.mfa_secret if not current_user.mfa_enabled else None,
        provisioning_uri=provisioning_uri,
    )


@dashboard_bp.route("/profile/matrix", methods=["POST"])
@login_required
def profile_matrix():
    raw = (request.form.get("matrix_id") or "").strip()
    matrix_id = raw or None
    if matrix_id and not (matrix_id.startswith("@") and ":" in matrix_id):
        flash("Matrix ID must look like '@user:homeserver'.", "danger")
        return redirect(url_for("dashboard.profile"))

    if matrix_id:
        existing = User.query.filter(
            User.matrix_id == matrix_id, User.id != current_user.id
        ).first()
        if existing is not None:
            flash(f"Matrix ID '{matrix_id}' is already linked to another account.", "danger")
            return redirect(url_for("dashboard.profile"))

    current_user.matrix_id = matrix_id
    db.session.commit()
    flash("Matrix link updated.", "success")
    return redirect(url_for("dashboard.profile"))


@dashboard_bp.route("/profile/password", methods=["POST"])
@login_required
def profile_password():
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    if new != confirm:
        flash("New password and confirmation do not match.", "danger")
        return redirect(url_for("dashboard.profile"))
    ok, err = user_service.change_password(current_user, current, new)
    if not ok:
        flash(err, "danger")
    else:
        flash("Password changed.", "success")
    return redirect(url_for("dashboard.profile"))


@dashboard_bp.route("/profile/mfa/start", methods=["POST"])
@login_required
def profile_mfa_start():
    if current_user.mfa_enabled:
        flash("MFA is already enabled. Disable it first to regenerate a secret.", "warning")
        return redirect(url_for("dashboard.profile"))
    user_service.start_mfa_setup(current_user)
    flash("Scan the QR with your authenticator app, then enter a code to activate MFA.", "info")
    return redirect(url_for("dashboard.profile"))


@dashboard_bp.route("/profile/mfa/confirm", methods=["POST"])
@login_required
def profile_mfa_confirm():
    code = (request.form.get("code") or "").strip()
    ok, err = user_service.confirm_mfa_setup(current_user, code)
    if not ok:
        flash(err, "danger")
    else:
        flash("MFA enabled.", "success")
    return redirect(url_for("dashboard.profile"))


@dashboard_bp.route("/profile/mfa/disable", methods=["POST"])
@login_required
def profile_mfa_disable():
    password = request.form.get("password") or ""
    code = (request.form.get("code") or "").strip()
    ok, err = user_service.disable_mfa(current_user, password, code)
    if not ok:
        flash(err, "danger")
    else:
        flash("MFA disabled.", "success")
    return redirect(url_for("dashboard.profile"))


@dashboard_bp.route("/profile/mfa/qr.png")
@login_required
def profile_mfa_qr():
    if not current_user.mfa_secret:
        abort(404)
    png = user_service.mfa_qr_png_bytes(current_user)
    return Response(png, mimetype="image/png")


@dashboard_bp.route("/profile/avatar", methods=["POST"])
@login_required
def profile_avatar_upload():
    file = request.files.get("avatar")
    ok, err = user_service.save_avatar(current_user, file)
    if not ok:
        flash(err, "danger")
    else:
        flash("Avatar updated.", "success")
    return redirect(url_for("dashboard.profile"))


@dashboard_bp.route("/profile/avatar/remove", methods=["POST"])
@login_required
def profile_avatar_remove():
    user_service.remove_avatar(current_user)
    flash("Avatar removed.", "info")
    return redirect(url_for("dashboard.profile"))


@dashboard_bp.route("/avatars/<path:filename>")
@login_required
def serve_avatar(filename):
    path = user_service.avatar_path(filename)
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/png", max_age=0)
