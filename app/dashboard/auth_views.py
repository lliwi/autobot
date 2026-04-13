from datetime import datetime, timezone

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.user import User


@dashboard_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.overview"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            user.last_login_at = datetime.now(timezone.utc)
            db.session.commit()
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.overview"))

        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html")


@dashboard_bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("dashboard.login"))
