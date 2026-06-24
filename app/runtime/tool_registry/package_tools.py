"""Python package install/list tools for the agent's workspace venv."""
from app.runtime.tool_registry.core import ToolDefinition, register


def register_package_tools():
    register(
        ToolDefinition(
            name="install_package",
            description=(
                "Request a Python package install in this agent's isolated workspace "
                "venv. Packages on the platform allowlist install immediately; anything "
                "else is queued for admin approval. Use this when a skill or tool needs "
                "an import that's not already available (e.g. 'feedparser', 'pandas'). "
                "Returns {status: 'installed'|'pending_review'|'failed', ...}."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "string",
                        "description": (
                            "PyPI install spec, e.g. 'feedparser' or 'pandas>=2,<3'. "
                            "Only PyPI names + version specifiers are accepted — no git URLs, "
                            "paths, or pip flags."
                        ),
                    }
                },
                "required": ["spec"],
            },
            handler=lambda **kwargs: _install_package(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_packages",
            description=(
                "List Python packages this agent has installed (or requested) in its "
                "workspace venv, with their status: installed, pending_review, failed, "
                "rejected."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_packages(**kwargs),
        )
    )


def _install_package(_agent=None, _run_id=None, spec=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not spec:
        return {"error": "Missing required argument 'spec'"}
    from app.services import package_service
    try:
        row = package_service.request_install(_agent, spec, run_id=_run_id)
    except package_service.PackageError as e:
        return {"error": str(e)}

    payload = {
        "name": row.name,
        "spec": row.spec,
        "status": row.status,
        "installed_version": row.installed_version,
    }
    if row.status == "installed":
        payload["message"] = (
            f"Package '{row.name}' {row.installed_version or ''} installed. "
            f"You can import it now."
        ).strip()
    elif row.status == "pending_review":
        payload["message"] = (
            f"'{row.name}' is not on the allowlist — waiting for admin approval "
            f"from the Packages dashboard. Retry list_packages later."
        )
    elif row.status == "failed":
        payload["message"] = row.reason or "install failed"
        payload["stderr_tail"] = (row.stderr_tail or "")[-500:]
    else:
        payload["message"] = f"status: {row.status}"
    return payload


def _list_packages(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    from app.models.package_installation import PackageInstallation
    rows = (
        PackageInstallation.query
        .filter_by(agent_id=_agent.id)
        .order_by(PackageInstallation.status, PackageInstallation.name)
        .all()
    )
    return {
        "packages": [
            {
                "name": r.name,
                "spec": r.spec,
                "status": r.status,
                "version": r.installed_version,
                "reason": r.reason,
            }
            for r in rows
        ]
    }
