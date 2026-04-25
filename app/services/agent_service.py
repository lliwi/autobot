import re
import shutil
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models.agent import Agent


def _slugify(name):
    slug = re.sub(r"[^\w\s-]", "", name.lower().strip())
    return re.sub(r"[\s_]+", "-", slug)


def create_agent(data):
    name = data["name"]
    slug = data.get("slug") or _slugify(name)
    model_name = data.get("model_name") or current_app.config["OPENAI_MODEL"]

    # Ensure unique slug
    base_slug = slug
    counter = 1
    while Agent.query.filter_by(slug=slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    from app.workspace.manager import scaffold_workspace

    workspace_path = scaffold_workspace(slug)

    agent = Agent(
        name=name,
        slug=slug,
        model_name=model_name,
        workspace_path=workspace_path,
    )
    db.session.add(agent)
    db.session.commit()
    return agent


def update_agent(agent, data):
    if "name" in data and data["name"]:
        agent.name = data["name"]
    if "model_name" in data and data["model_name"]:
        agent.model_name = data["model_name"]
    if "status" in data and data["status"] in ("active", "inactive"):
        agent.status = data["status"]
    if "review_effort" in data and data["review_effort"] != "":
        try:
            effort = int(data["review_effort"])
        except (TypeError, ValueError):
            raise ValueError("review_effort must be an integer between 0 and 10")
        if effort < 0 or effort > 10:
            raise ValueError("review_effort must be between 0 and 10")
        agent.review_effort = effort
    if "review_token_budget_daily" in data:
        raw = data["review_token_budget_daily"]
        if raw in (None, "", "none", "null"):
            agent.review_token_budget_daily = None
        else:
            try:
                budget = int(raw)
            except (TypeError, ValueError):
                raise ValueError("review_token_budget_daily must be a positive integer or empty")
            if budget < 0:
                raise ValueError("review_token_budget_daily must be >= 0")
            agent.review_token_budget_daily = budget or None
    if "forward_matrix_room" in data:
        agent.forward_matrix_room = (data["forward_matrix_room"] or "").strip() or None
    if "sync_matrix_room" in data:
        agent.sync_matrix_room = (data["sync_matrix_room"] or "").strip() or None
    if "matrix_default" in data:
        new_val = bool(data["matrix_default"])
        if new_val and not agent.matrix_default:
            # Clear the flag from any other agent to keep mutual exclusion.
            from app.models.agent import Agent as _Agent
            _Agent.query.filter(
                _Agent.id != agent.id, _Agent.matrix_default.is_(True)
            ).update({"matrix_default": False}, synchronize_session=False)
        agent.matrix_default = new_val
    if "parent_agent_id" in data:
        raw = data["parent_agent_id"]
        old_parent_id = agent.parent_agent_id
        if raw in (None, "", "none", "null"):
            agent.parent_agent_id = None
        else:
            try:
                new_parent_id = int(raw)
            except (TypeError, ValueError):
                raise ValueError("Invalid parent_agent_id")
            if new_parent_id == agent.id:
                raise ValueError("An agent cannot be its own parent")
            if new_parent_id in _descendant_ids(agent):
                raise ValueError("Cannot set a descendant as parent (would create a cycle)")
            if db.session.get(Agent, new_parent_id) is None:
                raise ValueError("Parent agent not found")
            agent.parent_agent_id = new_parent_id
        if agent.parent_agent_id != old_parent_id:
            _sync_agents_md_on_reparent(agent, old_parent_id)
    db.session.commit()
    return agent


def _sync_agents_md_on_reparent(agent, old_parent_id):
    """Keep parent AGENTS.md docs consistent when an agent's parent changes.

    Appends an entry to the new parent's file; the live runtime roster is
    built from the DB, so a missing de-register from the old parent is just
    cosmetic — logged but non-fatal.
    """
    from app.services.subagent_service import _register_in_agents_md

    if agent.parent_agent_id:
        new_parent = db.session.get(Agent, agent.parent_agent_id)
        if new_parent is not None:
            try:
                _register_in_agents_md(new_parent, agent, role="")
            except Exception:
                pass


def _descendant_ids(agent):
    """Return the set of agent IDs that descend from ``agent`` (children, grandchildren...)."""
    ids: set[int] = set()
    stack = list(agent.children or [])
    while stack:
        child = stack.pop()
        if child.id in ids:
            continue
        ids.add(child.id)
        stack.extend(child.children or [])
    return ids


def delete_agent(agent, remove_workspace: bool = False):
    """Delete an agent and all of its dependent rows.

    Refuses if the agent still has child agents — the caller must re-parent or
    delete those first. When ``remove_workspace`` is True the workspace
    directory on disk is removed too.
    """
    if agent.children:
        child_names = ", ".join(c.name for c in agent.children)
        raise ValueError(f"Agent has child agents ({child_names}). Delete or re-parent them first.")

    from app.models.message import Message
    from app.models.patch_proposal import PatchProposal
    from app.models.run import Run
    from app.models.scheduled_task import ScheduledTask
    from app.models.session import Session
    from app.models.skill import Skill
    from app.models.tool import Tool
    from app.models.tool_execution import ToolExecution

    agent_id = agent.id
    workspace_path = agent.workspace_path

    # Messages are linked to sessions, not agents directly — delete via session IDs.
    session_ids = [s.id for s in Session.query.filter_by(agent_id=agent_id).all()]
    if session_ids:
        Message.query.filter(Message.session_id.in_(session_ids)).delete(synchronize_session=False)

    ToolExecution.query.filter_by(agent_id=agent_id).delete(synchronize_session=False)
    Run.query.filter_by(agent_id=agent_id).delete(synchronize_session=False)
    Session.query.filter_by(agent_id=agent_id).delete(synchronize_session=False)
    Tool.query.filter_by(agent_id=agent_id).delete(synchronize_session=False)
    Skill.query.filter_by(agent_id=agent_id).delete(synchronize_session=False)
    ScheduledTask.query.filter_by(agent_id=agent_id).delete(synchronize_session=False)
    PatchProposal.query.filter_by(agent_id=agent_id).delete(synchronize_session=False)

    db.session.delete(agent)
    db.session.commit()

    if remove_workspace and workspace_path:
        path = Path(workspace_path)
        if path.exists() and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
