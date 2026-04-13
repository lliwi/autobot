from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.services.tool_service import (
    list_tools,
    sync_agent_tools,
    test_tool,
    toggle_tool,
)


@dashboard_bp.route("/agents/<int:agent_id>/tools")
@login_required
def tools_list(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))
    tools = list_tools(agent_id=agent_id)
    return render_template("dashboard/tools_list.html", agent=agent, tools=tools)


@dashboard_bp.route("/agents/<int:agent_id>/tools/sync", methods=["POST"])
@login_required
def tools_sync(agent_id):
    tools = sync_agent_tools(agent_id)
    flash(f"Synced {len(tools)} tools from workspace.", "success")
    return redirect(url_for("dashboard.tools_list", agent_id=agent_id))


@dashboard_bp.route("/tools/<int:tool_id>/toggle", methods=["POST"])
@login_required
def tool_toggle(tool_id):
    tool = toggle_tool(tool_id)
    if tool is None:
        flash("Tool not found.", "danger")
        return redirect(url_for("dashboard.overview"))
    return redirect(url_for("dashboard.tools_list", agent_id=tool.agent_id))


@dashboard_bp.route("/tools/<int:tool_id>/test", methods=["POST"])
@login_required
def tool_test(tool_id):
    result = test_tool(tool_id)
    if result.get("success"):
        flash(f"Tool test result: {result['result']}", "success")
    else:
        flash(f"Tool test error: {result.get('error', 'Unknown')}", "danger")

    from app.models.tool import Tool
    tool = db.session.get(Tool, tool_id)
    if tool:
        return redirect(url_for("dashboard.tools_list", agent_id=tool.agent_id))
    return redirect(url_for("dashboard.overview"))
