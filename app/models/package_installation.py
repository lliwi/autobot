from datetime import datetime, timezone

from app.extensions import db


class PackageInstallation(db.Model):
    """A Python package the agent wants (or has) installed in its workspace venv.

    Each agent has an isolated venv at ``<workspace>/.venv``. Agents request
    installs via the ``install_package`` tool. Allowlisted specs auto-install;
    the rest wait in ``pending_review`` until the admin approves from the
    dashboard. Status transitions:

      pending_review --approve--> installing --ok--> installed
      pending_review --reject---> rejected
      installing     --fail----> failed

    Uniqueness: one row per ``(agent_id, name)`` so the most recent request
    for a given package is visible. Updating an already-installed package
    reopens the row in ``pending_review`` unless the new spec is allowlisted.
    """

    __tablename__ = "package_installations"
    __table_args__ = (
        db.UniqueConstraint("agent_id", "name", name="uq_package_installations_agent_name"),
    )

    STATUSES = ("pending_review", "approved", "installing", "installed", "failed", "rejected")

    id = db.Column(db.Integer, primary_key=True)
    agent_id = db.Column(db.Integer, db.ForeignKey("agents.id"), nullable=False, index=True)
    # Parsed PyPI package name (lowercased, normalised) — unique per agent.
    name = db.Column(db.String(255), nullable=False, index=True)
    # Full spec as requested by the agent, e.g. "feedparser>=6.0,<7".
    spec = db.Column(db.String(512), nullable=False)
    # Resolved version after install, populated when status == "installed".
    installed_version = db.Column(db.String(128), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="pending_review", index=True)
    reason = db.Column(db.Text, nullable=True)
    stderr_tail = db.Column(db.Text, nullable=True)
    requested_by_run_id = db.Column(db.Integer, db.ForeignKey("runs.id"), nullable=True)
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    requested_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    installed_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(
        db.DateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    agent = db.relationship("Agent", backref="package_installations")
