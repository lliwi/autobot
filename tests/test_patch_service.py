"""Integration tests for app.services.patch_service.

Exercises the end-to-end propose → (validate / rate-limit / dedup /
auto-apply / review) pipeline against a real SQLite DB and a real on-disk
workspace. The reviewer sub-agent is absent in the test fixture, so level-2
patches without a standing rule stay in ``pending_review`` — exactly the
behaviour we want to assert.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.extensions import db
from app.models.patch_proposal import PatchProposal
from app.services.approval_rule_service import create_rule
from app.services.patch_service import (
    _apply_unified_diff,
    _compute_diff,
    apply_patch,
    list_patches,
    propose_change,
    reject_patch,
    rollback_patch,
)


# -- Pure helpers (no DB) ---------------------------------------------

def test_compute_diff_no_change_returns_empty():
    assert _compute_diff("f.txt", "hello\n", "hello\n") == ""


def test_compute_diff_adds_header_and_hunks():
    diff = _compute_diff("f.txt", "a\nb\n", "a\nB\n")
    assert diff.startswith("--- a/f.txt")
    assert "+++ b/f.txt" in diff
    assert "-b" in diff
    assert "+B" in diff


def test_apply_unified_diff_roundtrip():
    old = "alpha\nbeta\ngamma\n"
    new = "alpha\nBETA\ngamma\ndelta\n"
    diff = _compute_diff("f.txt", old, new)
    result = "".join(_apply_unified_diff(old.splitlines(keepends=True), diff))
    assert result == new


def test_apply_unified_diff_empty_diff_is_noop():
    lines = ["a\n", "b\n"]
    assert _apply_unified_diff(lines, "") == lines


# -- Service lifecycle -------------------------------------------------

def test_propose_prohibited_is_rejected(app, agent):
    patch = propose_change(
        agent_id=agent.id,
        target_path="SOUL.md",  # protected in MVP
        new_content="evil\n",
        title="bad",
        reason="try to edit identity",
    )
    assert patch.status == "rejected"
    assert patch.security_level == 3
    assert "Prohibited" in (patch.test_result_json or {}).get("error", "")


def test_propose_noop_is_rejected(app, agent):
    # Seed an existing file equal to the proposed content.
    ws = Path(agent.workspace_path)
    (ws / "MEMORY.md").write_text("same\n")
    patch = propose_change(
        agent_id=agent.id,
        target_path="MEMORY.md",
        new_content="same\n",
        title="noop",
        reason="identical content",
    )
    assert patch.status == "rejected"
    assert "No-op" in (patch.test_result_json or {}).get("error", "")


def test_propose_level1_auto_applies(app, agent):
    patch = propose_change(
        agent_id=agent.id,
        target_path="MEMORY.md",
        new_content="learned something new\n",
        title="memory update",
        reason="new fact",
    )
    assert patch.status == "applied"
    assert patch.security_level == 1
    assert (Path(agent.workspace_path) / "MEMORY.md").read_text() == "learned something new\n"


def test_propose_level2_stays_pending_without_rule(app, agent):
    # Seed an existing skill.py so the change is a modification (level 2).
    skill = Path(agent.workspace_path) / "skills" / "foo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "skill.py").write_text("def run():\n    return 1\n")

    patch = propose_change(
        agent_id=agent.id,
        target_path="skills/foo/skill.py",
        new_content="def run():\n    return 2\n",
        title="bump",
        reason="change return value",
    )
    assert patch.status == "pending_review"
    assert patch.security_level == 2


def test_propose_level2_with_matching_rule_auto_applies(app, agent):
    skill = Path(agent.workspace_path) / "skills" / "foo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "skill.py").write_text("def run():\n    return 1\n")
    create_rule(agent_id=agent.id, pattern="skills/*")

    patch = propose_change(
        agent_id=agent.id,
        target_path="skills/foo/skill.py",
        new_content="def run():\n    return 2\n",
        title="auto-approved via rule",
        reason="rule covers skills/*",
    )
    assert patch.status == "applied"
    meta = patch.test_result_json or {}
    assert meta.get("auto_approved_by_rule", {}).get("pattern") == "skills/*"


def test_propose_validator_rejects_broken_python(app, agent):
    # New skill file with syntax error — validator must block before apply.
    patch = propose_change(
        agent_id=agent.id,
        target_path="skills/bad/skill.py",
        new_content="def broken(:\n",
        title="bad skill",
        reason="deliberately broken",
    )
    assert patch.status == "rejected"
    assert "SyntaxError" in (patch.test_result_json or {}).get("error", "")


def test_dedup_returns_existing_pending_for_same_diff(app, agent):
    skill = Path(agent.workspace_path) / "skills" / "foo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "skill.py").write_text("def run():\n    return 1\n")

    first = propose_change(
        agent_id=agent.id,
        target_path="skills/foo/skill.py",
        new_content="def run():\n    return 2\n",
        title="first",
        reason="r",
    )
    second = propose_change(
        agent_id=agent.id,
        target_path="skills/foo/skill.py",
        new_content="def run():\n    return 2\n",
        title="duplicate attempt",
        reason="r2",
    )
    assert first.id == second.id
    # Only one pending row in the DB for that target.
    rows = list_patches(agent_id=agent.id, status="pending_review")
    assert len(rows) == 1


def test_rate_limit_rejects_above_cap(app, agent):
    # Put 3 applied patches in the last hour; set the cap to 3.
    now = datetime.now(timezone.utc)
    for i in range(3):
        db.session.add(PatchProposal(
            agent_id=agent.id,
            title=f"seed {i}",
            reason="seed",
            diff_text="",
            target_path="MEMORY.md",
            target_type="memory",
            security_level=1,
            status="applied",
            created_at=now - timedelta(minutes=5 * i),
        ))
    db.session.commit()
    app.config["PATCHES_PER_HOUR_PER_AGENT"] = 3

    patch = propose_change(
        agent_id=agent.id,
        target_path="MEMORY.md",
        new_content="one more please\n",
        title="over the cap",
        reason="should be rate-limited",
    )
    assert patch.status == "rejected"
    meta = patch.test_result_json or {}
    assert meta.get("rate_limited") is True
    assert "rate limit" in (meta.get("error") or "").lower()


def test_rate_limit_disabled_when_cap_is_zero(app, agent):
    app.config["PATCHES_PER_HOUR_PER_AGENT"] = 0
    # Seed many applied patches — still allowed.
    now = datetime.now(timezone.utc)
    for i in range(20):
        db.session.add(PatchProposal(
            agent_id=agent.id,
            title=f"seed {i}",
            reason="seed",
            diff_text="",
            target_path="MEMORY.md",
            target_type="memory",
            security_level=1,
            status="applied",
            created_at=now - timedelta(minutes=i),
        ))
    db.session.commit()

    patch = propose_change(
        agent_id=agent.id,
        target_path="MEMORY.md",
        new_content="still allowed\n",
        title="cap disabled",
        reason="no limit",
    )
    assert patch.status == "applied"


# -- Apply / reject / rollback ---------------------------------------

def test_apply_approved_patch_writes_file(app, agent):
    skill = Path(agent.workspace_path) / "skills" / "foo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "skill.py").write_text("def run():\n    return 1\n")

    patch = propose_change(
        agent_id=agent.id,
        target_path="skills/foo/skill.py",
        new_content="def run():\n    return 2\n",
        title="manual approve",
        reason="test",
    )
    assert patch.status == "pending_review"

    # Manually approve and apply.
    patch.status = "approved"
    db.session.commit()

    applied, err = apply_patch(patch.id)
    assert err is None
    assert applied.status == "applied"
    assert (skill / "skill.py").read_text() == "def run():\n    return 2\n"


def test_rollback_restores_snapshot(app, agent):
    skill = Path(agent.workspace_path) / "skills" / "foo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "skill.py").write_text("def run():\n    return 1\n")
    create_rule(agent_id=agent.id, pattern="skills/*")  # auto-apply

    patch = propose_change(
        agent_id=agent.id,
        target_path="skills/foo/skill.py",
        new_content="def run():\n    return 2\n",
        title="auto",
        reason="rollback test",
    )
    assert patch.status == "applied"
    assert (skill / "skill.py").read_text().strip().endswith("return 2")

    rolled, err = rollback_patch(patch.id)
    assert err is None
    assert rolled.status == "rolled_back"
    assert (skill / "skill.py").read_text().strip().endswith("return 1")


def test_reject_pending_patch(app, agent):
    skill = Path(agent.workspace_path) / "skills" / "foo"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "skill.py").write_text("def run():\n    return 1\n")

    patch = propose_change(
        agent_id=agent.id,
        target_path="skills/foo/skill.py",
        new_content="def run():\n    return 2\n",
        title="to reject",
        reason="x",
    )
    assert patch.status == "pending_review"
    rejected = reject_patch(patch.id)
    assert rejected.status == "rejected"
    # Workspace should not have changed.
    assert (skill / "skill.py").read_text().strip().endswith("return 1")
