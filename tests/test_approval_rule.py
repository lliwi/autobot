"""Tests for ApprovalRule model + app.services.approval_rule_service."""

import pytest

from app.extensions import db
from app.models.approval_rule import ApprovalRule
from app.services.approval_rule_service import (
    create_rule,
    delete_rule,
    get_rule,
    list_rules,
    matches_rule,
)


# -- Model: pattern matching ----------------------------------------

@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("MEMORY.md", "MEMORY.md", True),
        ("MEMORY.md", "OTHER.md", False),
        ("skills/*", "skills/foo/skill.py", True),
        ("skills/*", "tools/foo/tool.py", False),
        ("tools/http/*", "tools/http/tool.py", True),
        ("tools/http/*", "tools/ftp/tool.py", False),
        # trailing spaces are stripped
        ("  MEMORY.md  ", "MEMORY.md", True),
    ],
)
def test_rule_matches(pattern, path, expected):
    rule = ApprovalRule(pattern=pattern)
    assert rule.matches(path) is expected


# -- Service: CRUD --------------------------------------------------

def test_create_rule_requires_pattern(app, agent):
    with pytest.raises(ValueError):
        create_rule(agent_id=agent.id, pattern="   ")


def test_create_and_get_rule(app, agent):
    rule = create_rule(agent_id=agent.id, pattern="MEMORY.md", note="trust memory edits")
    assert rule.id is not None
    assert rule.pattern == "MEMORY.md"
    assert rule.note == "trust memory edits"

    fetched = get_rule(rule.id)
    assert fetched is not None
    assert fetched.id == rule.id


def test_delete_rule(app, agent):
    rule = create_rule(agent_id=agent.id, pattern="MEMORY.md")
    assert delete_rule(rule.id) is True
    assert get_rule(rule.id) is None
    # Deleting again is a no-op.
    assert delete_rule(rule.id) is False


def test_list_rules_scope_is_agent_plus_global(app, agent):
    # Agent-scoped
    create_rule(agent_id=agent.id, pattern="MEMORY.md")
    # Global (no agent)
    create_rule(agent_id=None, pattern="TOOLS.md")
    # Other agent's rule — should NOT appear for `agent`
    from app.models.agent import Agent
    other = Agent(name="other", slug="other", workspace_path="/tmp/other", model_name="gpt-5.2")
    db.session.add(other)
    db.session.commit()
    create_rule(agent_id=other.id, pattern="OTHER.md")

    visible = list_rules(agent_id=agent.id)
    patterns = {r.pattern for r in visible}
    assert "MEMORY.md" in patterns
    assert "TOOLS.md" in patterns
    assert "OTHER.md" not in patterns


def test_matches_rule_returns_first_hit(app, agent):
    create_rule(agent_id=agent.id, pattern="MEMORY.md")
    create_rule(agent_id=None, pattern="skills/*")

    hit = matches_rule(agent.id, "MEMORY.md")
    assert hit is not None and hit.pattern == "MEMORY.md"

    hit = matches_rule(agent.id, "skills/foo/skill.py")
    assert hit is not None and hit.pattern == "skills/*"

    miss = matches_rule(agent.id, "TOOLS.md")
    assert miss is None
