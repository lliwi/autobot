"""CRUD + lookup for standing approval rules.

A rule says: "for this agent, auto-apply any level-2 patch whose target_path
matches ``pattern``." Patterns are either exact paths or prefixes with a
trailing ``*``.
"""
import logging

from app.extensions import db
from app.models.approval_rule import ApprovalRule

logger = logging.getLogger(__name__)


def list_rules(agent_id: int | None = None):
    q = ApprovalRule.query
    if agent_id is not None:
        q = q.filter(
            (ApprovalRule.agent_id == agent_id) | (ApprovalRule.agent_id.is_(None))
        )
    return q.order_by(ApprovalRule.created_at.desc()).all()


def get_rule(rule_id: int) -> ApprovalRule | None:
    return db.session.get(ApprovalRule, rule_id)


def create_rule(agent_id: int | None, pattern: str, note: str | None = None,
                created_by_user_id: int | None = None) -> ApprovalRule:
    pattern = pattern.strip()
    if not pattern:
        raise ValueError("pattern is required")
    rule = ApprovalRule(
        agent_id=agent_id,
        pattern=pattern,
        note=note,
        created_by_user_id=created_by_user_id,
    )
    db.session.add(rule)
    db.session.commit()
    logger.info("Created approval rule %s: agent=%s pattern=%r", rule.id, agent_id, pattern)
    return rule


def delete_rule(rule_id: int) -> bool:
    rule = db.session.get(ApprovalRule, rule_id)
    if rule is None:
        return False
    db.session.delete(rule)
    db.session.commit()
    return True


def matches_rule(agent_id: int, target_path: str) -> ApprovalRule | None:
    """Return the first rule (agent-scoped or global) that matches ``target_path``."""
    candidates = (
        ApprovalRule.query
        .filter(
            (ApprovalRule.agent_id == agent_id) | (ApprovalRule.agent_id.is_(None))
        )
        .all()
    )
    for rule in candidates:
        if rule.matches(target_path):
            return rule
    return None
