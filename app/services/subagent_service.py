"""Service layer for sub-agent management and task delegation."""

import json
import logging

from app.extensions import db
from app.models.agent import Agent
from app.models.run import Run
from app.services.agent_service import create_agent
from app.workspace.manager import get_workspace_path, read_file, write_file

logger = logging.getLogger(__name__)


def create_subagent(parent_agent_id, data):
    """Create a sub-agent under a parent agent.

    Scaffolds workspace, sets parent_agent_id, and optionally inherits
    the parent's OAuth profile.
    """
    parent = db.session.get(Agent, parent_agent_id)
    if parent is None:
        raise ValueError("Parent agent not found")

    agent = create_agent(data)
    agent.parent_agent_id = parent.id
    db.session.commit()

    # Register sub-agent in parent's AGENTS.md
    _register_in_agents_md(parent, agent, data.get("role", ""))

    return agent


def list_subagents(parent_agent_id):
    """List direct children of a parent agent."""
    return (
        Agent.query
        .filter_by(parent_agent_id=parent_agent_id)
        .order_by(Agent.name)
        .all()
    )


def get_agent_topology(agent_id=None):
    """Build a tree structure of agents for the topology view.

    If agent_id is given, returns the subtree rooted at that agent.
    Otherwise returns all root agents (no parent) and their subtrees.
    """
    if agent_id:
        root = db.session.get(Agent, agent_id)
        if root is None:
            return None
        return _build_tree_node(root)

    roots = Agent.query.filter_by(parent_agent_id=None).order_by(Agent.name).all()
    return [_build_tree_node(r) for r in roots]


def delegate_task(parent_agent_id, target_agent_id, task_message, parent_run_id=None):
    """Delegate a task to a sub-agent and return the result synchronously.

    Creates a Run linked to the parent run, executes the sub-agent,
    and returns the response.
    """
    parent = db.session.get(Agent, parent_agent_id)
    target = db.session.get(Agent, target_agent_id)

    if parent is None:
        return {"error": "Parent agent not found"}
    if target is None:
        return {"error": "Target agent not found"}

    # Verify target is a child of parent (or allow explicit delegation)
    if target.parent_agent_id != parent.id:
        return {"error": f"Agent '{target.name}' is not a sub-agent of '{parent.name}'"}

    if target.status != "active":
        return {"error": f"Sub-agent '{target.name}' is not active"}

    from app.services.chat_service import run_agent_non_streaming

    result = run_agent_non_streaming(
        agent_id=target.id,
        message=task_message,
        channel_type="internal",
        trigger_type="delegation",
    )

    # Link the child run to the parent run
    if parent_run_id and result.get("run_id"):
        child_run = db.session.get(Run, result["run_id"])
        if child_run:
            child_run.parent_run_id = parent_run_id
            db.session.commit()

    return {
        "agent_id": target.id,
        "agent_name": target.name,
        "response": result.get("response", ""),
        "error": result.get("error"),
        "run_id": result.get("run_id"),
    }


def delegate_task_by_name(parent_agent_id, target_name, task_message, parent_run_id=None):
    """Delegate by sub-agent name or slug instead of ID."""
    target = (
        Agent.query
        .filter_by(parent_agent_id=parent_agent_id)
        .filter((Agent.slug == target_name) | (Agent.name == target_name))
        .first()
    )
    if target is None:
        return {"error": f"Sub-agent '{target_name}' not found"}
    return delegate_task(parent_agent_id, target.id, task_message, parent_run_id)


def _register_in_agents_md(parent, child, role=""):
    """Append a sub-agent entry to the parent's AGENTS.md."""
    agents_md = read_file(parent, "AGENTS.md") or "# Agents\n"
    entry = f"\n## {child.name}\n- **Slug:** {child.slug}\n- **Role:** {role or 'general'}\n- **Status:** {child.status}\n"
    write_file(parent, "AGENTS.md", agents_md + entry)


def _build_tree_node(agent):
    """Recursively build a topology tree node."""
    children = Agent.query.filter_by(parent_agent_id=agent.id).order_by(Agent.name).all()
    node = agent.to_dict()
    node["children"] = [_build_tree_node(c) for c in children]
    return node
