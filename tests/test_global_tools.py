"""Tests for the global tool catalog (Tool + AgentTool junction).

Mirrors the globalized-skills model: tools live in _global/tools/ and are
shared across agents through the agent_tools junction.
"""
import os

import pytest

from app.extensions import db
from app.models.agent import Agent
from app.models.tool import AgentTool, Tool
from app.services import tool_service
from app.workspace import discovery
from app.workspace.manager import get_global_tools_path


@pytest.fixture()
def second_agent(app, workspaces_dir):
    ws = os.path.join(workspaces_dir, "other")
    os.makedirs(ws, exist_ok=True)
    a = Agent(name="other", slug="other", status="active",
              workspace_path=ws, model_name="gpt-5.2")
    db.session.add(a)
    db.session.commit()
    return a


def test_create_tool_is_global_and_auto_assigned(app, agent):
    tool = tool_service.create_tool(agent.id, {
        "name": "weather",
        "description": "Get weather",
        "parameters": {"type": "object", "properties": {}},
    })
    # Tool row has no agent ownership; assignment is via junction.
    assert tool.slug == "weather"
    assert Tool.query.count() == 1
    at = AgentTool.query.filter_by(tool_id=tool.id, agent_id=agent.id).first()
    assert at is not None and at.enabled is True
    # Files written to the global catalog
    assert (get_global_tools_path() / "weather" / "tool.py").exists()
    assert (get_global_tools_path() / "weather" / "manifest.json").exists()


def test_duplicate_slug_rejected(app, agent):
    tool_service.create_tool(agent.id, {"name": "dup", "description": "x"})
    with pytest.raises(ValueError, match="already exists"):
        tool_service.create_tool(agent.id, {"name": "dup", "description": "y"})


@pytest.mark.parametrize("slug", ["foo2", "foo-v2", "bar3", "baz-2"])
def test_versioned_slug_rejected(app, agent, slug):
    with pytest.raises(ValueError, match="versioned name"):
        tool_service.create_tool(agent.id, {"name": slug, "slug": slug, "description": "x"})


def test_assignment_shares_tool_across_agents(app, agent, second_agent):
    tool = tool_service.create_tool(agent.id, {"name": "shared", "description": "x"})

    # second_agent doesn't see it until assigned
    assert tool not in discovery.get_enabled_tools(second_agent)

    tool_service.assign_tool_to_agent(tool.id, second_agent.id)
    enabled = discovery.get_enabled_tools(second_agent)
    assert [t.slug for t in enabled] == ["shared"]

    # Re-assigning is an error
    with pytest.raises(ValueError, match="already has tool"):
        tool_service.assign_tool_to_agent(tool.id, second_agent.id)


def test_toggle_is_per_agent(app, agent, second_agent):
    tool = tool_service.create_tool(agent.id, {"name": "tog", "description": "x"})
    tool_service.assign_tool_to_agent(tool.id, second_agent.id)

    tool_service.toggle_tool(tool.id, agent.id)  # disable for creator only
    assert discovery.get_enabled_tools(agent) == []
    assert [t.slug for t in discovery.get_enabled_tools(second_agent)] == ["tog"]


def test_tool_definitions_include_enabled_global_tool(app, agent):
    tool_service.create_tool(agent.id, {
        "name": "mytool",
        "description": "does things",
        "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
    })
    from app.runtime.tool_registry import register_builtin_tools
    register_builtin_tools()

    defs = discovery.get_agent_tool_definitions(agent)
    names = {d["function"]["name"] for d in defs}
    assert "mytool" in names
    # builtins still present
    assert "get_current_time" in names


def test_remove_assignment_keeps_global_tool(app, agent, second_agent):
    tool = tool_service.create_tool(agent.id, {"name": "keep", "description": "x"})
    tool_service.assign_tool_to_agent(tool.id, second_agent.id)
    tool_service.remove_tool_from_agent(tool.id, second_agent.id)
    assert AgentTool.query.filter_by(tool_id=tool.id, agent_id=second_agent.id).first() is None
    # global tool row survives
    assert db.session.get(Tool, tool.id) is not None


def test_execute_resolves_global_tool(app, agent):
    code = (
        "def handler(_agent=None, **kwargs):\n"
        "    return {'echoed': kwargs.get('msg')}\n"
    )
    tool_service.create_tool(agent.id, {
        "name": "echoer",
        "description": "echo",
        "parameters": {"type": "object", "properties": {"msg": {"type": "string"}}},
        "tool_py": code,
    })
    handler = discovery.load_tool_handler(agent, "echoer")
    assert handler is not None
    assert handler(_agent=agent, msg="hi") == {"echoed": "hi"}
