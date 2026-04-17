"""Slash-command handler for Matrix DMs.

Lets an admin approve/reject pending items (patches, packages) from their
Matrix client instead of switching to the dashboard. The handler is a pure
function over (sender, body, is_dm): it either returns a text reply or None
when the message is not a slash command and should be passed to the agent
runtime as usual.
"""

import logging
import shlex

from app.models.package_installation import PackageInstallation
from app.models.patch_proposal import PatchProposal
from app.models.user import User

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Matrix commands (DM only):\n"
    "  /help — show this message\n"
    "  /pending — list patches and packages awaiting approval\n"
    "  /approve patch <id>\n"
    "  /reject patch <id> [reason]\n"
    "  /approve package <id>\n"
    "  /reject package <id> [reason]\n"
    "Anything that doesn't start with '/' is passed to the agent as usual."
)


def is_command(body: str) -> bool:
    return bool(body) and body.lstrip().startswith("/")


def handle_command(sender: str, body: str, is_dm: bool) -> str:
    """Return the reply text for a slash-command message.

    Callers must only invoke this when ``is_command(body)`` is true.
    """
    if not is_dm:
        return "Approval commands only work in direct messages."

    try:
        tokens = shlex.split(body.strip())
    except ValueError:
        return "Could not parse the command — check your quoting."
    if not tokens:
        return HELP_TEXT

    cmd = tokens[0].lstrip("/").lower()
    args = tokens[1:]

    if cmd in ("help", "?"):
        return HELP_TEXT

    user = User.query.filter_by(matrix_id=sender).first()
    if user is None:
        return (
            f"Your Matrix ID '{sender}' is not linked to a platform admin. "
            "Log into the dashboard, open your profile, and set Matrix ID."
        )

    if cmd == "pending":
        return _render_pending()
    if cmd == "approve":
        return _handle_approve(args, user)
    if cmd == "reject":
        return _handle_reject(args, user)

    return f"Unknown command '/{cmd}'. Try /help."


def _render_pending() -> str:
    patches = (
        PatchProposal.query.filter_by(status="pending_review")
        .order_by(PatchProposal.created_at.asc())
        .limit(20)
        .all()
    )
    packages = (
        PackageInstallation.query.filter_by(status="pending_review")
        .order_by(PackageInstallation.requested_at.asc())
        .limit(20)
        .all()
    )
    if not patches and not packages:
        return "Nothing pending review."

    lines = []
    if patches:
        lines.append("Patches pending:")
        for p in patches:
            lines.append(
                f"  patch {p.id} · agent {p.agent_id} · L{p.security_level} · {p.target_path}"
            )
    if packages:
        if lines:
            lines.append("")
        lines.append("Packages pending:")
        for p in packages:
            lines.append(
                f"  package {p.id} · agent {p.agent_id} · {p.name} ({p.spec})"
            )
    lines.append("")
    lines.append("Use /approve <kind> <id> or /reject <kind> <id> [reason].")
    return "\n".join(lines)


def _handle_approve(args, user) -> str:
    if len(args) < 2:
        return "Usage: /approve patch <id>  or  /approve package <id>"
    kind, raw_id = args[0].lower(), args[1]
    try:
        item_id = int(raw_id)
    except ValueError:
        return f"'{raw_id}' is not a valid id."

    if kind == "patch":
        return _approve_patch(item_id)
    if kind == "package":
        return _approve_package(item_id, user.id)
    return f"Unknown kind '{kind}'. Use 'patch' or 'package'."


def _handle_reject(args, user) -> str:
    if len(args) < 2:
        return "Usage: /reject patch <id> [reason]  or  /reject package <id> [reason]"
    kind, raw_id = args[0].lower(), args[1]
    reason = " ".join(args[2:]).strip() or None
    try:
        item_id = int(raw_id)
    except ValueError:
        return f"'{raw_id}' is not a valid id."

    if kind == "patch":
        return _reject_patch(item_id, reason)
    if kind == "package":
        return _reject_package(item_id, user.id, reason)
    return f"Unknown kind '{kind}'. Use 'patch' or 'package'."


def _approve_patch(patch_id: int) -> str:
    from app.services.patch_service import apply_patch, approve_patch, get_patch

    patch = get_patch(patch_id)
    if patch is None:
        return f"Patch {patch_id} not found."
    if patch.status != "pending_review":
        return f"Patch {patch_id} is '{patch.status}', not pending review."

    approve_patch(patch_id)
    applied, error = apply_patch(patch_id)
    if error:
        return f"Patch {patch_id} approved but apply failed: {error}"
    return f"Patch {patch_id} ('{applied.title}') approved and applied."


def _reject_patch(patch_id: int, reason: str | None) -> str:
    from app.services.patch_service import get_patch, reject_patch

    patch = get_patch(patch_id)
    if patch is None:
        return f"Patch {patch_id} not found."
    rejected = reject_patch(patch_id)
    note = f" — reason: {reason}" if reason else ""
    logger.info("Matrix: patch %s rejected%s", patch_id, note)
    return f"Patch {patch_id} ('{rejected.title}') rejected{note}."


def _approve_package(installation_id: int, user_id: int) -> str:
    from app.services import package_service
    from app.services.package_service import PackageError

    try:
        row = package_service.approve(installation_id, user_id=user_id)
    except PackageError as e:
        return f"Package {installation_id}: {e}"
    if row.status == "installed":
        version = f" ({row.installed_version})" if row.installed_version else ""
        return f"Package {installation_id} '{row.name}'{version} installed."
    return f"Package {installation_id} '{row.name}' install failed: {row.reason or 'unknown'}"


def _reject_package(installation_id: int, user_id: int, reason: str | None) -> str:
    from app.services import package_service
    from app.services.package_service import PackageError

    try:
        row = package_service.reject(installation_id, reason=reason, user_id=user_id)
    except PackageError as e:
        return f"Package {installation_id}: {e}"
    note = f" — reason: {reason}" if reason else ""
    return f"Package {installation_id} '{row.name}' rejected{note}."
