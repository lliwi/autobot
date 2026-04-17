from pathlib import Path

from flask import current_app

from app.workspace.manager import read_file

WORKSPACE_FILES = ("SOUL.md", "AGENTS.md", "MEMORY.md", "TOOLS.md", "PACKAGES.md")


def load_security_baseline() -> str:
    """Return the platform-wide security baseline injected into every agent.

    Lives at ``<WORKSPACES_BASE_PATH>/SECURITY.md`` — one file shared by all
    agents so a single edit updates the baseline everywhere. Missing file ⇒
    empty string (agents still run, but without the baseline).
    """
    base = Path(current_app.config["WORKSPACES_BASE_PATH"]).resolve()
    path = base / "SECURITY.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_soul(agent):
    return read_file(agent, "SOUL.md")


def load_agents(agent):
    return read_file(agent, "AGENTS.md")


def load_memory(agent):
    return read_file(agent, "MEMORY.md")


def load_tools(agent):
    return read_file(agent, "TOOLS.md")


def load_packages(agent):
    return read_file(agent, "PACKAGES.md")


def load_full_context(agent):
    """Load all workspace context files as a dict."""
    return {name: read_file(agent, name) for name in WORKSPACE_FILES}
