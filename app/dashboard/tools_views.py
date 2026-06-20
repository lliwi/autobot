import json

from flask import flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.models.tool import AgentTool, Tool
from app.runtime.tool_registry import _registry, register_builtin_tools
from app.services.promotion_service import (
    _PROMOTIONS_DIR,
    create_promotion_pr,
    generate_promotion_bundle,
    is_promoted_to_template,
)
from app.services.tool_service import (
    _version_gt,
    assign_tool_to_agent,
    list_tools,
    reload_tool,
    remove_tool_from_agent,
    sync_agent_tools,
    test_tool,
    toggle_tool,
)
from app.workspace.manager import get_template_path


@dashboard_bp.route("/tools")
@login_required
def tools_overview():
    """Catalog of every tool available to agents.

    Two sections: built-in tools baked into the runtime (available to all
    agents) and the global tool catalog, each assignable per-agent via the
    agent_tools junction.
    """
    if not _registry:
        register_builtin_tools()

    builtins = sorted(
        (
            {"name": td.name, "description": td.description, "parameters": td.parameters}
            for td in _registry.values()
        ),
        key=lambda t: t["name"],
    )

    tools = Tool.query.order_by(Tool.name).all()
    agents = Agent.query.order_by(Agent.name).all()

    # Map tool_id → list of AgentTool rows so the template knows who has what
    tool_assignments: dict[int, list] = {}
    for at in AgentTool.query.all():
        tool_assignments.setdefault(at.tool_id, []).append(at)

    promoted_slugs = {t.slug for t in tools if is_promoted_to_template("tool", t.slug)}

    # Detect when _template/ has a newer version than what's active in _global/
    template_updates: dict[str, str] = {}
    for t in tools:
        if t.slug not in promoted_slugs:
            continue
        tmpl_manifest_path = get_template_path() / "tools" / t.slug / "manifest.json"
        try:
            tmpl = json.loads(tmpl_manifest_path.read_text(encoding="utf-8"))
            tmpl_ver = tmpl.get("version", "")
            if tmpl_ver and _version_gt(tmpl_ver, t.version or "0"):
                template_updates[t.slug] = tmpl_ver
        except Exception:
            pass

    return render_template(
        "dashboard/tools_overview.html",
        builtins=builtins,
        tools=tools,
        agents=agents,
        tool_assignments=tool_assignments,
        promoted_slugs=promoted_slugs,
        template_updates=template_updates,
        is_admin=(current_user.role == "admin"),
    )


@dashboard_bp.route("/tools/<int:tool_id>/assign", methods=["POST"])
@login_required
def tool_assign(tool_id):
    target_id = request.form.get("target_agent_id", type=int)
    if not target_id:
        flash("Target agent is required.", "danger")
        return redirect(url_for("dashboard.tools_overview"))
    try:
        at = assign_tool_to_agent(tool_id, target_id)
        tool = db.session.get(Tool, tool_id)
        agent = db.session.get(Agent, at.agent_id)
        flash(f"Tool '{tool.name}' assigned to agent '{agent.name}'.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("dashboard.tools_overview"))


@dashboard_bp.route("/agents/<int:agent_id>/tools")
@login_required
def tools_list(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))
    tools = list_tools(agent_id=agent_id)
    agent_tools_map = {
        at.tool_id: at
        for at in AgentTool.query.filter_by(agent_id=agent_id).all()
    }
    promoted_slugs = {t.slug for t in tools if is_promoted_to_template("tool", t.slug)}
    return render_template(
        "dashboard/tools_list.html",
        agent=agent,
        tools=tools,
        agent_tools_map=agent_tools_map,
        promoted_slugs=promoted_slugs,
        is_admin=(current_user.role == "admin"),
    )


@dashboard_bp.route("/agents/<int:agent_id>/tools/sync", methods=["POST"])
@login_required
def tools_sync(agent_id):
    tools = sync_agent_tools(agent_id)
    flash(f"Synced {len(tools)} tools from the global catalog.", "success")
    return redirect(url_for("dashboard.tools_list", agent_id=agent_id))


@dashboard_bp.route("/tools/<int:tool_id>/unassign", methods=["POST"])
@login_required
def tool_unassign(tool_id):
    agent_id = request.form.get("agent_id", type=int)
    if not agent_id:
        flash("agent_id missing.", "danger")
        return redirect(url_for("dashboard.tools_overview"))
    try:
        remove_tool_from_agent(tool_id, agent_id)
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("dashboard.tools_overview"))


@dashboard_bp.route("/tools/<int:tool_id>/reload", methods=["POST"])
@login_required
def tool_reload(tool_id):
    tool = reload_tool(tool_id)
    if tool is None:
        flash("Tool not found.", "danger")
        return redirect(url_for("dashboard.tools_overview"))
    flash(f"Tool '{tool.name}' reloaded (v{tool.version}).", "success")
    return redirect(url_for("dashboard.tools_overview"))


@dashboard_bp.route("/tools/<int:tool_id>/toggle", methods=["POST"])
@login_required
def tool_toggle(tool_id):
    agent_id = request.form.get("agent_id", type=int)
    if not agent_id:
        flash("agent_id missing.", "danger")
        return redirect(url_for("dashboard.tools_overview"))
    at = toggle_tool(tool_id, agent_id)
    if at is None:
        flash("Assignment not found.", "danger")
        return redirect(url_for("dashboard.tools_overview"))
    return redirect(url_for("dashboard.tools_list", agent_id=agent_id))


@dashboard_bp.route("/tools/<int:tool_id>/test", methods=["POST"])
@login_required
def tool_test(tool_id):
    agent_id = request.form.get("agent_id", type=int)
    if not agent_id:
        flash("Select an agent to test the tool as.", "danger")
        return redirect(url_for("dashboard.tools_overview"))
    result = test_tool(tool_id, agent_id)
    if result.get("success"):
        flash(f"Tool test result: {result['result']}", "success")
    else:
        flash(f"Tool test error: {result.get('error', 'Unknown')}", "danger")
    return redirect(url_for("dashboard.tools_list", agent_id=agent_id))


@dashboard_bp.route("/tools/<int:tool_id>/promote-bundle", methods=["POST"])
@login_required
def tool_promote_bundle(tool_id):
    if current_user.role != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("dashboard.tools_overview"))

    tool = db.session.get(Tool, tool_id)
    if tool is None:
        flash("Tool not found.", "danger")
        return redirect(url_for("dashboard.tools_overview"))

    result = generate_promotion_bundle(None, "tool", tool.slug)
    if not result["ok"]:
        flash(f"Bundle error: {result['error']}", "danger")
        return redirect(url_for("dashboard.tools_overview"))

    bundle_name = result["bundle_name"]
    return send_file(
        _PROMOTIONS_DIR / bundle_name,
        as_attachment=True,
        download_name=bundle_name,
    )


@dashboard_bp.route("/tools/<int:tool_id>/promote-pr", methods=["POST"])
@login_required
def tool_promote_pr(tool_id):
    if current_user.role != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("dashboard.tools_overview"))

    tool = db.session.get(Tool, tool_id)
    if tool is None:
        flash("Tool not found.", "danger")
        return redirect(url_for("dashboard.tools_overview"))

    result = create_promotion_pr(None, "tool", tool.slug)
    if result["ok"]:
        flash(f"PR creado: {result['pr_url']}", "success")
    else:
        flash(f"PR error: {result['error']}", "danger")
    return redirect(url_for("dashboard.tools_overview"))
