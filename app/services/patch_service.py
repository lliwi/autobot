"""Service layer for self-improvement patch proposals.

Handles the full lifecycle: propose → snapshot → validate → apply/reject → rollback.
"""

import difflib
import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models.agent import Agent
from app.models.patch_proposal import PatchProposal
from app.services.patch_validator import validate_patch
from app.services.security_policy import (
    can_auto_apply,
    classify_target,
    classify_target_type,
    is_prohibited,
)
from app.workspace.manager import get_workspace_path, read_file, write_file

logger = logging.getLogger(__name__)


def list_patches(agent_id=None, status=None):
    query = PatchProposal.query
    if agent_id:
        query = query.filter_by(agent_id=agent_id)
    if status:
        query = query.filter_by(status=status)
    return query.order_by(PatchProposal.created_at.desc()).all()


def get_patch(patch_id):
    return db.session.get(PatchProposal, patch_id)


def propose_change(agent_id, target_path, new_content, title, reason, run_id=None):
    """Create a patch proposal for a workspace file change.

    Computes the diff, classifies security level, takes a snapshot of the
    current file, and either auto-applies (level 1) or queues for review (level 2).
    Prohibited changes (level 3) are rejected immediately.

    Returns the PatchProposal instance.
    """
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        raise ValueError("Agent not found")

    workspace = get_workspace_path(agent)
    file_path = workspace / target_path
    is_new = not file_path.exists()

    # Security classification
    level = classify_target(target_path, is_new_file=is_new)
    target_type = classify_target_type(target_path)

    if is_prohibited(level):
        patch = PatchProposal(
            agent_id=agent_id,
            run_id=run_id,
            title=title,
            reason=reason,
            diff_text="",
            target_path=target_path,
            target_type=target_type,
            security_level=level,
            status="rejected",
            test_result_json={"error": "Prohibited by security policy (level 3)"},
        )
        db.session.add(patch)
        db.session.commit()
        logger.warning(f"Prohibited patch rejected: {target_path} (agent {agent_id})")
        return patch

    # Rate limit: cap how many patches an agent can produce per rolling hour.
    # Only "real" outcomes count (applied + pending_review). Rejected patches
    # don't consume budget — the validator dropping a broken first attempt
    # shouldn't block the retry.
    rate_err = _rate_limit_check(agent_id)
    if rate_err is not None:
        patch = PatchProposal(
            agent_id=agent_id,
            run_id=run_id,
            title=title,
            reason=reason,
            diff_text="",
            target_path=target_path,
            target_type=target_type,
            security_level=level,
            status="rejected",
            test_result_json={"error": rate_err, "rate_limited": True},
        )
        db.session.add(patch)
        db.session.commit()
        logger.warning("Patch rate-limited for agent %s: %s", agent_id, rate_err)
        return patch

    # Read current content and compute diff
    current_content = read_file(agent, target_path)
    diff = _compute_diff(target_path, current_content, new_content)

    # Noop: proposed content is identical to what's already there.
    if diff == "" and not is_new:
        patch = PatchProposal(
            agent_id=agent_id,
            run_id=run_id,
            title=title,
            reason=reason,
            diff_text="",
            target_path=target_path,
            target_type=target_type,
            security_level=level,
            status="rejected",
            test_result_json={"error": "No-op: proposed content is identical to current file"},
        )
        db.session.add(patch)
        db.session.commit()
        return patch

    # Dedup: if there's already a pending_review patch for the same target with
    # the same diff, return it instead of queueing a duplicate. Prevents the
    # "re-propose identical change" loop we saw in Matrix history.
    existing_pending = (
        PatchProposal.query
        .filter_by(agent_id=agent_id, target_path=target_path, status="pending_review")
        .order_by(PatchProposal.id.desc())
        .all()
    )
    for candidate in existing_pending:
        if (candidate.diff_text or "") == diff:
            logger.info(
                "propose_change dedup: returning existing pending patch %s for %s",
                candidate.id, target_path,
            )
            return candidate

    # Static validation (JSON parseable, Python syntax, tool handler, smoke-import).
    # Run before taking a snapshot or writing anything — a broken patch never
    # gets to touch the workspace, and the agent gets immediate feedback.
    validation = validate_patch(target_path, new_content, workspace_root=workspace)
    if not validation["ok"]:
        patch = PatchProposal(
            agent_id=agent_id,
            run_id=run_id,
            title=title,
            reason=reason,
            diff_text=diff,
            target_path=target_path,
            target_type=target_type,
            security_level=level,
            status="rejected",
            test_result_json={"validation": validation, "error": validation["error"]},
        )
        db.session.add(patch)
        db.session.commit()
        logger.info(
            "Patch rejected by validator: %s (agent %s) — %s",
            target_path, agent_id, validation["error"],
        )
        return patch

    # Take snapshot
    snapshot_path = _take_snapshot(workspace, target_path)

    # Determine initial status
    if can_auto_apply(level):
        status = "approved"
    else:
        status = "pending_review"

    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc)
    patch = PatchProposal(
        agent_id=agent_id,
        run_id=run_id,
        title=title,
        reason=reason,
        diff_text=diff,
        target_path=target_path,
        target_type=target_type,
        security_level=level,
        status=status,
        snapshot_path=snapshot_path,
        test_result_json={"validation": validation},
        created_at=now,
    )
    db.session.add(patch)
    db.session.flush()  # assign patch.id without committing

    from app.services.patch_audit_service import stamp
    stamp(patch)

    db.session.commit()

    # Auto-apply level 1 changes.
    if status == "approved":
        _apply_patch(patch, agent, new_content)
        return patch

    # Level 2: honour standing approval rules the user has set for this
    # agent + target. If a rule matches, auto-apply; otherwise fall through to
    # reviewer sub-agent vote.
    if level == 2:
        from app.services.approval_rule_service import matches_rule

        rule = matches_rule(agent_id, target_path)
        if rule is not None:
            patch.status = "approved"
            meta = dict(patch.test_result_json or {})
            meta["auto_approved_by_rule"] = {
                "rule_id": rule.id,
                "pattern": rule.pattern,
                "note": rule.note,
            }
            patch.test_result_json = meta
            db.session.commit()
            _apply_patch(patch, agent, new_content)
            logger.info(
                "Patch %s auto-approved by standing rule %s: %s",
                patch.id, rule.id, target_path,
            )
            return patch

        # No standing rule — ask the reviewer sub-agent. Its verdict can
        # auto-apply (APPROVE) or leave the patch pending with the reviewer's
        # notes attached (REJECT or no reviewer configured).
        from app.services.review_service import review_patch

        review = review_patch(
            agent=agent,
            target_path=target_path,
            diff_text=diff,
            new_content=new_content,
            reason=reason,
            run_id=run_id,
        )
        if review is not None:
            meta = dict(patch.test_result_json or {})
            meta["reviewer"] = {
                "slug": review.get("reviewer"),
                "approve": review.get("approve"),
                "summary": review.get("summary"),
                "run_id": review.get("run_id"),
                "error": review.get("error"),
            }
            patch.test_result_json = meta
            if review.get("approve"):
                patch.status = "approved"
                db.session.commit()
                _apply_patch(patch, agent, new_content)
                logger.info(
                    "Patch %s auto-approved by reviewer %s: %s",
                    patch.id, review.get("reviewer"), target_path,
                )
            else:
                db.session.commit()
                logger.info(
                    "Patch %s left pending after reviewer %s verdict: %s",
                    patch.id, review.get("reviewer"),
                    (review.get("summary") or "")[:80],
                )

    return patch


def approve_patch(patch_id):
    """Approve a pending patch. Does NOT apply it yet."""
    patch = db.session.get(PatchProposal, patch_id)
    if patch is None:
        return None
    if patch.status != "pending_review":
        return patch
    patch.status = "approved"
    db.session.commit()
    return patch


def reject_patch(patch_id):
    """Reject a pending or approved patch."""
    patch = db.session.get(PatchProposal, patch_id)
    if patch is None:
        return None
    if patch.status not in ("pending_review", "approved"):
        return patch
    patch.status = "rejected"
    db.session.commit()
    return patch


def apply_patch(patch_id):
    """Apply an approved patch to the workspace."""
    patch = db.session.get(PatchProposal, patch_id)
    if patch is None:
        return None, "Patch not found"
    if patch.status != "approved":
        return patch, f"Cannot apply patch in status '{patch.status}'"

    agent = db.session.get(Agent, patch.agent_id)
    if agent is None:
        return patch, "Agent not found"

    # Reconstruct new content from diff
    new_content = _reconstruct_content(patch)
    if new_content is None:
        return patch, "Cannot reconstruct content from diff"

    # Take a fresh snapshot before applying
    workspace = get_workspace_path(agent)
    if not patch.snapshot_path:
        patch.snapshot_path = _take_snapshot(workspace, patch.target_path)

    _apply_patch(patch, agent, new_content)
    if patch.status != "applied":
        err = (patch.test_result_json or {}).get("error") or "validation failed"
        return patch, f"Validation blocked apply: {err}"
    return patch, None


def revalidate_patch(patch_id):
    """Re-run the static validator on a patch's reconstructed content.

    Useful when a patch has been sitting pending for a while and the admin
    wants to confirm it still passes. Does NOT change patch status on failure
    — this is a diagnostic, not a gate. Returns (patch, error, validation).
    """
    patch = db.session.get(PatchProposal, patch_id)
    if patch is None:
        return None, "Patch not found", None
    agent = db.session.get(Agent, patch.agent_id)
    if agent is None:
        return patch, "Agent not found", None

    new_content = _reconstruct_content(patch)
    if new_content is None:
        return patch, "Cannot reconstruct content from diff", None

    workspace = get_workspace_path(agent)
    validation = validate_patch(patch.target_path, new_content, workspace_root=workspace)
    meta = dict(patch.test_result_json or {})
    meta["revalidation"] = validation
    patch.test_result_json = meta
    db.session.commit()
    return patch, None, validation


def rollback_patch(patch_id):
    """Rollback an applied patch using the stored snapshot."""
    patch = db.session.get(PatchProposal, patch_id)
    if patch is None:
        return None, "Patch not found"
    if patch.status != "applied":
        return patch, f"Cannot rollback patch in status '{patch.status}'"

    agent = db.session.get(Agent, patch.agent_id)
    if agent is None:
        return patch, "Agent not found"

    workspace = get_workspace_path(agent)

    if patch.snapshot_path:
        snapshot = Path(patch.snapshot_path)
        if snapshot.exists():
            target = workspace / patch.target_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(snapshot), str(target))
            patch.status = "rolled_back"
            db.session.commit()
            logger.info(f"Rolled back patch {patch_id}: {patch.target_path}")
            return patch, None

        return patch, "Snapshot file not found"

    # If no snapshot and it was a new file, delete it
    target = workspace / patch.target_path
    if target.exists():
        target.unlink()

    patch.status = "rolled_back"
    db.session.commit()
    return patch, None


# -- Internal helpers --


def _rate_limit_check(agent_id: int) -> str | None:
    """Return an error message if the agent has hit its per-hour patch cap.

    Counts patches in the last 60 minutes whose status implies real work
    (applied or pending_review). Rejected/rolled_back patches don't count.
    Returns None when the agent is under the limit or the limit is disabled.
    """
    try:
        cap = int(current_app.config.get("PATCHES_PER_HOUR_PER_AGENT", 0) or 0)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0:
        return None

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    count = (
        PatchProposal.query
        .filter(
            PatchProposal.agent_id == agent_id,
            PatchProposal.created_at >= since,
            PatchProposal.status.in_(("applied", "pending_review", "approved")),
        )
        .count()
    )
    if count >= cap:
        return (
            f"Self-improvement rate limit hit: {count}/{cap} patches in the "
            "last hour. Wait before proposing more changes."
        )
    return None


def _compute_diff(filename, old_content, new_content):
    """Compute a unified diff between old and new content."""
    old_lines = (old_content or "").splitlines(keepends=True)
    new_lines = (new_content or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff)


def _take_snapshot(workspace, target_path):
    """Copy current file to patches/ as a snapshot. Returns snapshot path or None."""
    source = workspace / target_path
    if not source.exists():
        return None

    patches_dir = workspace / "patches"
    patches_dir.mkdir(exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_name = target_path.replace("/", "__").replace("\\", "__")
    snapshot_name = f"{timestamp}_{safe_name}"
    snapshot = patches_dir / snapshot_name

    shutil.copy2(str(source), str(snapshot))
    return str(snapshot)


def _apply_patch(patch, agent, new_content):
    """Write new content to workspace and update patch status.

    Re-runs validation as a safety net: propose-time checks may be stale if
    the patch sat pending for a while, or if an admin is applying a patch
    whose content was reconstructed from a diff against a file that has
    since changed.
    """
    workspace = get_workspace_path(agent)
    validation = validate_patch(patch.target_path, new_content, workspace_root=workspace)
    if not validation["ok"]:
        meta = dict(patch.test_result_json or {})
        meta["validation_at_apply"] = validation
        meta["error"] = validation["error"]
        patch.test_result_json = meta
        patch.status = "rejected"
        db.session.commit()
        logger.warning(
            "Patch %s rejected at apply time by validator: %s",
            patch.id, validation["error"],
        )
        return

    meta = dict(patch.test_result_json or {})
    meta["validation_at_apply"] = validation
    patch.test_result_json = meta
    write_file(agent, patch.target_path, new_content)
    patch.status = "applied"
    patch.applied_at = datetime.now(timezone.utc)
    db.session.commit()
    logger.info(f"Applied patch {patch.id}: {patch.target_path} (level {patch.security_level})")


def _reconstruct_content(patch):
    """Reconstruct new content by applying the unified diff to the current file."""
    agent = db.session.get(Agent, patch.agent_id)
    if agent is None:
        return None

    current = read_file(agent, patch.target_path)
    current_lines = (current or "").splitlines(keepends=True)

    # Parse the unified diff and apply
    try:
        new_lines = _apply_unified_diff(current_lines, patch.diff_text)
        return "".join(new_lines)
    except Exception as e:
        logger.error(f"Failed to apply diff for patch {patch.id}: {e}")
        return None


def _apply_unified_diff(original_lines, diff_text):
    """Apply a unified diff to a list of lines. Returns new lines."""
    if not diff_text:
        return original_lines

    result = []
    orig_idx = 0
    diff_lines = diff_text.splitlines(keepends=True)
    i = 0

    while i < len(diff_lines):
        line = diff_lines[i]
        if line.startswith("@@"):
            # Parse hunk header: @@ -start,count +start,count @@
            import re
            match = re.match(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", line)
            if not match:
                i += 1
                continue

            hunk_start = int(match.group(1)) - 1  # 0-indexed

            # Copy lines before this hunk
            while orig_idx < hunk_start and orig_idx < len(original_lines):
                result.append(original_lines[orig_idx])
                orig_idx += 1

            i += 1
            # Process hunk lines
            while i < len(diff_lines) and not diff_lines[i].startswith("@@") and not diff_lines[i].startswith("diff ") and not diff_lines[i].startswith("---") and not diff_lines[i].startswith("+++"):
                dline = diff_lines[i]
                if dline.startswith("-"):
                    orig_idx += 1  # skip removed line
                elif dline.startswith("+"):
                    result.append(dline[1:])  # add new line
                elif dline.startswith(" "):
                    result.append(dline[1:])  # context line
                    orig_idx += 1
                else:
                    # No-newline marker or other
                    pass
                i += 1
        else:
            i += 1

    # Copy remaining original lines
    while orig_idx < len(original_lines):
        result.append(original_lines[orig_idx])
        orig_idx += 1

    return result
