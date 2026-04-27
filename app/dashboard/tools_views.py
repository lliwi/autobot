from flask import render_template, redirect, url_for, request, flash, send_file
from flask_login import current_user, login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.runtime.tool_registry import _registry, register_builtin_tools
from app.services.promotion_service import (
    _PROMOTIONS_DIR,
    create_promotion_pr,
    generate_promotion_bundle,
    get_promotion_status,
    is_promoted_to_template,
)
from app.services.tool_service import (
    list_tools,
    sync_agent_tools,
    test_tool,
    toggle_tool,
)


@dashboard_bp.route("/tools")
@login_required
def tools_overview():
    """Catalog of every tool available to agents.

    Split in two sections: built-in tools baked into the runtime (available to
    all agents) and workspace tools registered per-agent under ``tools/`` in
    each agent's workspace.
    """
    if not _registry:
        register_builtin_tools()

    builtins = sorted(
        (
            {
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters,
            }
            for td in _registry.values()
        ),
        key=lambda t: t["name"],
    )

    workspace_tools = list_tools()
    groups: dict[str, dict] = {}
    for t in workspace_tools:
        g = groups.setdefault(t.slug, {"slug": t.slug, "name": t.name, "items": []})
        g["items"].append(t)
        if t.name and not g["name"]:
            g["name"] = t.name
    grouped = sorted(groups.values(), key=lambda g: g["name"].lower())

    return render_template(
        "dashboard/tools_overview.html",
        builtins=builtins,
        grouped=grouped,
    )


@dashboard_bp.route("/agents/<int:agent_id>/tools")
@login_required
def tools_list(agent_id):
    agent = db.session.get(Agent, agent_id)
    if agent is None:
        flash("Agent not found.", "danger")
        return redirect(url_for("dashboard.agents_list"))
    tools = list_tools(agent_id=agent_id)
    promoted_slugs = {t.slug for t in tools if is_promoted_to_template("tool", t.slug)}
    return render_template(
        "dashboard/tools_list.html",
        agent=agent,
        tools=tools,
        promoted_slugs=promoted_slugs,
        is_admin=(current_user.role == "admin"),
    )


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


@dashboard_bp.route("/tools/<int:tool_id>/promote-bundle", methods=["POST"])
@login_required
def tool_promote_bundle(tool_id):
    if current_user.role != "admin":
        flash("Admin access required.", "danger")
        from app.models.tool import Tool
        tool = db.session.get(Tool, tool_id)
        return redirect(url_for("dashboard.tools_list", agent_id=tool.agent_id if tool else 0))

    from app.models.tool import Tool
    tool = db.session.get(Tool, tool_id)
    if tool is None:
        flash("Tool not found.", "danger")
        return redirect(url_for("dashboard.overview"))

    result = generate_promotion_bundle(tool.agent_id, "tool", tool.slug)
    if not result["ok"]:
        flash(f"Bundle error: {result['error']}", "danger")
        return redirect(url_for("dashboard.tools_list", agent_id=tool.agent_id))

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
        from app.models.tool import Tool
        tool = db.session.get(Tool, tool_id)
        return redirect(url_for("dashboard.tools_list", agent_id=tool.agent_id if tool else 0))

    from app.models.tool import Tool
    tool = db.session.get(Tool, tool_id)
    if tool is None:
        flash("Tool not found.", "danger")
        return redirect(url_for("dashboard.overview"))

    result = create_promotion_pr(tool.agent_id, "tool", tool.slug)
    if result["ok"]:
        flash(f"PR creado: {result['pr_url']}", "success")
    else:
        flash(f"PR error: {result['error']}", "danger")
    return redirect(url_for("dashboard.tools_list", agent_id=tool.agent_id))
