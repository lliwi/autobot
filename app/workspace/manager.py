import os
import shutil
from pathlib import Path

from flask import current_app


def _base_path():
    return Path(current_app.config["WORKSPACES_BASE_PATH"]).resolve()


def _template_path():
    return Path(__file__).resolve().parent.parent.parent / "workspaces" / "_template"


def scaffold_workspace(slug):
    """Create a new workspace directory from the template.

    Also seeds a ``PACKAGES.md`` placeholder. The per-workspace venv itself is
    created lazily by ``venv_manager.ensure_venv`` on first tool run so the
    scaffold stays fast even if pip is slow/offline.
    """
    workspace = _base_path() / slug
    workspace.mkdir(parents=True, exist_ok=True)

    template = _template_path()
    if template.exists():
        for src in template.iterdir():
            dest = workspace / src.name
            if src.is_file() and not dest.exists():
                shutil.copy2(src, dest)
            elif src.is_dir() and not dest.exists():
                shutil.copytree(src, dest)

    # Create standard subdirectories (no-op if already copied from template)
    for subdir in ("skills", "tools", "agents", "runs", "patches", "tests"):
        (workspace / subdir).mkdir(exist_ok=True)

    # Placeholder so the context builder always sees a PACKAGES.md.
    packages_md = workspace / "PACKAGES.md"
    if not packages_md.exists():
        packages_md.write_text(
            "# Python packages installed in this workspace\n\n"
            "_None yet — use `install_package` to request one._\n",
            encoding="utf-8",
        )

    return str(workspace)


def get_workspace_path(agent):
    return Path(agent.workspace_path).resolve()


def read_file(agent, filename):
    filepath = get_workspace_path(agent) / filename
    if not filepath.exists():
        return ""
    return filepath.read_text(encoding="utf-8")


def write_file(agent, filename, content):
    filepath = get_workspace_path(agent) / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")


def list_files(agent):
    workspace = get_workspace_path(agent)
    if not workspace.exists():
        return []
    return [str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file()]


def refresh_tools_md(agent):
    """Rewrite ``<workspace>/TOOLS.md`` with builtins + workspace tools.

    The file feeds into the system prompt. Keeping it in sync with what the
    agent can actually call prevents the "I don't have that tool" loop when
    a freshly-created workspace tool is already available to the runtime.
    """
    import logging
    from app.runtime.tool_registry import _registry
    from app.workspace.discovery import discover_workspace_tools

    logger = logging.getLogger(__name__)
    try:
        builtins = sorted(_registry.values(), key=lambda t: t.name)
        workspace_tools = discover_workspace_tools(agent)

        lines = ["# Tools", ""]
        lines.append("## Built-in Tools")
        lines.append("")
        for td in builtins:
            lines.append(f"- **{td.name}** — {td.description}")

        lines.append("")
        lines.append("## Workspace Tools")
        lines.append("")
        if not workspace_tools:
            lines.append("_None yet — create one with `create_tool`._")
        else:
            for wt in workspace_tools:
                params = wt.get("parameters", {}).get("properties", {}) or {}
                required = set(wt.get("parameters", {}).get("required", []) or [])
                param_bits = []
                for pname, pspec in params.items():
                    ptype = pspec.get("type", "any")
                    marker = "req" if pname in required else "opt"
                    param_bits.append(f"`{pname}` ({ptype}, {marker})")
                params_line = ", ".join(param_bits) if param_bits else "no arguments"
                lines.append(f"- **{wt['name']}** — {wt.get('description', '').strip()}")
                lines.append(f"  Parameters: {params_line}")

        content = "\n".join(lines) + "\n"
        path = get_workspace_path(agent) / "TOOLS.md"
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to refresh TOOLS.md for agent %s: %s", agent.slug, e)
