"""Workspace package management — validation, allowlist, review workflow.

Agents request packages with the ``install_package`` runtime tool. This module
decides whether the request can auto-install (allowlist), runs ``pip`` via the
venv manager, and mirrors the state into ``PackageInstallation`` rows plus the
workspace-facing ``PACKAGES.md`` file so the agent sees its inventory in the
system prompt.

Security posture:

  * Specs are validated with a strict regex — no VCS URLs, no local paths,
    no ``--index-url`` injections. Only PyPI distribution names plus standard
    version specifiers and optional extras.
  * Base package name is normalised (lowercase, hyphens) before allowlist
    comparison so ``Requests`` and ``requests`` are treated the same.
  * Anything not allowlisted waits in ``pending_review`` until an admin
    approves from the dashboard. Agents cannot install arbitrary code on a
    whim.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models.agent import Agent
from app.models.package_installation import PackageInstallation
from app.services import venv_manager

logger = logging.getLogger(__name__)


class PackageError(Exception):
    """Raised when a package request is malformed or rejected up-front."""


# name[extras](specifier)?  — the grammar we accept. Deliberately narrow.
# name    : letters, digits, dot, dash, underscore (PEP 503 normalised name)
# extras  : [foo,bar]
# spec    : (==|>=|<=|!=|~=|>|<)\s*version
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
_EXTRAS_RE = re.compile(r"\[([A-Za-z0-9._,\-\s]+)\]")
_VERSION_PART_RE = re.compile(r"(==|>=|<=|!=|~=|>|<)\s*([A-Za-z0-9._+!*-]+)")


def _normalise_name(name: str) -> str:
    """PEP 503 normalisation: lowercase, treat ._- as equivalent."""
    return re.sub(r"[._-]+", "-", name.strip().lower())


def parse_spec(spec: str) -> tuple[str, str]:
    """Parse and sanity-check a PyPI install spec.

    Returns ``(normalised_name, canonical_spec)``. Raises ``PackageError`` on
    anything we don't want to pass to pip.
    """
    if not isinstance(spec, str):
        raise PackageError("spec must be a string")
    s = spec.strip()
    if not s:
        raise PackageError("spec is empty")
    if len(s) > 200:
        raise PackageError("spec too long")

    # Blocklist of patterns that bypass PyPI.
    banned = ["://", " ", "\t", "\n", "git+", "hg+", "svn+", "bzr+", "file:",
              "--", "-e", "-r", ";", "&", "|", "`", "$", "\\", "/"]
    lowered = s.lower()
    for b in banned:
        if b in lowered:
            raise PackageError(f"spec contains forbidden substring: {b!r}")

    m = _NAME_RE.match(s)
    if not m or m.start() != 0:
        raise PackageError("spec must start with a PyPI package name")
    name = m.group(0)
    rest = s[m.end():]

    em = _EXTRAS_RE.match(rest)
    if em:
        rest = rest[em.end():]

    if rest.strip():
        parts = [p.strip() for p in rest.split(",")]
        for part in parts:
            if not part:
                continue
            vm = _VERSION_PART_RE.fullmatch(part)
            if not vm:
                raise PackageError(f"invalid version specifier: {part!r}")

    return _normalise_name(name), s


def _allowlist() -> set[str]:
    raw = current_app.config.get("PACKAGE_ALLOWLIST", "") or ""
    return {_normalise_name(p) for p in raw.split(",") if p.strip()}


def is_allowlisted(name: str) -> bool:
    return _normalise_name(name) in _allowlist()


# -- Core flow --------------------------------------------------------------


def request_install(agent: Agent, spec: str, run_id: int | None = None) -> PackageInstallation:
    """Agent-initiated entry point.

    Validates the spec, creates or updates the ``PackageInstallation`` row,
    and — if allowlisted — installs immediately. Non-allowlisted requests stay
    in ``pending_review`` until an admin approves.
    """
    name, canonical = parse_spec(spec)

    row = PackageInstallation.query.filter_by(agent_id=agent.id, name=name).first()
    if row is None:
        row = PackageInstallation(agent_id=agent.id, name=name, spec=canonical)
        db.session.add(row)
    else:
        row.spec = canonical
        row.reason = None
        row.stderr_tail = None

    row.requested_by_run_id = run_id
    row.status = "approved" if is_allowlisted(name) else "pending_review"
    db.session.commit()

    if row.status == "approved":
        _do_install(row)

    return row


def approve(installation_id: int, user_id: int | None = None) -> PackageInstallation:
    row = db.session.get(PackageInstallation, installation_id)
    if row is None:
        raise PackageError("installation not found")
    if row.status not in ("pending_review", "failed", "rejected"):
        raise PackageError(f"cannot approve from status {row.status!r}")
    row.status = "approved"
    row.approved_by_user_id = user_id
    row.reason = None
    row.stderr_tail = None
    db.session.commit()
    _do_install(row)
    return row


def reject(installation_id: int, reason: str | None = None,
           user_id: int | None = None) -> PackageInstallation:
    row = db.session.get(PackageInstallation, installation_id)
    if row is None:
        raise PackageError("installation not found")
    row.status = "rejected"
    row.reason = (reason or "").strip() or "rejected by admin"
    row.approved_by_user_id = user_id
    db.session.commit()
    return row


def delete_request(installation_id: int) -> PackageInstallation:
    """Delete a failed or rejected installation record without uninstalling anything."""
    row = db.session.get(PackageInstallation, installation_id)
    if row is None:
        raise PackageError("installation not found")
    if row.status not in ("failed", "rejected"):
        raise PackageError("only failed or rejected requests can be deleted")
    db.session.delete(row)
    db.session.commit()
    return row


def uninstall(installation_id: int) -> PackageInstallation:
    row = db.session.get(PackageInstallation, installation_id)
    if row is None:
        raise PackageError("installation not found")
    ok, err = venv_manager.pip_uninstall(row.agent, row.name)
    if not ok:
        row.status = "failed"
        row.stderr_tail = err
        db.session.commit()
        raise PackageError(f"uninstall failed: {err}")
    db.session.delete(row)
    db.session.commit()
    _refresh_packages_md(row.agent)
    return row


# -- Internals --------------------------------------------------------------


def _do_install(row: PackageInstallation) -> None:
    """Run ``pip install`` and update the row in place."""
    row.status = "installing"
    db.session.commit()

    ok, version, stderr = venv_manager.pip_install(row.agent, row.spec)
    if ok:
        row.status = "installed"
        row.installed_version = version
        row.installed_at = datetime.now(timezone.utc)
        row.stderr_tail = None
        row.reason = None
    else:
        row.status = "failed"
        row.stderr_tail = stderr
        row.reason = f"pip install failed: {stderr[:200] if stderr else 'unknown error'}"
    db.session.commit()

    if ok:
        _refresh_packages_md(row.agent)


def _refresh_packages_md(agent: Agent) -> None:
    """Rewrite ``<workspace>/PACKAGES.md`` with the current inventory.

    The file is loaded into the agent's system prompt so it can see what's
    already available before asking for more installs.
    """
    try:
        rows = (
            PackageInstallation.query
            .filter_by(agent_id=agent.id)
            .order_by(PackageInstallation.name)
            .all()
        )

        lines = ["# Python packages installed in this workspace", ""]
        if not rows:
            lines.append("_None yet — use `install_package` to request one._")
        else:
            lines.append("| Package | Version | Status | Spec |")
            lines.append("|---|---|---|---|")
            for r in rows:
                lines.append(
                    f"| `{r.name}` | {r.installed_version or '—'} | {r.status} | `{r.spec}` |"
                )
        content = "\n".join(lines) + "\n"

        path = Path(agent.workspace_path).resolve() / "PACKAGES.md"
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write PACKAGES.md for agent %s: %s", agent.slug, e)


# -- Dashboard helpers ------------------------------------------------------


def list_installations(agent_id: int | None = None,
                        status: str | None = None) -> list[PackageInstallation]:
    query = PackageInstallation.query
    if agent_id is not None:
        query = query.filter(PackageInstallation.agent_id == agent_id)
    if status:
        query = query.filter(PackageInstallation.status == status)
    return query.order_by(
        PackageInstallation.status,
        PackageInstallation.name,
    ).all()


def to_dict(row: PackageInstallation) -> dict:
    return {
        "id": row.id,
        "agent_id": row.agent_id,
        "agent_name": row.agent.name if row.agent else None,
        "agent_slug": row.agent.slug if row.agent else None,
        "name": row.name,
        "spec": row.spec,
        "status": row.status,
        "installed_version": row.installed_version,
        "reason": row.reason,
        "stderr_tail": (row.stderr_tail or "")[-500:] if row.stderr_tail else None,
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "installed_at": row.installed_at.isoformat() if row.installed_at else None,
    }
